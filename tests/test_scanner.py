import asyncio
import os
import time
from pathlib import Path

from fsgc.scanner import DirectoryNode, Scanner


def test_scanner_initialization(tmp_path: Path) -> None:
    scanner = Scanner(tmp_path)
    assert scanner.root == tmp_path.resolve()
    assert scanner.stay_on_mount is True


def test_scanner_caches_signature(tmp_path: Path) -> None:
    """
    Verify that Scanner populates the signature field on DirectoryNode.
    """
    from fsgc.config import Recovery, Signature
    from fsgc.engine import HeuristicEngine

    # Setup a directory that should match a signature
    venv_path = tmp_path / ".venv"
    venv_path.mkdir()

    signatures = [Signature(name="Venv", pattern="**/.venv", recovery=Recovery.NETWORK)]
    engine = HeuristicEngine()
    scanner = Scanner(tmp_path, engine=engine, signatures=signatures)

    async def run_scan():
        async for snapshot in scanner.scan():
            if ".venv" in snapshot.children:
                return snapshot.children[".venv"]
        return None

    venv_node = asyncio.run(run_scan())

    assert venv_node is not None
    assert venv_node.signature is not None
    assert venv_node.signature.name == "Venv"
    # Create mock structure
    # tmp_path/
    #   file1 (100 bytes, old)
    #   dir1/
    #     file2 (200 bytes, new)

    file1 = tmp_path / "file1"
    file1.write_bytes(b"a" * 100)
    # Set an old timestamp for file1
    old_time = time.time() - 100000
    os.utime(file1, (old_time, old_time))

    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    file2 = dir1 / "file2"
    file2.write_bytes(b"b" * 200)
    # File2 has current time
    new_time = time.time()
    os.utime(file2, (new_time, new_time))

    scanner = Scanner(tmp_path)

    async def get_root():
        root = None
        async for snapshot in scanner.scan():
            root = snapshot
        return root

    root_node = asyncio.run(get_root())

    assert isinstance(root_node, DirectoryNode)
    assert root_node.size == 300

    # Check timestamps: root should have the 'new' time from file2 in dir1
    # Note: st_atime might be slightly different on some filesystems,
    # so we check if it's at least as recent as new_time (within a small margin)
    assert root_node.atime >= new_time - 1
    assert root_node.mtime >= new_time - 1

    dir1_node = root_node.children["dir1"]
    assert dir1_node.atime >= new_time - 1


def test_scanner_cache_hit_skips_walking_unchanged_subtree(tmp_path: Path) -> None:
    """
    The win condition: with a TrailStore and a matched signature, the second
    scan of an unchanged garbage subtree must NOT call os.scandir on it.
    Cache hits on signature-matched dirs short-circuit the walk.
    """
    import os

    from fsgc.config import Recovery, Signature
    from fsgc.engine import HeuristicEngine
    from fsgc.trail import TrailStore

    # Build a "garbage" subtree under a name that matches the trivial-recovery
    # signature below. The cache short-circuit only fires for signature-matched
    # dirs (otherwise we still need to walk children to find new garbage).
    bulky = tmp_path / "__pycache__"
    bulky.mkdir()
    for i in range(200):
        (bulky / f"f{i:03d}.bin").write_bytes(b"x" * 1024)

    sigs = [Signature(name="Pycache", pattern="**/__pycache__", recovery=Recovery.TRIVIAL)]
    engine = HeuristicEngine()
    store = TrailStore(db_path=tmp_path / "trails.db")

    async def run_once() -> int:
        # trail_threshold_mb=0 so even small test fixtures get persisted.
        scanner = Scanner(
            tmp_path,
            engine=engine,
            signatures=sigs,
            trail_store=store,
            trail_threshold_mb=0,
        )
        async for _ in scanner.scan():
            pass
        return scanner.cache_hits

    # First scan populates the trail.
    asyncio.run(run_once())

    # Second scan: spy on os.scandir to count calls. With a warm cache and
    # the bulky/ subtree unchanged, scandir on bulky/ must not fire.
    real_scandir = os.scandir
    scandir_calls: list[str] = []

    def counting_scandir(path):  # type: ignore[no-untyped-def]
        scandir_calls.append(str(path))
        return real_scandir(path)

    os.scandir = counting_scandir  # type: ignore[assignment]
    try:
        scanner2 = Scanner(
            tmp_path,
            engine=engine,
            signatures=sigs,
            trail_store=store,
            trail_threshold_mb=0,
        )

        async def second_scan() -> None:
            async for _ in scanner2.scan():
                pass

        asyncio.run(second_scan())
    finally:
        os.scandir = real_scandir  # type: ignore[assignment]

    # tmp_path itself may be re-scanned (it's the root, not signature-matched).
    # But __pycache__ — the matched signature dir with 200 files inside —
    # must have been short-circuited via the cache hit.
    bulky_walks = [c for c in scandir_calls if c == str(bulky.resolve())]
    assert bulky_walks == [], (
        f"Second scan walked bulky/ {len(bulky_walks)} time(s); the cache hit "
        f"should have skipped it entirely. All scandir calls: {scandir_calls}"
    )
    assert scanner2.cache_hits >= 1, "expected at least one cache hit on second scan"
    store.close()


def test_scanner_cache_miss_when_directory_changes(tmp_path: Path) -> None:
    """If a directory's content changes between scans, the cache must NOT short-circuit."""
    from fsgc.trail import TrailStore

    target = tmp_path / "vol"
    target.mkdir()
    (target / "a.bin").write_bytes(b"x" * 1024)

    store = TrailStore(db_path=tmp_path / "trails.db")

    async def scan_once() -> None:
        scanner = Scanner(tmp_path, trail_store=store, trail_threshold_mb=0)
        async for _ in scanner.scan():
            pass

    asyncio.run(scan_once())

    # Change the directory's contents → mtime + nlink may shift.
    (target / "b.bin").write_bytes(b"y" * 1024)

    scanner2 = Scanner(tmp_path, trail_store=store, trail_threshold_mb=0)

    async def scan_again() -> None:
        async for _ in scanner2.scan():
            pass

    asyncio.run(scan_again())

    # Cache must have recognized the mismatch and re-walked.
    assert scanner2.cache_misses >= 1
    store.close()
