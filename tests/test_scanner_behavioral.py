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
