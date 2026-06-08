"""Tests for the beaver-backed TrailStore."""

import time
from pathlib import Path

from fsgc.trail import TopChild, TrailRecord, TrailStore, calculate_fingerprint


def _make_record(fingerprint: int = 42) -> TrailRecord:
    return TrailRecord(
        scanned_at=time.time(),
        fingerprint=fingerprint,
        total_size=10 * 1024 * 1024,
        entry_count=12,
        atime=time.time() - 86400,
        mtime=time.time() - 86400,
        file_evidence=["package.json", ".o"],
        top_children=[
            TopChild(name="node_modules", score=0.4, size=8 * 1024 * 1024),
            TopChild(name=".venv", score=0.4, size=2 * 1024 * 1024),
        ],
    )


def test_trail_store_roundtrip(tmp_path: Path) -> None:
    store = TrailStore(db_path=tmp_path / "trails.db")
    target = tmp_path / "some_dir"
    target.mkdir()
    record = _make_record(fingerprint=99)
    store.put(target, record)

    fetched = store.get(target)
    assert fetched is not None
    assert fetched.fingerprint == 99
    assert fetched.total_size == record.total_size
    assert fetched.entry_count == 12
    assert fetched.file_evidence == ["package.json", ".o"]
    assert len(fetched.top_children) == 2
    assert fetched.top_children[0].name == "node_modules"
    assert fetched.top_children[0].score == 0.4
    store.close()


def test_trail_store_missing_key_returns_none(tmp_path: Path) -> None:
    store = TrailStore(db_path=tmp_path / "trails.db")
    assert store.get(tmp_path / "never_scanned") is None
    store.close()


def test_trail_store_clear_drops_everything(tmp_path: Path) -> None:
    store = TrailStore(db_path=tmp_path / "trails.db")
    for i in range(5):
        d = tmp_path / f"d{i}"
        d.mkdir()
        store.put(d, _make_record())
    assert sum(1 for _ in store.keys()) == 5

    store.clear()

    assert sum(1 for _ in store.keys()) == 0
    store.close()


def test_calculate_fingerprint_is_stable_and_changes_with_inputs() -> None:
    fp1 = calculate_fingerprint(1000.0, 5)
    fp2 = calculate_fingerprint(1000.0, 5)
    assert fp1 == fp2
    assert isinstance(fp1, int)
    assert 0 <= fp1 < 2**64

    # Different mtime → different fingerprint
    assert calculate_fingerprint(1001.0, 5) != fp1
    # Different entry_count → different fingerprint
    assert calculate_fingerprint(1000.0, 6) != fp1


def test_trail_store_context_manager_closes(tmp_path: Path) -> None:
    db = tmp_path / "trails.db"
    with TrailStore(db_path=db) as store:
        store.put(tmp_path, _make_record())
    # After context exit, opening again should still see the record.
    with TrailStore(db_path=db) as store2:
        assert store2.get(tmp_path) is not None
