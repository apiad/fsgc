import asyncio
import os
import time
from pathlib import Path

from fsgc.behavior import (
    BehavioralKind,
    BehavioralRule,
    BehavioralRuleManager,
    BehavioralSignal,
)
from fsgc.scanner import Scanner


def _make_manager(rule: BehavioralRule) -> BehavioralRuleManager:
    """Build a manager with a single rule, bypassing YAML loading."""
    mgr = BehavioralRuleManager.__new__(BehavioralRuleManager)
    mgr.rules = [rule]
    mgr.default_path = Path("/dev/null")
    mgr.user_path = Path("/dev/null")
    mgr.config_path = Path("/dev/null")
    return mgr


def test_scanner_flags_old_git_repo(tmp_path: Path) -> None:
    """A directory with .git/HEAD mtime older than min_age_days is flagged."""
    repo = tmp_path / "old-prototype"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n")
    (repo / "src.py").write_bytes(b"x" * 4096)

    # Set HEAD mtime to 200 days ago.
    ancient = time.time() - (200 * 86400)
    os.utime(head, (ancient, ancient))

    mgr = _make_manager(
        BehavioralRule(
            name="Stale Code Project",
            kind=BehavioralKind.STALE_DIR,
            signal=BehavioralSignal.GIT_HEAD_MTIME,
            min_age_days=180,
        )
    )
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert len(scanner.behavioral_matches) == 1
    m = scanner.behavioral_matches[0]
    assert m.path == repo
    assert m.rule_name == "Stale Code Project"
    assert m.age_days >= 199  # rounding tolerance
    # size_bytes is rolled up after walk (includes src.py).
    assert m.size_bytes > 0


def test_scanner_skips_recent_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "active-repo"
    head = repo / ".git" / "HEAD"
    head.parent.mkdir(parents=True)
    head.write_text("ref: refs/heads/main\n")  # fresh mtime by default

    mgr = _make_manager(
        BehavioralRule(
            name="Stale Code Project",
            kind=BehavioralKind.STALE_DIR,
            signal=BehavioralSignal.GIT_HEAD_MTIME,
            min_age_days=180,
        )
    )
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []


def test_scanner_skips_dirs_without_git(tmp_path: Path) -> None:
    """A non-git directory, however old, is not a stale code project."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "doc.txt").write_text("x")

    mgr = _make_manager(
        BehavioralRule(
            name="Stale Code Project",
            kind=BehavioralKind.STALE_DIR,
            signal=BehavioralSignal.GIT_HEAD_MTIME,
            min_age_days=180,
        )
    )
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []


def test_scanner_flags_old_download_by_path_scope(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old_dmg = downloads / "firefox-old.dmg"
    old_dmg.write_bytes(b"x" * 4096)
    ancient = time.time() - (100 * 86400)
    os.utime(old_dmg, (ancient, ancient))

    mgr = _make_manager(
        BehavioralRule(
            name="Old Download",
            kind=BehavioralKind.STALE_FILE,
            signal=BehavioralSignal.FILE_MTIME,
            min_age_days=90,
            path_scope="**/Downloads/*",
        )
    )
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    matches = [m for m in scanner.behavioral_matches if m.rule_name == "Old Download"]
    assert len(matches) == 1
    assert matches[0].path == old_dmg
    assert matches[0].size_bytes == 4096


def test_scanner_respects_min_size_bytes(tmp_path: Path) -> None:
    """A .pt file at 1 KB never matches the 500 MB rule, even if ancient."""
    tiny = tmp_path / "tiny.pt"
    tiny.write_bytes(b"x" * 1024)
    ancient = time.time() - (365 * 86400)
    os.utime(tiny, (ancient, ancient))

    mgr = _make_manager(
        BehavioralRule(
            name="Old Large ML Weights",
            kind=BehavioralKind.STALE_FILE,
            signal=BehavioralSignal.FILE_MTIME,
            min_age_days=180,
            extensions=[".pt"],
            min_size_bytes=524_288_000,
        )
    )
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []


def test_scanner_respects_extensions(tmp_path: Path) -> None:
    """A non-archive file is never a Forgotten Archive, even if old."""
    misc = tmp_path / "old_notes.txt"
    misc.write_bytes(b"x")
    ancient = time.time() - (365 * 86400)
    os.utime(misc, (ancient, ancient))

    mgr = _make_manager(
        BehavioralRule(
            name="Forgotten Archive",
            kind=BehavioralKind.STALE_FILE,
            signal=BehavioralSignal.FILE_MTIME,
            min_age_days=90,
            extensions=[".zip", ".dmg"],
        )
    )
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []


def test_scanner_tolerates_git_as_file(tmp_path: Path) -> None:
    """
    When `.git` is a regular file (a 'gitlink' pointing into a worktree or
    submodule store), os.stat(.git/HEAD) raises NotADirectoryError. The
    scanner must swallow that and simply skip the rule, not error.
    """
    repo = tmp_path / "worktree-checkout"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere\n")
    (repo / "code.py").write_bytes(b"x" * 4096)

    mgr = _make_manager(
        BehavioralRule(
            name="Stale Code Project",
            kind=BehavioralKind.STALE_DIR,
            signal=BehavioralSignal.GIT_HEAD_MTIME,
            min_age_days=180,
        )
    )
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    # No match, and the directory must have been fully walked despite the
    # gitlink — code.py's bytes should be reflected in the tree's confirmed size.
    assert scanner.behavioral_matches == []
    scanner.tree.calculate_metadata()
    repo_node = scanner.path_to_node[repo]
    assert repo_node.is_processed
    assert repo_node.confirmed_size >= 4096


def test_scanner_stale_dir_match_survives_cache_roundtrip(tmp_path: Path) -> None:
    """
    A stale_dir match found on a cold scan must reappear on a warm-cache scan
    without re-stating the .git/HEAD file. This is how the REVIEW section
    stays populated when fsgc skips walking known-stale subtrees.
    """
    from fsgc.trail import TrailStore

    repo = tmp_path / "old-prototype"
    head = repo / ".git" / "HEAD"
    head.parent.mkdir(parents=True)
    head.write_text("ref: refs/heads/main\n")
    (repo / "blob.bin").write_bytes(b"x" * 4096)
    ancient = time.time() - (200 * 86400)
    os.utime(head, (ancient, ancient))

    mgr = _make_manager(
        BehavioralRule(
            name="Stale Code Project",
            kind=BehavioralKind.STALE_DIR,
            signal=BehavioralSignal.GIT_HEAD_MTIME,
            min_age_days=180,
        )
    )
    store = TrailStore(db_path=tmp_path / "trails.db")

    async def cold() -> Scanner:
        s = Scanner(
            tmp_path,
            behavioral_manager=mgr,
            trail_store=store,
            trail_threshold_mb=0,
            budget_seconds=None,
        )
        async for _ in s.scan():
            pass
        return s

    s1 = asyncio.run(cold())
    assert len(s1.behavioral_matches) == 1

    async def warm() -> Scanner:
        s = Scanner(
            tmp_path,
            behavioral_manager=mgr,
            trail_store=store,
            trail_threshold_mb=0,
            budget_seconds=None,
        )
        async for _ in s.scan():
            pass
        return s

    s2 = asyncio.run(warm())
    matches = [m for m in s2.behavioral_matches if m.rule_name == "Stale Code Project"]
    assert len(matches) == 1
    assert matches[0].path == repo
    store.close()
