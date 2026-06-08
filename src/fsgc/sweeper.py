"""
Sweeper — the deletion stage of fsgc, isolated and tested.

Owns every decision between "user confirmed the collection" and "files are
gone from disk". Three classes of safety guard run on every node:

  * unsafe-root guard: refuses filesystem root, configured forbidden paths,
    and the user's home directory (Path.home() itself, not its children).
  * symlink guard: refuses to follow symlinks; the target is preserved
    even when the link's name matches a signature pattern.
  * sentinel re-verification: re-stats the directory at sweep time and
    confirms at least one signature sentinel is still present, catching
    the race where the scan saw a sentinel that disappeared by confirm time.

Default deletion is to the system trash (send2trash) so confirmed sweeps
remain recoverable. Permanent unlink/rmtree is opt-in via trash=False.

Every record (deleted, skipped, or errored) is appended as one JSONL line
to journal_path when configured, providing an audit trail.

Returns a structured SweepResult so the CLI can format output independently
and tests can assert on machine-readable records.
"""

import datetime
import json
import os
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from send2trash import send2trash

from fsgc.config import Signature

DEFAULT_UNSAFE_ROOTS: frozenset[Path] = frozenset(
    Path(p)
    for p in (
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/lib64",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/srv",
        "/sys",
        "/usr",
        "/var",
    )
)


class SkipReason(Enum):
    UNSAFE_ROOT = "unsafe-root"
    SYMLINK = "symlink"
    SENTINEL_MISSING = "sentinel-missing"
    MISSING = "missing"


class Action(Enum):
    TRASHED = "trashed"
    DELETED = "deleted"
    DRY_RUN = "dry-run"
    SKIPPED = "skipped"
    ERRORED = "errored"


@dataclass
class DeletionRecord:
    path: Path
    signature_name: str
    action: Action = Action.SKIPPED
    deleted: bool = False
    freed_bytes: int = 0
    skip_reason: SkipReason | None = None
    error: str | None = None
    review: bool = False


@dataclass
class SweepResult:
    records: list[DeletionRecord] = field(default_factory=list)

    @property
    def deleted(self) -> list[DeletionRecord]:
        return [r for r in self.records if r.deleted]

    @property
    def skipped(self) -> list[DeletionRecord]:
        return [r for r in self.records if r.skip_reason is not None]

    @property
    def errors(self) -> list[DeletionRecord]:
        return [r for r in self.records if r.error is not None]

    @property
    def total_freed_bytes(self) -> int:
        return sum(r.freed_bytes for r in self.deleted)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class Sweeper:
    def __init__(
        self,
        *,
        dry_run: bool = True,
        trash: bool = True,
        max_concurrency: int = 1,
        unsafe_roots: frozenset[Path] | None = None,
        journal_path: Path | None = None,
        now: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.trash = trash
        self.max_concurrency = max(1, max_concurrency)
        self.journal_path = journal_path
        self._now = now or _utcnow
        self._journal_lock = threading.Lock()
        roots = DEFAULT_UNSAFE_ROOTS if unsafe_roots is None else unsafe_roots
        self._unsafe_roots: set[Path] = {self._safe_resolve(p) for p in roots}
        self._unsafe_roots.add(self._safe_resolve(Path.home()))

    def sweep(
        self,
        groups: list[dict[str, Any]],
        progress_callback: Callable[[DeletionRecord], None] | None = None,
    ) -> SweepResult:
        """
        Process every node in every group. Returns records in submission order
        (group order then node order) regardless of completion order.

        With max_concurrency > 1, deletions run on a thread pool — IO-bound
        rmtree/send2trash calls release the GIL during syscalls. The
        progress_callback fires in *completion* order so the caller's
        progress bar updates as soon as work finishes, while result.records
        is reassembled in submission order for stable downstream output.
        """
        work: list[tuple[int, Path, int, Signature | None, str, bool]] = []
        for group in groups:
            group_name: str = group["name"]
            is_review = bool(group.get("review", False))
            if is_review:
                # Behavioral group: items are bare Paths.
                for path in group.get("behavioral_paths", []):
                    size = path.stat().st_size if path.is_file() else 0
                    work.append((len(work), Path(path), size, None, group_name, True))
            else:
                signature: Signature = group["signature"]
                for node in group["nodes"]:
                    work.append((len(work), node.path, node.size, signature, group_name, False))

        records_by_idx: dict[int, DeletionRecord] = {}
        if not work:
            return SweepResult()

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            future_to_idx = {
                pool.submit(self._process_one, path, size, sig, gn, rv): i
                for i, path, size, sig, gn, rv in work
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                record = future.result()
                records_by_idx[idx] = record
                self._journal(record)
                if progress_callback is not None:
                    progress_callback(record)

        return SweepResult(records=[records_by_idx[i] for i in range(len(work))])

    def _process_one(
        self,
        path: Path,
        node_size: int,
        signature: Signature | None,
        group_name: str,
        review: bool,
    ) -> DeletionRecord:
        record = DeletionRecord(path=path, signature_name=group_name, review=review)

        if self._is_unsafe_root(path):
            record.skip_reason = SkipReason.UNSAFE_ROOT
            record.action = Action.SKIPPED
            return record

        if path.is_symlink():
            record.skip_reason = SkipReason.SYMLINK
            record.action = Action.SKIPPED
            return record

        if not path.exists():
            record.skip_reason = SkipReason.MISSING
            record.action = Action.SKIPPED
            return record

        # Sentinel re-verification only applies to structural groups.
        if signature is not None and not self._reverify_sentinel(path, signature):
            record.skip_reason = SkipReason.SENTINEL_MISSING
            record.action = Action.SKIPPED
            return record

        if self.dry_run:
            record.deleted = True
            record.freed_bytes = node_size
            record.action = Action.DRY_RUN
            return record

        try:
            if self.trash:
                send2trash(path)
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as e:
            record.error = f"{type(e).__name__}: {e}"
            record.action = Action.ERRORED
            return record

        record.deleted = True
        record.freed_bytes = node_size
        record.action = Action.TRASHED if self.trash else Action.DELETED
        return record

    def _is_unsafe_root(self, path: Path) -> bool:
        resolved = self._safe_resolve(path)
        if resolved == resolved.parent:
            return True
        return resolved in self._unsafe_roots

    @staticmethod
    def _safe_resolve(path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    @staticmethod
    def _reverify_sentinel(path: Path, signature: Signature) -> bool:
        if not signature.sentinels:
            return True
        try:
            entries = [entry.name for entry in os.scandir(path)]
        except OSError:
            return False
        for sentinel in signature.sentinels:
            for name in entries:
                if name == sentinel or fnmatch(name, sentinel):
                    return True
        return False

    def _journal(self, record: DeletionRecord) -> None:
        if self.journal_path is None:
            return
        detail: str | None = None
        if record.skip_reason is not None:
            detail = record.skip_reason.value
        elif record.error is not None:
            detail = record.error
        entry = {
            "timestamp": self._now().isoformat(),
            "path": str(record.path),
            "signature": record.signature_name,
            "size_bytes": record.freed_bytes,
            "action": record.action.value,
            "detail": detail,
            "review": record.review,
        }
        with self._journal_lock:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
