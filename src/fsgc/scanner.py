import asyncio
import logging
import os
import random
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from fsgc.behavior import (
    BehavioralMatch,
    BehavioralRule,
    BehavioralRuleManager,
    BehavioralSignal,
)
from fsgc.config import Signature
from fsgc.trail import TopChild, TrailRecord, TrailStore, calculate_fingerprint

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
    top_subdirs: list[TopChild] = field(default_factory=list)
    children: dict[str, "DirectoryNode"] = field(default_factory=dict)
    is_dir: bool = True
    # Enhanced Metadata for Incremental Scan
    cached_size: int = 0
    cached_hash: int = 0
    is_processed: bool = False
    entry_count: int = 0
    completion_ratio: float = 0.0
    # Fingerprint of this directory's own (mtime, st_nlink), captured once
    # during _process_directory and reused by persist_trail. The trail short-
    # circuit on the next scan compares against this same value.
    fingerprint: int = 0

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
        trail_store: TrailStore | None = None,
        trail_threshold_mb: int = 0,
        budget_seconds: float | None = None,
        behavioral_manager: BehavioralRuleManager | None = None,
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
        self.trail_store = trail_store
        self.trail_threshold_mb = trail_threshold_mb
        # Diagnostic counters — let tests/users see how much the cache saved.
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        # Wall-clock budget for the scan phase. None means no cap (`--full`).
        # The deadline is captured in `scan()` so the timer starts when the
        # worker pool spins up, not when the Scanner is constructed.
        self.budget_seconds: float | None = budget_seconds
        self._deadline: float | None = None
        self.timed_out: bool = False
        self.behavioral_manager = behavioral_manager
        self.behavioral_matches: list[BehavioralMatch] = []

    def _get_dev(self, path: Path) -> int:
        try:
            return os.stat(path).st_dev
        except (PermissionError, FileNotFoundError):
            return -1

    def select_node(self, node: DirectoryNode) -> DirectoryNode | None:
        """
        Select the most promising child node using a multi-tier heuristic:
        1. Tier 1: Exact signature match (highest recovery cap first).
        1.5 Tier 1.5: Signature-derived directory prior — children whose name
            appears as a literal component of any signature's pattern
            (e.g. ``.cache``, ``.config``, ``node_modules``).
        2. Tier 2: Historical trash density (score×size from the previous scan).
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

        # Tier 1.5: Signature-derived directory prior — go to the children
        # whose names appear in the catalog's literal path components first.
        # Catches the cold-cache case where the trail tier (tier 2) is empty
        # but we still want MCTS to rush to .cache, .config, etc.
        if self.engine and getattr(self.engine, "directory_priors", None):
            priors = self.engine.directory_priors
            best_prior = 0.0
            best_prior_child: DirectoryNode | None = None
            for child in available_children:
                prior = priors.get(child.path.name, 0.0)
                if prior > best_prior or (
                    prior == best_prior
                    and best_prior_child is not None
                    and child.estimated_size > best_prior_child.estimated_size
                ):
                    best_prior = prior
                    best_prior_child = child
            if best_prior_child is not None and best_prior > 0.0:
                return best_prior_child

        # Tier 2: Trail-derived trash density (score × size from previous scan).
        # Where there was the most garbage last time, go again first.
        if node.top_subdirs:
            trash_density = {sub.name: max(sub.score, 0.01) * sub.size for sub in node.top_subdirs}
            tier2_candidates = [c for c in available_children if c.path.name in trash_density]
            if tier2_candidates:
                return max(tier2_candidates, key=lambda x: trash_density.get(x.path.name, 0))

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
        # Propagate visits and check for FINISHED state. Persist whenever a
        # node is fully-explored — `_process_directory` already flips that
        # bit for leaf-only dirs, so the old "just-became-explored" guard
        # silently dropped those persists on the floor. Idempotent: re-writing
        # the same record is cheap and lets multi-iteration nodes keep their
        # trails fresh as more children get rolled up.
        for node in reversed(path):
            node.visits += 1
            node.update_metadata()
            if node.is_fully_explored:
                await self.persist_trail(node)

    async def scan(self) -> AsyncGenerator[DirectoryNode, None]:
        """
        Perform an informed MCTS scan of the filesystem and yield tree snapshots.

        If ``budget_seconds`` is set, the scan stops once the deadline passes
        (checked between MCTS iterations). The partial tree is yielded one
        final time; ``self.timed_out`` is True. Trail persistence is gated
        on ``node.is_fully_explored`` (existing behavior), so partial subtrees
        never pollute the cache — next run continues where this one stopped,
        guided by the same priors.
        """
        root_node = DirectoryNode(path=self.root)
        if self.engine:
            root_node.signature = self.engine.get_matching_signature(root_node, self.signatures)
        self.tree = root_node
        self.path_to_node[self.root] = root_node
        self.visited.add(os.path.realpath(self.root))

        if self.budget_seconds is not None and self.budget_seconds > 0:
            # Monotonic so NTP slew can't extend or curtail the budget.
            self._deadline = time.monotonic() + self.budget_seconds
        else:
            self._deadline = None
        self.timed_out = False

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
                    if self._deadline is not None and time.monotonic() > self._deadline:
                        self.timed_out = True
                        return
                    while not node.is_fully_explored and iterations < max_iterations:
                        if self._deadline is not None and time.monotonic() > self._deadline:
                            self.timed_out = True
                            return
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
                # End the yield loop early on budget exhaustion so the live UI
                # snaps to its final state without waiting for the queue to
                # drain through the workers' graceful exits.
                if self._deadline is not None and time.monotonic() > self._deadline:
                    self.timed_out = True
                    break
                if not done:
                    yield root_node
        finally:
            for w in worker_tasks:
                w.cancel()

        self._finalize_behavioral_matches()
        yield root_node

    async def _process_directory(self, node: DirectoryNode) -> None:
        """
        Scan a single directory level and update node metadata.

        When a trail_store is wired and the cached fingerprint matches
        the current ``(mtime, entry_count)`` of the directory, the walk
        is skipped entirely — the node is hydrated from the trail and
        marked fully-explored. This is where the sub-second second-run
        comes from.
        """
        try:
            # 1. Single os.stat — gives us mtime + nlink for the fingerprint.
            #    On cache hit, this is the only filesystem call we make.
            try:
                st = await asyncio.to_thread(os.stat, node.path)
            except (PermissionError, FileNotFoundError):
                return
            current_fp = calculate_fingerprint(st.st_mtime, st.st_nlink)
            node.fingerprint = current_fp

            # 2. Trail short-circuit: matching fingerprint means we trust the
            #    cache and skip walking entirely. This is the sub-second second-run
            #    win — for an unchanged 5 GB subtree, the next scan costs one
            #    stat call instead of tens of thousands of scandir/stat calls.
            #
            #    Trade-off: we don't detect new garbage created INSIDE an unchanged
            #    parent (the parent's mtime/nlink didn't change). For caches
            #    (where add/remove DOES update mtime) this is fine; for build
            #    outputs modified in place, run with `fsgc scan --no-cache` to
            #    force a full walk. The TTL also caps stale entries at 30 days.
            if self.trail_store is not None:
                cached = self.trail_store.get(node.path)
                if cached is not None and cached.fingerprint == current_fp:
                    # Roll the entire cached subtree size into files_size so the
                    # later update_metadata() pass in MCTS backprop recomputes
                    # confirmed_size = total_size (no children walked → sum=0).
                    # Without this, the recompute would zero out the restored
                    # values and propagate a negative delta to the parent —
                    # which surfaces as negative reclaimed bytes (and negative
                    # MB/s, since the speed UI subtracts current from history).
                    node.files_size = cached.total_size
                    node.cached_size = cached.total_size
                    node.size = cached.total_size
                    node.confirmed_size = cached.total_size
                    node.estimated_size = cached.total_size
                    node.atime = cached.atime
                    node.mtime = cached.mtime
                    node.entry_count = cached.entry_count
                    node.file_evidence = set(cached.file_evidence)
                    node.top_subdirs = list(cached.top_children)
                    node.is_processed = True
                    node.is_fully_explored = True
                    node.completion_ratio = 1.0
                    node.state = ScanState.FINISHED
                    self.cache_hits += 1
                    if self.engine:
                        node.signature = await asyncio.to_thread(
                            self.engine.get_matching_signature, node, self.signatures
                        )
                    # Replay any stale_dir matches recorded for this node.
                    for raw in cached.behavioral_matches:
                        self.behavioral_matches.append(
                            BehavioralMatch(
                                path=node.path,
                                rule_name=raw["rule_name"],
                                size_bytes=int(raw["size_bytes"]),
                                age_days=int(raw["age_days"]),
                            )
                        )
                    return
                self.cache_misses += 1

            # Behavioral stale_dir rules — one extra os.stat per candidate per rule.
            if self.behavioral_manager is not None:
                for rule in self.behavioral_manager.dir_rules:
                    await self._check_behavioral_dir_rule(node, rule)

            # 3. No cache hit — pay for the full scandir.
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
                        if self.behavioral_manager is not None:
                            self._check_behavioral_file_rules(entry_name, entry_path, stat)
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

    async def _check_behavioral_dir_rule(self, node: DirectoryNode, rule: BehavioralRule) -> None:
        """Apply a single stale_dir rule to a directory node."""
        if rule.signal is BehavioralSignal.GIT_HEAD_MTIME:
            head = node.path / ".git" / "HEAD"
            try:
                head_st = await asyncio.to_thread(os.stat, head)
            except (PermissionError, FileNotFoundError):
                return
            age_seconds = time.time() - head_st.st_mtime
            if age_seconds < rule.min_age_days * 86400:
                return
            # size_bytes is provisional — it'll be rewritten in the post-scan
            # finalize pass once the subtree is fully walked.
            self.behavioral_matches.append(
                BehavioralMatch(
                    path=node.path,
                    rule_name=rule.name,
                    size_bytes=node.size,
                    age_days=int(age_seconds / 86400),
                )
            )

    def _check_behavioral_file_rules(self, name: str, path: Path, stat: os.stat_result) -> None:
        """Apply every stale_file rule to a single file entry."""
        assert self.behavioral_manager is not None  # noqa: S101
        for rule in self.behavioral_manager.file_rules:
            if rule.min_size_bytes and stat.st_size < rule.min_size_bytes:
                continue
            if rule.extensions and not any(name.endswith(ext) for ext in rule.extensions):
                continue
            if rule.path_scope and not path.match(rule.path_scope):
                continue
            age_seconds = time.time() - stat.st_mtime
            if age_seconds < rule.min_age_days * 86400:
                continue
            self.behavioral_matches.append(
                BehavioralMatch(
                    path=path,
                    rule_name=rule.name,
                    size_bytes=stat.st_size,
                    age_days=int(age_seconds / 86400),
                )
            )

    def _finalize_behavioral_matches(self) -> None:
        """
        Re-stat stale_dir match sizes from the walked tree. During detection
        we wrote a provisional node.size; by scan end it's been rolled up.
        """
        for i, match in enumerate(self.behavioral_matches):
            node = self.path_to_node.get(match.path)
            if node is not None:
                self.behavioral_matches[i] = BehavioralMatch(
                    path=match.path,
                    rule_name=match.rule_name,
                    size_bytes=node.size,
                    age_days=match.age_days,
                )

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

    async def persist_trail(self, node: DirectoryNode, threshold_mb: int | None = None) -> None:
        """
        Write the node's trail record into the central TrailStore.

        We persist every successfully-walked directory above the size
        threshold (default 10 MB) — much more aggressive than the old
        100 MB cutoff because the cache lives in one beaver file, not
        scattered on disk, so it costs us nothing to record more.
        """
        if self.trail_store is None:
            return
        if node.state != ScanState.FINISHED:
            return
        effective_threshold = threshold_mb if threshold_mb is not None else self.trail_threshold_mb
        if node.size < effective_threshold * 1024 * 1024:
            return

        # Top children by trash density: score × size. Children that match a
        # signature score the highest; non-garbage children fall back to a
        # tiny epsilon so they still register relative to each other.
        scored: list[tuple[DirectoryNode, float]] = []
        for child in node.children.values():
            score = 0.0
            if self.engine and child.signature is not None:
                score = self.engine.calculate_score(child, child.signature)
            scored.append((child, score))

        scored.sort(key=lambda pair: max(pair[1], 0.01) * pair[0].size, reverse=True)
        top_children = [
            TopChild(name=c.path.name, score=score, size=c.size) for c, score in scored[:10]
        ]
        node.top_subdirs = top_children

        # Behavioral stale_dir matches that point at this exact node.
        this_node_matches = [
            {
                "rule_name": m.rule_name,
                "size_bytes": m.size_bytes,
                "age_days": m.age_days,
            }
            for m in self.behavioral_matches
            if m.path == node.path
        ]

        # Use the fingerprint captured in _process_directory — must agree
        # exactly with what the next scan will recompute via os.stat.
        record = TrailRecord(
            scanned_at=node.mtime,
            fingerprint=node.fingerprint,
            total_size=node.size,
            entry_count=node.entry_count,
            atime=node.atime,
            mtime=node.mtime,
            file_evidence=list(node.file_evidence),
            top_children=top_children,
            behavioral_matches=this_node_matches,
        )
        # TrailStore's put goes to an in-memory dict; the bulk flush to
        # beaver happens once at close() so we never contend mid-scan.
        self.trail_store.put(node.path, record)
