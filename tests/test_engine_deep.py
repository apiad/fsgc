import asyncio
import os
import time

from fsgc.config import Recovery, Signature
from fsgc.engine import HeuristicEngine
from fsgc.scanner import Scanner


def test_deep_nested_matching(tmp_path):
    # Setup deep structure:
    # tmp_path/
    #   .venv/ (match)
    #   subdir/
    #     .venv/ (match)
    #     deep/
    #       .venv/ (match)

    paths = [
        tmp_path / ".venv",
        tmp_path / "subdir" / ".venv",
        tmp_path / "subdir" / "deep" / ".venv",
        tmp_path / ".cache" / "uv",
    ]

    for p in paths:
        p.mkdir(parents=True)
        # Create a file inside to give it size and force older atime
        f = p / "data"
        f.write_bytes(b"x" * 1024)
        # Set old access time (100 days ago)
        old_time = time.time() - (100 * 24 * 60 * 60)
        os.utime(f, (old_time, old_time))
        os.utime(p, (old_time, old_time))

    scanner = Scanner(tmp_path)

    async def run_full_scan():
        async for _ in scanner.scan():
            pass
        return scanner.tree

    root_node = asyncio.run(run_full_scan())

    sigs = [
        Signature(
            name="Python Virtualenv",
            pattern="**/.venv",
            recovery=Recovery.NETWORK,
            min_age_days=14,
        ),
        Signature(
            name="uv Cache",
            pattern="**/.cache/uv",
            recovery=Recovery.NETWORK,
            min_age_days=7,
        ),
    ]
    engine = HeuristicEngine()

    scores = engine.apply_scoring(root_node, sigs)

    # We expect 4 matches
    matched_paths = {str(node.path) for node in scores.keys()}

    print(f"Matched paths: {matched_paths}")

    assert str(tmp_path / ".venv") in matched_paths
    assert str(tmp_path / "subdir" / ".venv") in matched_paths
    assert str(tmp_path / "subdir" / "deep" / ".venv") in matched_paths
    assert str(tmp_path / ".cache" / "uv") in matched_paths
    assert len(scores) == 4
