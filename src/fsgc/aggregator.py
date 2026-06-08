from typing import Any

from fsgc.config import Signature
from fsgc.scanner import DirectoryNode, ScanState


def summarize_tree(
    node: DirectoryNode,
    max_depth: int,
    min_percent: float = 0.05,
    max_children: int = 4,
    current_depth: int = 0,
    min_size: int = 0,
    speed: float | None = None,
) -> dict[str, Any]:
    """
    Recursively summarize a DirectoryNode tree into a format suitable for TUI rendering.
    """
    summary: dict[str, Any] = {
        "name": node.path.name or str(node.path),
        "size": node.size,
        "estimated_size": node.estimated_size,
        "confirmed_size": node.confirmed_size,
        "state": node.state.name,
        "completion_ratio": node.completion_ratio,
        "is_others": False,
        "children": [],
        "speed": speed,
    }

    if current_depth >= max_depth or not node.children:
        return summary

    # Sort children by size
    sorted_children = sorted(node.children.values(), key=lambda x: x.size, reverse=True)

    keep_count = 0
    total_child_size_shown = 0

    children_list: list[dict[str, Any]] = []
    other_state = "FINISHED"
    others = 0
    other_completion = 1.0

    for child in sorted_children:
        # Check if we should keep this child individually
        percentage = child.size / node.size if node.size > 0 else 0

        should_keep = (
            keep_count < max_children and percentage >= min_percent and child.size >= min_size
        )

        if should_keep:
            child_summary = summarize_tree(
                child, max_depth, min_percent, max_children, current_depth + 1, min_size
            )
            children_list.append(child_summary)
            total_child_size_shown += child.size
            keep_count += 1
        else:
            others += 1

            # This child and all subsequent ones will be grouped in "Others"
            if child.state == ScanState.ENQUEUED and other_state == "DONE":
                other_state = "ENQUEUED"
            if child.state == ScanState.EXPLORING:
                other_state = "EXPLORING"

            other_completion = min(other_completion, child.completion_ratio)

    # Calculate "Others" size: (Total node size) - (Sizes of shown children)
    others_size = node.size - total_child_size_shown

    if others > 0:
        children_list.append(
            {
                "name": "Others",
                "size": others_size,
                "is_others": True,
                "children": [],
                "state": other_state,
                "completion_ratio": other_completion,
            }
        )

    summary["children"] = children_list

    if node.state == ScanState.EXPLORING:
        node.state = ScanState.ENQUEUED

    return summary


def group_by_signature(
    node_scores: dict[DirectoryNode, tuple[float, Signature]],
) -> list[dict[str, Any]]:
    """
    Group scored nodes by their matching signature name for interactive selection.

    Returns a list of groups:
    [
        {
            "name": "Signature Name",
            "size": total_size,
            "avg_score": avg_score,
            "nodes": [node1, node2, ...],
            "auto_check": bool
        },
        ...
    ]
    """
    groups: dict[str, dict[str, Any]] = {}

    for node, (score, signature) in node_scores.items():
        if signature.name not in groups:
            groups[signature.name] = {
                "name": signature.name,
                "signature": signature,
                "size": 0,
                "scores": [],
                "nodes": [],
                "auto_check": False,
            }

        group = groups[signature.name]
        group["size"] += node.size
        group["scores"].append(score)
        group["nodes"].append(node)

    result = []
    for group in groups.values():
        avg_score = sum(group["scores"]) / len(group["scores"])
        group["avg_score"] = avg_score
        group["auto_check"] = avg_score > 0.8
        del group["scores"]
        result.append(group)

    # Sort by avg_score (recovery tier × recency) descending, with total size as
    # the tiebreaker. Old + trivially-rebuilt surfaces first; large-but-fresh and
    # network-dependent groups sink to the bottom — matches the audit's three axes
    # (old + stale → easy-to-rebuild → needs-network).
    return sorted(result, key=lambda x: (x["avg_score"], x["size"]), reverse=True)
