from pathlib import Path

from fsgc.aggregator import group_by_signature
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
