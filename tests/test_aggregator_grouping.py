from pathlib import Path

from fsgc.aggregator import group_behavioral_matches, group_by_signature
from fsgc.behavior import BehavioralMatch
from fsgc.config import Recovery, Signature
from fsgc.scanner import DirectoryNode


def test_group_by_signature() -> None:
    node1 = DirectoryNode(path=Path("node_modules_1"), size=100)
    node2 = DirectoryNode(path=Path("node_modules_2"), size=200)

    sig = Signature(name="Node", pattern="node_modules", recovery=Recovery.NETWORK)

    node_scores = {node1: (0.9, sig), node2: (0.7, sig)}

    groups = group_by_signature(node_scores)

    assert len(groups) == 1
    assert groups[0]["name"] == "Node"
    assert groups[0]["size"] == 300
    assert groups[0]["avg_score"] == 0.8
    assert groups[0]["auto_check"] is False  # 0.8 is not > 0.8
    assert len(groups[0]["nodes"]) == 2


def test_group_behavioral_matches_groups_by_rule_name() -> None:
    matches = [
        BehavioralMatch(Path("/x/a"), "Old Download", 100, 95),
        BehavioralMatch(Path("/x/b"), "Old Download", 200, 120),
        BehavioralMatch(Path("/y/c"), "Stale Code Project", 1024, 200),
    ]
    groups = group_behavioral_matches(matches)

    assert len(groups) == 2
    by_name = {g["name"]: g for g in groups}
    assert by_name["Old Download"]["size"] == 300
    assert len(by_name["Old Download"]["matches"]) == 2
    assert by_name["Stale Code Project"]["size"] == 1024
    assert groups[0]["name"] == "Stale Code Project"
    assert all(g["auto_check"] is False for g in groups)
    assert all(g["review"] is True for g in groups)


def test_group_behavioral_matches_empty_when_no_matches() -> None:
    assert group_behavioral_matches([]) == []
