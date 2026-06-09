import datetime
import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fsgc.config import Recovery, Signature
from fsgc.scanner import DirectoryNode
from fsgc.sweeper import Action, SkipReason, Sweeper, SweepResult


def _make_node(path: Path, size: int = 1024) -> DirectoryNode:
    """Build a minimal DirectoryNode for sweep tests."""
    return DirectoryNode(path=path, size=size)


def _node_sig() -> Signature:
    """Common test signature: matches node_modules with package.json sentinel."""
    return Signature(
        name="Node",
        pattern="**/node_modules",
        recovery=Recovery.NETWORK,
        sentinels=["package.json"],
    )


def _make_group(
    name: str,
    nodes: list[DirectoryNode],
    signature: Signature | None = None,
) -> dict[str, Any]:
    """Build a group dict in the shape aggregator.group_by_signature emits."""
    if signature is None:
        signature = Signature(name=name, pattern=f"**/{name}", recovery=Recovery.TRIVIAL)
    return {
        "name": name,
        "signature": signature,
        "nodes": nodes,
        "size": sum(n.size for n in nodes),
        "avg_score": 0.9,
        "auto_check": True,
    }


# ── dry-run vs run ──────────────────────────────────────────────────────────


def test_sweep_dry_run_makes_no_changes(tmp_path: Path) -> None:
    target = tmp_path / "node_modules"
    target.mkdir()
    (target / "package.json").write_text("{}")
    (target / "file.bin").write_bytes(b"x" * 1024)

    node = _make_node(target, size=1024)
    sig = _node_sig()
    groups = [_make_group("Node", [node], signature=sig)]

    result = Sweeper(dry_run=True, unsafe_roots=frozenset()).sweep(groups)

    assert target.exists(), "dry-run must not delete anything"
    assert len(result.deleted) == 1
    assert result.deleted[0].path == target
    assert result.total_freed_bytes == 1024


def test_sweep_run_deletes_directory(tmp_path: Path) -> None:
    target = tmp_path / "node_modules"
    target.mkdir()
    (target / "package.json").write_text("{}")
    (target / "file.bin").write_bytes(b"x" * 2048)

    node = _make_node(target, size=2048)
    sig = _node_sig()
    groups = [_make_group("Node", [node], signature=sig)]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert not target.exists(), "run mode must delete the directory"
    assert len(result.deleted) == 1
    assert result.total_freed_bytes == 2048


# ── safety: unsafe-root guard ───────────────────────────────────────────────


def test_sweep_skips_unsafe_root(tmp_path: Path) -> None:
    forbidden = tmp_path / "system"
    forbidden.mkdir()
    (forbidden / "important.conf").write_text("x")

    node = _make_node(forbidden, size=1)
    groups = [_make_group("Anything", [node])]

    result = Sweeper(dry_run=False, unsafe_roots=frozenset({forbidden.resolve()})).sweep(groups)

    assert forbidden.exists(), "unsafe roots must never be deleted"
    assert len(result.deleted) == 0
    assert len(result.skipped) == 1
    assert result.skipped[0].skip_reason == SkipReason.UNSAFE_ROOT


def test_sweep_skips_filesystem_root() -> None:
    # The actual filesystem root is the canonical foot-gun. Sweeper must
    # refuse to delete `/` regardless of unsafe_roots configuration, because
    # `Path("/").parent == Path("/")` is the fundamental signal of a root.
    node = _make_node(Path("/"), size=1)
    groups = [_make_group("Anything", [node])]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert len(result.deleted) == 0
    assert result.skipped[0].skip_reason == SkipReason.UNSAFE_ROOT


# ── safety: symlinks ────────────────────────────────────────────────────────


def test_sweep_skips_symlinks_and_preserves_target(tmp_path: Path) -> None:
    real_target = tmp_path / "real_dir"
    real_target.mkdir()
    (real_target / "package.json").write_text("{}")
    (real_target / "precious.txt").write_text("do not delete")

    symlink = tmp_path / "node_modules"
    symlink.symlink_to(real_target)

    node = _make_node(symlink, size=1)
    sig = _node_sig()
    groups = [_make_group("Node", [node], signature=sig)]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert real_target.exists(), "symlink target must be untouched"
    assert (real_target / "precious.txt").exists()
    assert len(result.skipped) == 1
    assert result.skipped[0].skip_reason == SkipReason.SYMLINK


# ── safety: sentinel re-verification at sweep time ──────────────────────────


def test_sweep_reverifies_sentinel_present(tmp_path: Path) -> None:
    target = tmp_path / ".venv"
    target.mkdir()
    (target / "pyvenv.cfg").write_text("home = /usr/bin")

    node = _make_node(target, size=512)
    sig = Signature(
        name="Python Virtualenv",
        pattern="**/.venv",
        recovery=Recovery.NETWORK,
        sentinels=["pyvenv.cfg"],
    )
    groups = [_make_group("Python Virtualenv", [node], signature=sig)]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert not target.exists()
    assert len(result.deleted) == 1


def test_sweep_skips_when_sentinel_missing_at_sweep_time(tmp_path: Path) -> None:
    # Simulate the race where the scan saw a sentinel but it's gone by sweep time
    # (someone removed pyvenv.cfg between scan and confirm, or the dir was repurposed).
    target = tmp_path / ".venv"
    target.mkdir()
    # NO pyvenv.cfg created — sentinel is missing.
    (target / "src.py").write_text("print('this is not a venv anymore')")

    node = _make_node(target, size=512)
    sig = Signature(
        name="Python Virtualenv",
        pattern="**/.venv",
        recovery=Recovery.NETWORK,
        sentinels=["pyvenv.cfg"],
    )
    groups = [_make_group("Python Virtualenv", [node], signature=sig)]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert target.exists(), "must not delete when sentinel disappeared since scan"
    assert (target / "src.py").exists()
    assert len(result.skipped) == 1
    assert result.skipped[0].skip_reason == SkipReason.SENTINEL_MISSING


def test_sweep_no_sentinels_required_proceeds(tmp_path: Path) -> None:
    # Some signatures (e.g. __pycache__) have no sentinels — sweep must proceed.
    target = tmp_path / "__pycache__"
    target.mkdir()
    (target / "module.cpython-312.pyc").write_bytes(b"\x00" * 32)

    node = _make_node(target, size=32)
    sig = Signature(
        name="Python Bytecode", pattern="**/__pycache__", recovery=Recovery.TRIVIAL, sentinels=[]
    )
    groups = [_make_group("Python Bytecode", [node], signature=sig)]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert not target.exists()
    assert len(result.deleted) == 1


# ── safety: missing path ────────────────────────────────────────────────────


def test_sweep_skips_missing_path(tmp_path: Path) -> None:
    # User scanned then deleted manually before confirming sweep.
    node = _make_node(tmp_path / "already_gone", size=1)
    groups = [_make_group("Anything", [node])]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert len(result.deleted) == 0
    assert result.skipped[0].skip_reason == SkipReason.MISSING


# ── safety: error tolerance + accurate accounting ───────────────────────────


def test_sweep_tolerates_oserror_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two valid targets; rmtree raises on the first, second must still run.
    first = tmp_path / "first"
    first.mkdir()
    (first / "package.json").write_text("{}")
    second = tmp_path / "second"
    second.mkdir()
    (second / "package.json").write_text("{}")

    sig = _node_sig()

    real_rmtree = shutil.rmtree
    calls: list[Path] = []

    def flaky_rmtree(path: str | Path, *args: Any, **kwargs: Any) -> None:
        p = Path(path)
        calls.append(p)
        if p == first:
            raise PermissionError("simulated EPERM")
        real_rmtree(p, *args, **kwargs)

    monkeypatch.setattr("fsgc.sweeper.shutil.rmtree", flaky_rmtree)

    nodes = [_make_node(first, size=100), _make_node(second, size=200)]
    groups = [_make_group("Node", nodes, signature=sig)]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert calls == [first, second], "second node must be processed despite first failure"
    assert first.exists(), "failed delete leaves directory in place"
    assert not second.exists()
    assert len(result.deleted) == 1
    assert len(result.errors) == 1
    assert result.errors[0].path == first
    assert "EPERM" in (result.errors[0].error or "")


def test_sweep_freed_bytes_excludes_skipped_and_errored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted_target = tmp_path / "deleted"
    deleted_target.mkdir()
    (deleted_target / "package.json").write_text("{}")

    errored_target = tmp_path / "errored"
    errored_target.mkdir()
    (errored_target / "package.json").write_text("{}")

    skipped_target = tmp_path / "skipped_missing"  # never created → MISSING

    sig = _node_sig()

    real_rmtree = shutil.rmtree

    def selective_rmtree(path: str | Path, *args: Any, **kwargs: Any) -> None:
        p = Path(path)
        if p == errored_target:
            raise OSError("simulated")
        real_rmtree(p, *args, **kwargs)

    monkeypatch.setattr("fsgc.sweeper.shutil.rmtree", selective_rmtree)

    nodes = [
        _make_node(deleted_target, size=1000),
        _make_node(errored_target, size=10_000),
        _make_node(skipped_target, size=100_000),
    ]
    groups = [_make_group("Node", nodes, signature=sig)]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert result.total_freed_bytes == 1000, (
        "freed bytes must only count nodes that actually deleted; "
        "errored and skipped nodes contribute nothing"
    )
    assert len(result.deleted) == 1
    assert len(result.errors) == 1
    assert len(result.skipped) == 1


# ── Slice B: trash mode (recoverable deletion) ──────────────────────────────


def test_sweep_default_action_is_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "node_modules"
    target.mkdir()
    (target / "package.json").write_text("{}")
    calls: list[Path] = []

    def fake_send2trash(p: str | Path) -> None:
        calls.append(Path(p))

    monkeypatch.setattr("fsgc.sweeper.send2trash", fake_send2trash)

    node = _make_node(target, size=512)
    groups = [_make_group("Node", [node], signature=_node_sig())]

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

    assert calls == [target], "default mode must route through send2trash"
    assert len(result.deleted) == 1
    assert result.deleted[0].action == Action.TRASHED


def test_sweep_permanent_mode_uses_rmtree_not_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "node_modules"
    target.mkdir()
    (target / "package.json").write_text("{}")

    trash_calls: list[Path] = []

    def fake_send2trash(p: str | Path) -> None:
        trash_calls.append(Path(p))

    monkeypatch.setattr("fsgc.sweeper.send2trash", fake_send2trash)

    node = _make_node(target, size=512)
    groups = [_make_group("Node", [node], signature=_node_sig())]

    result = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset()).sweep(groups)

    assert trash_calls == [], "trash=False must not call send2trash"
    assert not target.exists(), "permanent mode rmtrees in place"
    assert result.deleted[0].action == Action.DELETED


def test_sweep_dry_run_records_dry_action(tmp_path: Path) -> None:
    target = tmp_path / "node_modules"
    target.mkdir()
    (target / "package.json").write_text("{}")

    node = _make_node(target, size=128)
    groups = [_make_group("Node", [node], signature=_node_sig())]

    result = Sweeper(dry_run=True, unsafe_roots=frozenset()).sweep(groups)

    assert result.deleted[0].action == Action.DRY_RUN


def test_sweep_trash_oserror_recorded_as_errored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # send2trash can raise (e.g. TrashPermissionError on volumes without trash).
    target = tmp_path / "node_modules"
    target.mkdir()
    (target / "package.json").write_text("{}")

    def raising_send2trash(_p: str | Path) -> None:
        raise OSError("simulated trash failure")

    monkeypatch.setattr("fsgc.sweeper.send2trash", raising_send2trash)

    node = _make_node(target, size=128)
    groups = [_make_group("Node", [node], signature=_node_sig())]

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

    assert target.exists(), "failed trash leaves the directory in place"
    assert len(result.deleted) == 0
    assert len(result.errors) == 1
    assert result.errors[0].action == Action.ERRORED


# ── Slice B: JSONL sweep journal ────────────────────────────────────────────


def _fixed_clock() -> Callable[[], datetime.datetime]:
    return lambda: datetime.datetime(2026, 6, 8, 12, 0, 0, tzinfo=datetime.UTC)


def _read_journal(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_journal_records_every_sweep_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One trashed, one skipped (sentinel missing), one errored. All three must be journaled.
    trashed = tmp_path / "node_modules_a"
    trashed.mkdir()
    (trashed / "package.json").write_text("{}")

    skipped = tmp_path / "node_modules_b"
    skipped.mkdir()
    # no package.json → SENTINEL_MISSING

    errored = tmp_path / "node_modules_c"
    errored.mkdir()
    (errored / "package.json").write_text("{}")

    def selective_trash(p: str | Path) -> None:
        if Path(p) == errored:
            raise OSError("nope")

    monkeypatch.setattr("fsgc.sweeper.send2trash", selective_trash)

    journal = tmp_path / "log.jsonl"
    nodes = [
        _make_node(trashed, size=100),
        _make_node(skipped, size=200),
        _make_node(errored, size=300),
    ]
    groups = [_make_group("Node", nodes, signature=_node_sig())]

    Sweeper(
        dry_run=False,
        unsafe_roots=frozenset(),
        journal_path=journal,
        now=_fixed_clock(),
    ).sweep(groups)

    entries = _read_journal(journal)
    assert len(entries) == 3
    by_path = {e["path"]: e for e in entries}

    assert by_path[str(trashed)]["action"] == "trashed"
    assert by_path[str(trashed)]["size_bytes"] == 100
    assert by_path[str(trashed)]["detail"] is None
    assert by_path[str(trashed)]["timestamp"] == "2026-06-08T12:00:00+00:00"
    assert by_path[str(trashed)]["signature"] == "Node"

    assert by_path[str(skipped)]["action"] == "skipped"
    assert by_path[str(skipped)]["detail"] == "sentinel-missing"
    assert by_path[str(skipped)]["size_bytes"] == 0

    assert by_path[str(errored)]["action"] == "errored"
    assert "nope" in by_path[str(errored)]["detail"]


def test_journal_disabled_when_path_is_none(tmp_path: Path) -> None:
    target = tmp_path / "__pycache__"
    target.mkdir()
    (target / "x.pyc").write_bytes(b"\x00")
    sig = Signature(
        name="Pycache", pattern="**/__pycache__", recovery=Recovery.TRIVIAL, sentinels=[]
    )
    nodes = [_make_node(target, size=1)]

    Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset(), journal_path=None).sweep(
        [_make_group("Pycache", nodes, signature=sig)]
    )

    # No journal file should have been created anywhere under tmp_path.
    assert list(tmp_path.glob("*.jsonl")) == []


def test_journal_appends_across_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fsgc.sweeper.send2trash", lambda _p: None)

    a = tmp_path / "node_modules_a"
    a.mkdir()
    (a / "package.json").write_text("{}")
    b = tmp_path / "node_modules_b"
    b.mkdir()
    (b / "package.json").write_text("{}")

    journal = tmp_path / "nested" / "log.jsonl"  # also verifies parent mkdir
    sweeper = Sweeper(
        dry_run=False,
        unsafe_roots=frozenset(),
        journal_path=journal,
        now=_fixed_clock(),
    )

    sweeper.sweep([_make_group("Node", [_make_node(a, size=1)], signature=_node_sig())])
    sweeper.sweep([_make_group("Node", [_make_node(b, size=2)], signature=_node_sig())])

    entries = _read_journal(journal)
    assert len(entries) == 2
    assert {e["path"] for e in entries} == {str(a), str(b)}


# ── Slice C: parallel sweep + progress callback ─────────────────────────────


def _populate(target: Path) -> None:
    target.mkdir()
    (target / "package.json").write_text("{}")
    (target / "blob.bin").write_bytes(b"x" * 256)


def test_sweep_parallel_processes_every_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_concurrency > 1 must still produce one record per node."""
    monkeypatch.setattr("fsgc.sweeper.send2trash", lambda _p: None)

    nodes = []
    for i in range(20):
        target = tmp_path / f"node_modules_{i:02d}"
        _populate(target)
        nodes.append(_make_node(target, size=256 * (i + 1)))

    groups = [_make_group("Node", nodes, signature=_node_sig())]

    result = Sweeper(dry_run=False, max_concurrency=8, unsafe_roots=frozenset()).sweep(groups)

    assert len(result.records) == 20
    assert len(result.deleted) == 20
    assert {r.action for r in result.records} == {Action.TRASHED}
    assert result.total_freed_bytes == sum(256 * (i + 1) for i in range(20))


def test_sweep_parallel_preserves_submission_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Records come back in submission order regardless of completion order.

    A spy on send2trash deliberately stalls the first item so later items
    complete first; result.records must still match the submission ordering.
    """
    completed_order: list[Path] = []
    block = threading.Event()
    started = threading.Event()

    def staggered_trash(p: str | Path) -> None:
        path = Path(p)
        if path.name == "node_modules_00":
            started.set()
            block.wait(timeout=5.0)
        completed_order.append(path)

    monkeypatch.setattr("fsgc.sweeper.send2trash", staggered_trash)

    nodes = []
    for i in range(4):
        target = tmp_path / f"node_modules_{i:02d}"
        _populate(target)
        nodes.append(_make_node(target, size=100))
    groups = [_make_group("Node", nodes, signature=_node_sig())]

    sweeper = Sweeper(dry_run=False, max_concurrency=4, unsafe_roots=frozenset())

    def run() -> SweepResult:
        return sweeper.sweep(groups)

    holder: list[SweepResult] = []
    runner = threading.Thread(target=lambda: holder.append(run()))
    runner.start()

    # Wait until the blocked worker has started, then let the others finish first.
    assert started.wait(timeout=5.0), "first worker never started"
    # Give the other 3 a moment to complete past the blocked one.
    import time

    time.sleep(0.2)
    block.set()
    runner.join(timeout=5.0)

    assert holder, "sweep did not return"
    result = holder[0]
    assert [r.path.name for r in result.records] == [f"node_modules_{i:02d}" for i in range(4)]
    # And the completion order shows node_modules_00 finished last (the staggered one).
    assert completed_order[-1].name == "node_modules_00"


def test_sweep_progress_callback_fires_once_per_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fsgc.sweeper.send2trash", lambda _p: None)

    nodes = []
    for i in range(7):
        target = tmp_path / f"node_modules_{i:02d}"
        _populate(target)
        nodes.append(_make_node(target, size=100))
    groups = [_make_group("Node", nodes, signature=_node_sig())]

    seen: list[Path] = []
    lock = threading.Lock()

    def callback(record: Any) -> None:
        with lock:
            seen.append(record.path)

    Sweeper(dry_run=False, max_concurrency=4, unsafe_roots=frozenset()).sweep(
        groups, progress_callback=callback
    )

    assert len(seen) == 7
    assert {p.name for p in seen} == {f"node_modules_{i:02d}" for i in range(7)}


def test_sweep_parallel_journal_contains_every_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent journal writes are serialized by the sweeper's lock — no entries lost."""
    monkeypatch.setattr("fsgc.sweeper.send2trash", lambda _p: None)

    nodes = []
    for i in range(30):
        target = tmp_path / f"node_modules_{i:02d}"
        _populate(target)
        nodes.append(_make_node(target, size=100))
    groups = [_make_group("Node", nodes, signature=_node_sig())]

    journal = tmp_path / "log.jsonl"
    Sweeper(
        dry_run=False,
        max_concurrency=8,
        unsafe_roots=frozenset(),
        journal_path=journal,
        now=_fixed_clock(),
    ).sweep(groups)

    entries = _read_journal(journal)
    assert len(entries) == 30
    paths = {e["path"] for e in entries}
    assert paths == {str(tmp_path / f"node_modules_{i:02d}") for i in range(30)}
    assert all(e["action"] == "trashed" for e in entries)


def test_sweep_empty_groups_returns_empty_result() -> None:
    result = Sweeper(dry_run=False, max_concurrency=8, unsafe_roots=frozenset()).sweep([])
    assert result.records == []
    assert result.total_freed_bytes == 0


def test_sweeper_records_review_flag_on_group(tmp_path: Path) -> None:
    """
    A group marked with review=True flows that flag into every DeletionRecord
    and (when journal_path is set) the JSONL journal line.
    """
    from fsgc.behavior import BehavioralMatch

    target = tmp_path / "doomed.bin"
    target.write_bytes(b"x" * 1024)

    match = BehavioralMatch(path=target, rule_name="Old Download", size_bytes=1024, age_days=120)
    group = {
        "name": "Old Download",
        "review": True,
        "matches": [match],
        "auto_check": False,
    }
    journal = tmp_path / "log.jsonl"
    sweeper = Sweeper(
        dry_run=False,
        trash=False,
        unsafe_roots=frozenset(),
        journal_path=journal,
    )
    result = sweeper.sweep([group])

    assert not target.exists()
    assert len(result.deleted) == 1
    assert result.deleted[0].review is True
    assert result.deleted[0].freed_bytes == 1024

    lines = [json.loads(line) for line in journal.read_text().splitlines() if line]
    assert lines[0]["review"] is True
    assert lines[0]["signature"] == "Old Download"
    assert lines[0]["size_bytes"] == 1024


def test_sweeper_uses_recorded_size_for_stale_dir_review(tmp_path: Path) -> None:
    """
    For stale_dir REVIEW matches, the rolled-up directory size carried by
    BehavioralMatch.size_bytes must flow through to DeletionRecord.freed_bytes
    — a directory's own st_size is 0 on most filesystems, so the sweeper
    cannot recompute it from path.stat().
    """
    from fsgc.behavior import BehavioralMatch

    stale_project = tmp_path / "old-project"
    stale_project.mkdir()
    (stale_project / "main.py").write_bytes(b"x" * 100_000)

    match = BehavioralMatch(
        path=stale_project,
        rule_name="Stale Code Project",
        size_bytes=100_000,
        age_days=200,
    )
    group = {
        "name": "Stale Code Project",
        "review": True,
        "matches": [match],
        "auto_check": False,
    }
    sweeper = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset())
    result = sweeper.sweep([group])

    assert not stale_project.exists()
    assert len(result.deleted) == 1
    assert result.deleted[0].freed_bytes == 100_000
    assert result.total_freed_bytes == 100_000


def test_sweeper_records_review_false_for_structural(tmp_path: Path) -> None:
    """Structural groups omit the review flag (or set it to False)."""
    import json

    from fsgc.config import Recovery, Signature
    from fsgc.scanner import DirectoryNode
    from fsgc.sweeper import Sweeper

    target = tmp_path / "__pycache__"
    target.mkdir()
    (target / "x.pyc").write_bytes(b"x")

    node = DirectoryNode(path=target, size=1)
    sig = Signature(name="Python Bytecode", pattern="**/__pycache__", recovery=Recovery.TRIVIAL)
    group = {
        "name": "Python Bytecode",
        "signature": sig,
        "nodes": [node],
    }
    journal = tmp_path / "log.jsonl"
    sweeper = Sweeper(dry_run=False, trash=False, unsafe_roots=frozenset(), journal_path=journal)
    result = sweeper.sweep([group])

    assert len(result.deleted) == 1
    assert result.deleted[0].review is False
    lines = [json.loads(line) for line in journal.read_text().splitlines() if line]
    assert lines[0]["review"] is False
