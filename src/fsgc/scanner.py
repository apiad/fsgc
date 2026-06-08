import asyncio
import logging
import os
import random
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from fsgc.config import Signature
from fsgc.trail import GCTrail, TopSubdirectory

logger = logging.getLogger(__name__)


class ScanState(Enum):
    """
    The scanning state of a directory node.
    """

    NONE = auto()  # No status set
    ENQUEUED = auto()  # Not yet scanned or currently unverified
    EXPLORING = auto()  # Currently being descended in an MCTS iteration
    FINISHED = auto()  # Verified on disk AND entire subtree is fully explored


@dataclass(order=True)
class PrioritizedPath:
    """
    A path with a priority score for the Global Priority Queue.
    Lower score means higher priority.
    """

    priority: int
    path: Path = field(compare=False)


@dataclass
class DirectoryNode:
    """
    A node in the directory tree that aggregates sizes and timestamps.
    """

    path: Path
    size: int = 0  # Total size (self + children)
    files_size: int = 0  # Sum of file sizes in this directory only
    atime: float = 0.0  # Most recent access time in this branch
    mtime: float = 0.0  # Most recent modification time in this branch
    state: ScanState = ScanState.NONE
    top_subdirs: list[TopSubdirectory] = field(default_factory=list)
    children: dict[str, "DirectoryNode"] = field(default_factory=dict)
    is_dir: bool = True
    # Enhanced Metadata for Incremental Scan
    cached_size: int = 0
    cached_hash: int = 0
    is_processed: bool = False
    entry_count: int = 0
    completion_ratio: float = 0.0

    # MCTS metrics
    visits: int = 0
    total_reward: float = 0.0
    total_time: float = 0.0
    confirmed_size: int = 0
    estimated_size: int = 0
    is_fully_explored: bool = False
    heuristic_score: float = 0.0
    signature: Signature | None = None
    file_evidence: set[str] = field(default_factory=set)

    parent: "DirectoryNode | None" = field(default=None, repr=False)

    # Internal counters for incremental propagation
    _sum_child_confirmed_size: int = 0
    _sum_child_estimated_size: int = 0
    _sum_child_completion_ratio: float = 0.0
    _unexplored_children_count: int = 0

    def __hash__(self) -> int:
        return hash(str(self.path))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DirectoryNode):
            return False
        return self.path == other.path

    def add_child(self, name: str, node: "DirectoryNode") -> None:
        self.children[name] = node
        node.parent = self
        if not node.is_fully_explored:
            self._unexplored_children_count += 1

    def update_metadata(self) -> None:
        """
        Recalculate local totals based on current internal state and counters,
        then propagate deltas to parent.
        """
        old_confirmed = self.confirmed_size
        old_estimated = self.estimated_size
        old_ratio = self.completion_ratio

        # 1. Size calculation
        self.confirmed_size = self.files_size + self._sum_child_confirmed_size
        self.size = self.confirmed_size  # UI Compatibility

        # Estimated size uses counters + local files + fallback to cached
        est = self.files_size + self._sum_child_estimated_size
        self.estimated_size = max(est, self.cached_size)

        # 2. Ratio calculation
        total_ratio_sum = (1.0 if self.is_processed else 0.0) + self._sum_child_completion_ratio
        items_count = len(self.children) + 1
        self.completion_ratio = total_ratio_sum / items_count

        # 3. State calculation
        became_fully_explored = False
        if not self.is_fully_explored:
            if self.is_processed and self._unexplored_children_count == 0:
                self.is_fully_explored = True
                became_fully_explored = True

        if self.is_fully_explored:
            self.state = ScanState.FINISHED

        # 4. Propagate if parent exists
        if self.parent:
            delta_confirmed = self.confirmed_size - old_confirmed
            delta_estimated = self.estimated_size - old_estimated
            # Ratio delta needs to be normalized by parent's items_count?
            # No, parent stores _sum_child_completion_ratio as raw sum.
            delta_ratio = self.completion_ratio - old_ratio

            self.parent.propagate_child_update(
                delta_confirmed=delta_confirmed,
                delta_estimated=delta_estimated,
                delta_ratio=delta_ratio,
                became_fully_explored=became_fully_explored,
                atime=self.atime,
                mtime=self.mtime,
            )

    def propagate_child_update(
        self,
        delta_confirmed: int,
        delta_estimated: int,
        delta_ratio: float,
        became_fully_explored: bool,
        atime: float,
        mtime: float,
    ) -> None:
        """
        Update internal counters based on child's delta and trigger local update.
        """
        self._sum_child_confirmed_size += delta_confirmed
        self._sum_child_estimated_size += delta_estimated
        self._sum_child_completion_ratio += delta_ratio

        if became_fully_explored:
            self._unexplored_children_count -= 1

        self.atime = max(self.atime, atime)
        self.mtime = max(self.mtime, mtime)

        self.update_metadata()

    def calculate_metadata(self) -> tuple[int, float, float, bool, float]:
        """
        Returns cached metadata fields (updated via incremental propagation).
        Returns (size, atime, mtime, is_complete, completion_ratio).
        """
        return (
            self.confirmed_size,
            self.atime,
            self.mtime,
            self.is_fully_explored,
            self.completion_ratio,
        )


class Scanner:
    """
    A stochastic scanner that uses a priority queue and async workers
    to discover high-value directories first.
    """

    # Static Priors: Lower is higher priority
    STATIC_PRIORS = {
        ".cache": 10,
        "Downloads": 20,
        "node_modules": 5,
        ".git": 50,
        "build": 15,
        "dist": 15,
        "target": 15,
        "bin": 30,
        "obj": 30,
    }

    def __init__(
        self,
        root: Path,
        stay_on_mount: bool = True,
        engine: "Any" = None,
        signatures: list[Signature] | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.root = root.resolve()
        self.stay_on_mount = stay_on_mount
        self.root_dev = self._get_dev(self.root)
        self.tree: DirectoryNode | None = None
        self.visited: set[str] = set()
        self.path_to_node: dict[Path, DirectoryNode] = {}
        self.engine = engine
        self.signatures = signatures or []
        self.max_concurrency = max_concurrency

    def _get_dev(self, path: Path) -> int:
        try:
            return os.stat(path).st_dev
        except (PermissionError, FileNotFoundError):
            return -1

    def select_node(self, node: DirectoryNode) -> DirectoryNode | None:
        """
        Select the most promising child node using a two-tiered heuristic:
        1. Tier 1: Preconfigured folder patterns with priorities (High-value targets).
        2. Tier 2: Historical data from .gctrail (Historical hotspots).
        Fallback: Greedy largest estimated size, prioritizing unvisited.
        """
        # Filter out fully explored children
        available_children = [c for c in node.children.values() if not c.is_fully_explored]

        if not available_children:
            node.is_fully_explored = True
            node.state = ScanState.FINISHED
            return None

        # Tier 1: Signatures (Known Garbage Patterns)
        # Prefer exploring children whose signature has the highest recovery cap
        # (trivial-rebuild caches first — they're the safest, biggest scoring opportunity).
        if self.signatures:
            best_cap = -1.0
            best_tier1 = None
            for child in available_children:
                # Use cached signature
                sig = child.signature
                if sig and sig.recovery_cap > best_cap:
                    best_cap = sig.recovery_cap
                    best_tier1 = child

            if best_tier1:
                return best_tier1

        # Tier 2: Trail Data (Large subdirectories from previous scans)
        if node.top_subdirs:
            # Create a lookup for children that match trail names
            trail_names = {sub.name: sub.size for sub in node.top_subdirs}
            tier2_candidates = [c for c in available_children if c.path.name in trail_names]
            if tier2_candidates:
                # Return the candidate that was historically largest
                return max(tier2_candidates, key=lambda x: trail_names.get(x.path.name, 0))

        # Fallback: Greedy largest estimated size, prioritizing unvisited
        unvisited = [c for c in available_children if c.visits == 0]
        if unvisited:
            return random.choice(unvisited)  # noqa: S311

        best_score = -1.0
        best_child = None

        for child in available_children:
            score = child.estimated_size

            if score > best_score:
                best_score = score
                best_child = child

        return best_child or available_children[0]

    async def mcts_iteration(self, root: DirectoryNode) -> None:
        """
        Perform one MCTS iteration: a complete playout from root to leaf.
        """
        path = [root]
        current = root

        while True:
            # 1. Verification (Expansion if needed)
            if not current.is_processed:
                await self._process_directory(current)

            # Mark as exploring
            current.state = ScanState.EXPLORING

            # 2. Termination Check
            if not current.children or current.is_fully_explored:
                break

            # 3. Selection (Move deeper)
            # Offload heuristic selection to thread as it can be CPU bound for wide dirs
            next_node = await asyncio.to_thread(self.select_node, current)
            if next_node is None or next_node == current:
                break

            current = next_node
            path.append(current)

        # 4. Backpropagation (State & Trail Persistence)
        # Propagate visits and check for FINISHED state
        for node in reversed(path):
            node.visits += 1
            old_fully_explored = node.is_fully_explored

            node.update_metadata()

            # If the node just became fully explored, persist its trail
            if not old_fully_explored and node.is_fully_explored:
                await self.persist_trail(node)

    async def scan(self) -> AsyncGenerator[DirectoryNode, None]:
        """
        Perform an informed MCTS scan of the filesystem and yield tree snapshots.
        """
        root_node = DirectoryNode(path=self.root)
        if self.engine:
            root_node.signature = self.engine.get_matching_signature(root_node, self.signatures)
        self.tree = root_node
        self.path_to_node[self.root] = root_node
        self.visited.add(os.path.realpath(self.root))

        # Initial expansion of root
        await self._process_directory(root_node)

        queue: asyncio.Queue[DirectoryNode] = asyncio.Queue()

        # Seed the queue with top-level subdirectories
        for child in root_node.children.values():
            queue.put_nowait(child)

        async def worker() -> None:
            while True:
                node = await queue.get()
                iterations = 0
                max_iterations = 50

                try:
                    while not node.is_fully_explored and iterations < max_iterations:
                        await self.mcts_iteration(node)
                        iterations += 1

                    if not node.is_fully_explored:
                        # Find unexplored children to partition the work
                        unexplored_children = [
                            c for c in node.children.values() if not c.is_fully_explored
                        ]
                        if unexplored_children:
                            for c in unexplored_children:
                                queue.put_nowait(c)
                        else:
                            # Edge case: node is not explored but has no unexplored children
                            # (could happen if children haven't been discovered yet)
                            queue.put_nowait(node)
                except Exception as e:
                    logger.error(f"Worker error on {node.path}: {e}")
                finally:
                    queue.task_done()

        worker_tasks = [asyncio.create_task(worker()) for _ in range(self.max_concurrency)]
        queue_task = asyncio.create_task(queue.join())

        yield_interval = 0.1  # 100ms

        try:
            while not queue_task.done():
                done, pending = await asyncio.wait([queue_task], timeout=yield_interval)
                if not done:
                    yield root_node
        finally:
            for w in worker_tasks:
                w.cancel()

        yield root_node

    async def _process_directory(self, node: DirectoryNode) -> None:
        """
        Scan a single directory level and update node metadata.
        """
        try:
            # 1. Trail Logic (Fast-path if hash matches)
            trail_path = node.path / ".gctrail"
            if trail_path.exists():
                try:
                    data = await asyncio.to_thread(trail_path.read_bytes)
                    trail = GCTrail.from_bytes(data)
                    node.cached_size = trail.total_size
                    node.cached_hash = trail.structural_hash
                    node.top_subdirs = trail.top_subdirs
                    node.estimated_size = trail.total_size
                    # Check for quick verification (offloaded to thread)
                    await asyncio.to_thread(os.stat, node.path)
                    await asyncio.to_thread(os.listdir, node.path)
                except Exception as e:
                    logger.debug(f"Failed to load trail at {trail_path}: {e}")

            # 2. Directory Exploration
            entries = await asyncio.to_thread(self._get_entries, node.path)
            node.entry_count = len(entries)

            for entry_name, entry_path, is_dir, stat in entries:
                if self.stay_on_mount and self._get_dev(entry_path) != self.root_dev:
                    continue

                if is_dir:
                    real_path = os.path.realpath(entry_path)
                    if real_path not in self.visited:
                        self.visited.add(real_path)
                        child_node = DirectoryNode(path=entry_path)
                        if self.engine:
                            # Offload signature matching
                            child_node.signature = await asyncio.to_thread(
                                self.engine.get_matching_signature, child_node, self.signatures
                            )
                        node.add_child(entry_name, child_node)
                        self.path_to_node[entry_path] = child_node
                else:
                    if stat:
                        node.files_size += stat.st_size
                        node.atime = max(node.atime, stat.st_atime)
                        node.mtime = max(node.mtime, stat.st_mtime)
                        # Collect evidence (Only if potentially relevant to sentinels)
                        # Optimization: once we have some evidence, we don't need to collect
                        # more for this dir.
                        if not node.file_evidence and self.engine:
                            if self.engine.is_relevant_evidence(entry_name):
                                node.file_evidence.add(entry_name)
                            path_entry = Path(entry_name)
                            suffix = path_entry.suffix
                            if suffix and self.engine.is_relevant_evidence(suffix):
                                node.file_evidence.add(suffix)
                        elif not self.engine:
                            node.file_evidence.add(entry_name)
                            path_entry = Path(entry_name)
                            if path_entry.suffix:
                                node.file_evidence.add(path_entry.suffix)

            node.state = ScanState.ENQUEUED
            node.is_processed = True

            # Re-match signature after evidence collection (offloaded)
            if self.engine:
                node.signature = await asyncio.to_thread(
                    self.engine.get_matching_signature, node, self.signatures
                )

            # Initial metadata sync
            node.update_metadata()

        except (PermissionError, FileNotFoundError) as e:
            logger.debug(f"Skipping {node.path}: {e}")

    def _get_entries(self, path: Path) -> list[tuple[str, Path, bool, os.stat_result | None]]:
        """
        Blocking call to scan a directory and return metadata for its entries.
        """
        results = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        results.append((entry.name, Path(entry.path), is_dir, stat))
                    except (PermissionError, FileNotFoundError):
                        results.append((entry.name, Path(entry.path), is_dir, None))
        except (PermissionError, FileNotFoundError):
            pass
        return results

    async def persist_trail(self, node: DirectoryNode, threshold_mb: int = 100) -> None:
        """
        Recursively save .gctrail files for directories larger than threshold_mb.
        """
        # Only persist for verified nodes that meet the size threshold
        if node.state == ScanState.FINISHED and node.size > threshold_mb * 1024 * 1024:
            # Calculate top 10 subdirectories
            children = sorted(node.children.values(), key=lambda x: x.size, reverse=True)
            node.top_subdirs = [
                TopSubdirectory(name=child.path.name, size=child.size) for child in children[:10]
            ]

            trail = GCTrail(
                timestamp=node.mtime,
                structural_hash=GCTrail.calculate_structural_hash(node.mtime, node.entry_count),
                total_size=node.size,
                reconstructible_size=0,
                noise_size=0,
                top_subdirs=node.top_subdirs,
            )

            trail_path = node.path / ".gctrail"
            try:
                trail_path.write_bytes(trail.to_bytes())
                logger.debug(f"Persisted trail to {trail_path}")
            except (PermissionError, OSError) as e:
                logger.debug(f"Failed to persist trail to {trail_path}: {e}")
