from pathlib import Path

from fsgc.config import Recovery, Signature
from fsgc.engine import HeuristicEngine
from fsgc.scanner import DirectoryNode


def test_engine_rejects_signature_if_sentinel_missing():
    engine = HeuristicEngine()
    sig = Signature(
        name="Build",
        pattern="**/build",
        recovery=Recovery.LOCAL,
        sentinels=["*.o", "Makefile"],
    )

    # Node matching pattern but NO evidence
    node = DirectoryNode(path=Path("/mock/myproject/build"))
    node.file_evidence = {"main.c", ".c"}  # No .o, no Makefile

    assert engine.get_matching_signature(node, [sig]) is None


def test_engine_accepts_signature_if_sentinel_present():
    engine = HeuristicEngine()
    sig = Signature(
        name="Build",
        pattern="**/build",
        recovery=Recovery.LOCAL,
        sentinels=["*.o", "Makefile"],
    )

    # Node matching pattern AND has .o
    node = DirectoryNode(path=Path("/mock/myproject/build"))
    node.file_evidence = {"main.o", ".o", "main.c"}

    matched = engine.get_matching_signature(node, [sig])
    assert matched == sig


def test_engine_accepts_signature_if_no_sentinels_defined():
    engine = HeuristicEngine()
    sig = Signature(name="Build", pattern="**/build", recovery=Recovery.LOCAL, sentinels=[])

    node = DirectoryNode(path=Path("/mock/myproject/build"))
    node.file_evidence = set()

    matched = engine.get_matching_signature(node, [sig])
    assert matched == sig
