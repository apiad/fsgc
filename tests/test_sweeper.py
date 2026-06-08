import shutil
from pathlib import Path
from typing import Any

import pytest

from fsgc.config import Signature
from fsgc.scanner import DirectoryNode
from fsgc.sweeper import SkipReason, Sweeper


def _make_node(path: Path, size: int = 1024) -> DirectoryNode:
    """Build a minimal DirectoryNode for sweep tests."""
    return DirectoryNode(path=path, size=size)


def _node_sig() -> Signature:
    """Common test signature: matches node_modules with package.json sentinel."""
    return Signature(
        name="Node",
        pattern="**/node_modules",
        priority=0.9,
        sentinels=["package.json"],
    )


def _make_group(
    name: str,
    nodes: list[DirectoryNode],
    signature: Signature | None = None,
) -> dict[str, Any]:
    """Build a group dict in the shape aggregator.group_by_signature emits."""
    if signature is None:
        signature = Signature(name=name, pattern=f"**/{name}", priority=0.9)
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

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

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

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

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

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

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
        priority=0.9,
        sentinels=["pyvenv.cfg"],
    )
    groups = [_make_group("Python Virtualenv", [node], signature=sig)]

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

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
        priority=0.9,
        sentinels=["pyvenv.cfg"],
    )
    groups = [_make_group("Python Virtualenv", [node], signature=sig)]

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

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
    sig = Signature(name="Python Bytecode", pattern="**/__pycache__", priority=1.0, sentinels=[])
    groups = [_make_group("Python Bytecode", [node], signature=sig)]

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

    assert not target.exists()
    assert len(result.deleted) == 1


# ── safety: missing path ────────────────────────────────────────────────────


def test_sweep_skips_missing_path(tmp_path: Path) -> None:
    # User scanned then deleted manually before confirming sweep.
    node = _make_node(tmp_path / "already_gone", size=1)
    groups = [_make_group("Anything", [node])]

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

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

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

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

    result = Sweeper(dry_run=False, unsafe_roots=frozenset()).sweep(groups)

    assert result.total_freed_bytes == 1000, (
        "freed bytes must only count nodes that actually deleted; "
        "errored and skipped nodes contribute nothing"
    )
    assert len(result.deleted) == 1
    assert len(result.errors) == 1
    assert len(result.skipped) == 1
