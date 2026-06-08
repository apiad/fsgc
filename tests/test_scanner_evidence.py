from pathlib import Path

import pytest

from fsgc.config import Recovery, Signature
from fsgc.engine import HeuristicEngine
from fsgc.scanner import DirectoryNode, Scanner


def test_directory_node_file_evidence():
    node = DirectoryNode(path=Path("/mock/test"))
    # This should work once we add the field
    assert hasattr(node, "file_evidence")
    assert isinstance(node.file_evidence, set)


@pytest.mark.asyncio
async def test_scanner_collects_evidence(tmp_path):
    # Create a directory with some files
    test_dir = tmp_path / "project"
    test_dir.mkdir()
    (test_dir / "main.c").touch()
    (test_dir / "main.o").touch()
    (test_dir / "Makefile").touch()
    (test_dir / "sub").mkdir()

    scanner = Scanner(root=tmp_path)
    # We need to process the directory
    node = DirectoryNode(path=test_dir)
    await scanner._process_directory(node)

    assert "main.c" in node.file_evidence
    assert ".c" in node.file_evidence
    assert "main.o" in node.file_evidence
    assert ".o" in node.file_evidence
    assert "Makefile" in node.file_evidence
    # Subdirectory names should NOT be in file_evidence based on plan
    assert "sub" not in node.file_evidence


@pytest.mark.asyncio
async def test_scanner_filters_evidence_with_engine(tmp_path):
    # Create a directory with some files
    test_dir = tmp_path / "project"
    test_dir.mkdir()
    (test_dir / "relevant.o").touch()
    (test_dir / "irrelevant.txt").touch()
    (test_dir / "package.json").touch()

    sig = Signature(
        name="test",
        pattern="**/project",
        recovery=Recovery.LOCAL,
        sentinels=["*.o", "package.json"],
    )
    engine = HeuristicEngine()
    # Initialize engine with signatures
    engine.get_matching_signature(DirectoryNode(path=test_dir), [sig])

    scanner = Scanner(root=tmp_path, engine=engine, signatures=[sig])
    node = DirectoryNode(path=test_dir)
    await scanner._process_directory(node)

    # With short-circuiting, we only collect at least ONE evidence.
    # Depending on os.scandir order, it could be relevant.o or package.json.
    assert len(node.file_evidence) >= 1
    # Check that at least one of the relevant ones is there
    found_any = (
        "relevant.o" in node.file_evidence
        or ".o" in node.file_evidence
        or "package.json" in node.file_evidence
    )
    assert found_any
    # Irrelevant ones should still NOT be there
    assert "irrelevant.txt" not in node.file_evidence
    assert ".txt" not in node.file_evidence
