import time
from pathlib import Path
from unittest.mock import patch

from fsgc.config import RECOVERY_CAP, Recovery, Signature
from fsgc.engine import HeuristicEngine
from fsgc.scanner import DirectoryNode


def test_score_is_age_factor_times_recovery_cap_for_network_signature() -> None:
    """An ancient network-recovery node should hit the network cap (0.4) exactly."""
    engine = HeuristicEngine(age_threshold_days=90)
    ancient = engine.now - (180 * 24 * 60 * 60)  # 180 days, past the cap
    node = DirectoryNode(path=Path("node_modules"), size=1000, atime=ancient, mtime=ancient)
    sig = Signature(name="Node", pattern="node_modules", recovery=Recovery.NETWORK)

    score = engine.calculate_score(node, sig)

    assert score == RECOVERY_CAP[Recovery.NETWORK]  # 0.4


def test_score_is_age_factor_times_recovery_cap_for_trivial_signature() -> None:
    """An ancient trivial-recovery node hits the maximum score (1.0)."""
    engine = HeuristicEngine(age_threshold_days=90)
    ancient = engine.now - (180 * 24 * 60 * 60)
    node = DirectoryNode(path=Path("__pycache__"), size=100, atime=ancient, mtime=ancient)
    sig = Signature(name="Pycache", pattern="**/__pycache__", recovery=Recovery.TRIVIAL)

    score = engine.calculate_score(node, sig)

    assert score == 1.0


def test_score_scales_linearly_with_age_below_threshold() -> None:
    """A half-old trivial cache scores half the cap."""
    engine = HeuristicEngine(age_threshold_days=90)
    half_old = engine.now - (45 * 24 * 60 * 60)
    node = DirectoryNode(path=Path("x"), size=100, atime=half_old, mtime=half_old)
    sig = Signature(name="X", pattern="x", recovery=Recovery.TRIVIAL)

    score = engine.calculate_score(node, sig)

    assert 0.49 < score < 0.51  # ~0.5


def test_score_zero_when_younger_than_min_age_days() -> None:
    engine = HeuristicEngine(age_threshold_days=90)
    fresh = engine.now - (3 * 24 * 60 * 60)  # 3 days
    node = DirectoryNode(path=Path("x"), size=100, atime=fresh, mtime=fresh)
    sig = Signature(name="X", pattern="x", recovery=Recovery.TRIVIAL, min_age_days=7)

    assert engine.calculate_score(node, sig) == 0.0


def test_score_uses_max_of_atime_and_mtime() -> None:
    """noatime mounts mean atime can be stale; mtime must rescue."""
    engine = HeuristicEngine(age_threshold_days=90)
    ancient_atime = engine.now - (365 * 24 * 60 * 60)
    fresh_mtime = engine.now - (1 * 24 * 60 * 60)
    node = DirectoryNode(path=Path("x"), size=100, atime=ancient_atime, mtime=fresh_mtime)
    sig = Signature(name="X", pattern="x", recovery=Recovery.TRIVIAL)

    score = engine.calculate_score(node, sig)

    # fresh mtime should dominate → very low age_factor → near-zero score
    assert score < 0.05


def test_score_zero_when_no_signature() -> None:
    engine = HeuristicEngine()
    node = DirectoryNode(path=Path("src"), size=1000, atime=time.time())
    assert engine.calculate_score(node, None) == 0.0


def test_recovery_ordering_trivial_beats_local_beats_network() -> None:
    """An ancient node scores by its recovery tier alone."""
    engine = HeuristicEngine(age_threshold_days=90)
    ancient = engine.now - (180 * 24 * 60 * 60)

    def score_for(recovery: Recovery) -> float:
        node = DirectoryNode(path=Path("x"), size=1, atime=ancient, mtime=ancient)
        sig = Signature(name="X", pattern="x", recovery=recovery)
        return engine.calculate_score(node, sig)

    assert score_for(Recovery.TRIVIAL) > score_for(Recovery.LOCAL) > score_for(Recovery.NETWORK)


def test_apply_scoring_returns_matched_nodes() -> None:
    engine = HeuristicEngine()
    ancient = engine.now - (180 * 24 * 60 * 60)
    root = DirectoryNode(path=Path("root"), size=1000)
    node1 = DirectoryNode(path=Path("node_modules"), size=500, atime=ancient, mtime=ancient)
    root.add_child("node_modules", node1)
    sig = Signature(name="Node", pattern="node_modules", recovery=Recovery.NETWORK)

    scores = engine.apply_scoring(root, [sig])

    assert len(scores) == 1
    assert node1 in scores
    assert scores[node1][1] == sig


def test_engine_optimized_matching_uses_fast_path_for_simple_patterns() -> None:
    engine = HeuristicEngine()
    sigs = [
        Signature(name="Simple", pattern="**/node_modules", recovery=Recovery.NETWORK),
        Signature(
            name="Complex",
            pattern="**/google-chrome-backup-crashrecovery*",
            recovery=Recovery.TRIVIAL,
        ),
    ]

    # Simple pattern bypasses Path.match()
    node1 = DirectoryNode(path=Path("/home/user/node_modules"))
    with patch.object(Path, "match", wraps=node1.path.match) as mock_match:
        sig = engine.get_matching_signature(node1, sigs)
        assert sig is not None
        assert sig.name == "Simple"
        assert mock_match.call_count == 0

    # Complex pattern still uses Path.match()
    node2 = DirectoryNode(path=Path("/home/user/google-chrome-backup-crashrecovery-123"))
    with patch.object(Path, "match", wraps=node2.path.match) as mock_match:
        sig = engine.get_matching_signature(node2, sigs)
        assert sig is not None
        assert sig.name == "Complex"
        assert mock_match.call_count > 0
