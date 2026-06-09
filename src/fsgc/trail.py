"""
Trail store — beaver-backed cache of past-scan results.

Replaces the previous on-disk ``.gctrail`` files scattered through every
scanned directory. Every record now lives in a single ``BeaverDB`` file at
``~/.cache/fsgc/trails.db`` (override via ``TRAIL_DB_PATH``), keyed by the
absolute path of the directory it describes.

A trail record carries enough state to skip the next walk entirely when the
directory's structural fingerprint (mtime + entry-count) is unchanged:

    * ``scanned_at`` — unix timestamp; used for TTL pruning.
    * ``fingerprint`` — hash of ``(mtime, entry_count)``; the gate for the
      "trust the cache, don't walk" short-circuit.
    * ``total_size`` / ``entry_count`` / ``atime`` — restored onto the
      DirectoryNode on cache hit so MCTS sees the same metrics it would
      have computed.
    * ``file_evidence`` — sentinel matches at scan time; restored so the
      engine's sentinel verification still passes on cache hit.
    * ``top_children`` — list of ``(name, score, size)`` for the children
      that had the highest ``recovery_cap × size`` last time. Drives the
      scanner's tier-2 selection ("explore where the trash was").
"""

import hashlib
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from beaver import BeaverDB

DEFAULT_DB_PATH: Path = Path.home() / ".cache" / "fsgc" / "trails.db"
DEFAULT_TTL_SECONDS: int = 30 * 24 * 60 * 60  # 30 days


def calculate_fingerprint(mtime: float, nlink: int) -> int:
    """
    Stable 64-bit fingerprint of a directory's structural state.

    Computed from the directory's own ``st_mtime`` (changes when entries are
    added/removed) and ``st_nlink`` (changes when subdirectories are added or
    removed — defense in depth against mtime clock skew). Both come from a
    single ``os.stat`` call, so checking the fingerprint costs one syscall
    versus a full ``os.scandir`` of the directory.
    """
    data = struct.pack("!d Q", mtime, nlink)
    return int(struct.unpack("!Q", hashlib.blake2b(data, digest_size=8).digest())[0])


@dataclass
class TopChild:
    name: str
    score: float  # signature score (0..1) from last scan
    size: int


@dataclass
class TrailRecord:
    scanned_at: float
    fingerprint: int
    total_size: int
    entry_count: int
    atime: float
    mtime: float
    file_evidence: list[str]
    top_children: list[TopChild]
    behavioral_matches: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_at": self.scanned_at,
            "fingerprint": self.fingerprint,
            "total_size": self.total_size,
            "entry_count": self.entry_count,
            "atime": self.atime,
            "mtime": self.mtime,
            "file_evidence": self.file_evidence,
            "top_children": [
                {"name": c.name, "score": c.score, "size": c.size} for c in self.top_children
            ],
            "behavioral_matches": self.behavioral_matches,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrailRecord":
        return cls(
            scanned_at=float(data["scanned_at"]),
            fingerprint=int(data["fingerprint"]),
            total_size=int(data["total_size"]),
            entry_count=int(data["entry_count"]),
            atime=float(data["atime"]),
            mtime=float(data["mtime"]),
            file_evidence=list(data.get("file_evidence", [])),
            top_children=[
                TopChild(name=c["name"], score=float(c["score"]), size=int(c["size"]))
                for c in data.get("top_children", [])
            ],
            behavioral_matches=list(data.get("behavioral_matches", [])),
        )


class TrailStore:
    """
    In-memory cache backed by a beaver-db dictionary.

    Beaver's sync facade marshals every call onto a single background
    "Reactor" thread, which means concurrent ``get()`` / ``set()`` from
    multiple scanner workers serialize through one event loop and trigger
    SQLite lock contention. To dodge that without losing beaver as the
    on-disk store, the trail records live entirely in memory during the
    scan; beaver is only touched in two places:

      * ``__init__``: bulk-load every persisted record via ``items()`` in
        one round-trip.
      * ``close()``: bulk-write every dirtied record back the same way.

    Mid-scan ``get`` / ``put`` calls operate on the in-memory dict — O(1),
    no I/O, no contention. The 30-day TTL is applied at write time.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._db = BeaverDB(str(self.db_path))
        self._trails = self._db.dict("trails")
        self._memory: dict[str, TrailRecord] = {}
        self._dirty: set[str] = set()
        self._closed = False
        self._load()

    def _load(self) -> None:
        """One-shot bulk load of every persisted record into RAM."""
        try:
            for key, value in self._trails.items():
                try:
                    self._memory[key] = TrailRecord.from_dict(value)
                except (KeyError, ValueError, TypeError):
                    continue
        except OSError:
            # If the initial dump fails (e.g. lock contention from another
            # process), start with an empty cache — we'll re-populate as we go.
            pass

    def get(self, path: Path) -> TrailRecord | None:
        return self._memory.get(str(path.resolve()))

    def put(self, path: Path, record: TrailRecord) -> None:
        key = str(path.resolve())
        self._memory[key] = record
        self._dirty.add(key)

    def clear(self) -> None:
        """Drop every cached record from memory AND persistence."""
        self._memory.clear()
        self._dirty.clear()
        for key in list(self._trails.keys()):
            try:
                del self._trails[key]
            except OSError:
                pass

    def keys(self) -> Iterator[str]:
        return iter(self._memory.keys())

    def flush(self) -> None:
        """Persist every dirty record back to beaver in one pass."""
        if not self._dirty:
            return
        for key in self._dirty:
            record = self._memory.get(key)
            if record is None:
                continue
            try:
                self._trails.set(key, record.to_dict(), ttl_seconds=self.ttl_seconds)
            except OSError:
                continue
        self._dirty.clear()

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._db.close()
        self._closed = True

    def __enter__(self) -> "TrailStore":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
