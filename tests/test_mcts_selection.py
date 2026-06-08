from pathlib import Path

from fsgc.config import Recovery, Signature
from fsgc.engine import HeuristicEngine
from fsgc.scanner import DirectoryNode, Scanner
from fsgc.trail import TopChild


def test_mcts_selection_prioritizes_larger_estimated_size():
    scanner = Scanner(Path("mock_dir"))
    root = DirectoryNode(path=Path("mock_dir"))

    child1 = DirectoryNode(path=Path("mock_dir/1"))
    child2 = DirectoryNode(path=Path("mock_dir/2"))

    root.add_child("1", child1)
    root.add_child("2", child2)

    # child2 is larger estimated_size
    child1.estimated_size = 100
    child2.estimated_size = 500
    child1.visits = 1
    child2.visits = 1

    selected = scanner.select_node(root)
    assert selected == child2


def test_mcts_selection_tier1_trail():
    scanner = Scanner(Path("mock_dir"))
    root = DirectoryNode(path=Path("mock_dir"))

    child1 = DirectoryNode(path=Path("mock_dir/1"))
    child2 = DirectoryNode(path=Path("mock_dir/2"))
    root.add_child("1", child1)
    root.add_child("2", child2)

    # Trail says child2 was larger
    root.top_subdirs = [TopChild(name="2", score=0.5, size=1000)]

    selected = scanner.select_node(root)
    assert selected == child2


def test_mcts_selection_uses_cached_signature():
    class MockEngine:
        def get_matching_signature(self, _node, _signatures):
            # This should NOT be called during select_node if caching is working
            raise RuntimeError("Engine should not be called during selection")

    engine = MockEngine()
    scanner = Scanner(Path("mock_dir"), engine=engine)
    root = DirectoryNode(path=Path("mock_dir"))

    child1 = DirectoryNode(path=Path("mock_dir/1"))
    child2 = DirectoryNode(path=Path("mock_dir/2"))
    root.add_child("1", child1)
    root.add_child("2", child2)

    # Set cached signatures
    # MCTS tier-1 now prefers signatures with the highest recovery cap (TRIVIAL > NETWORK)
    child1.signature = Signature(name="Low", pattern="**/1", recovery=Recovery.NETWORK)
    child2.signature = Signature(name="High", pattern="**/2", recovery=Recovery.TRIVIAL)
    scanner.signatures = [child1.signature, child2.signature]

    # Both have visits to bypass unvisited prioritization
    child1.visits = 1
    child2.visits = 1

    selected = scanner.select_node(root)
    assert selected == child2


def test_mcts_selection_tier1_signatures():
    # Setup scanner with signatures
    sigs = [
        Signature(name="High", pattern="**/high/", recovery=Recovery.TRIVIAL),
        Signature(name="Low", pattern="**/low/", recovery=Recovery.NETWORK),
    ]
    engine = HeuristicEngine()
    scanner = Scanner(Path("mock_dir"), engine=engine, signatures=sigs)
    root = DirectoryNode(path=Path("mock_dir"))

    child1 = DirectoryNode(path=Path("mock_dir/low/"))
    child2 = DirectoryNode(path=Path("mock_dir/high/"))
    root.add_child("low", child1)
    root.add_child("high", child2)

    # Pre-populate signatures (simulating what Scanner._process_directory does)
    child1.signature = sigs[1]
    child2.signature = sigs[0]

    selected = scanner.select_node(root)
    assert selected == child2
