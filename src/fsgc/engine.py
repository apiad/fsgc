"""
HeuristicEngine — scores DirectoryNodes against the signature catalog.

Score formula (rewritten 2026-06-08 to match Alex's three-axis intent):

    age_factor = min(1.0, max(0, max(atime, mtime) − now − minimum) / threshold)
    score      = age_factor × RECOVERY_CAP[signature.recovery]

Recency is the multiplier. Recovery tier is the cap. An ancient, trivially-
regenerated cache scores 1.0; an ancient network-fetched dep tree scores 0.4;
a young anything scores near zero. min_age_days is a hard cutoff applied
before scoring — younger nodes are filtered out entirely.

We use max(atime, mtime) because Linux mounts default to `noatime`, so atime
alone is unreliable. mtime tracks dir-content churn (add/remove), which is
the right signal for "is this cache still being used."
"""

import fnmatch
import time

from fsgc.config import RECOVERY_CAP, Signature
from fsgc.scanner import DirectoryNode


class HeuristicEngine:
    """
    Scores DirectoryNodes based on pattern matching, recency, and recovery cost.
    """

    def __init__(self, age_threshold_days: int = 90) -> None:
        self.age_threshold = age_threshold_days * 24 * 60 * 60  # seconds
        self.now = time.time()

        # Caching matchers to avoid redundant pattern analysis
        self._matchers: list[tuple[bool, str, Signature]] | None = None
        self._exact_sentinels: set[str] = set()
        self._glob_sentinels: list[str] = []

    def _get_matchers(self, signatures: list[Signature]) -> list[tuple[bool, str, Signature]]:
        """
        Analyze signatures and return a list of (is_simple, pattern, signature).
        'is_simple' means it can be matched by exact directory name.
        """
        matchers = []
        for sig in signatures:
            pattern = sig.pattern
            # Optimization: if pattern is "**/name" and contains no other globs, it's simple
            is_simple = False
            match_pattern = pattern
            if pattern.startswith("**/"):
                base_pattern = pattern[3:]
                # Optimization: if it's a single-level name without globs, it's simple
                if "/" not in base_pattern and not any(c in base_pattern for c in "*?[]"):
                    is_simple = True
                    match_pattern = base_pattern

            matchers.append((is_simple, match_pattern, sig))

            # Track all sentinels
            for sentinel in sig.sentinels:
                if any(c in sentinel for c in "*?[]"):
                    if sentinel not in self._glob_sentinels:
                        self._glob_sentinels.append(sentinel)
                else:
                    self._exact_sentinels.add(sentinel)

        return matchers

    def is_relevant_evidence(self, name: str) -> bool:
        """
        Check if a filename or suffix matches any sentinel defined in any signature.
        """
        if name in self._exact_sentinels:
            return True
        for glob in self._glob_sentinels:
            if fnmatch.fnmatch(name, glob):
                return True
        return False

    def _verify_sentinels(self, node: DirectoryNode, sig: Signature) -> bool:
        if not sig.sentinels:
            return True
        for sentinel in sig.sentinels:
            for ev in node.file_evidence:
                if fnmatch.fnmatch(ev, sentinel) or ev == sentinel:
                    return True
        return False

    def get_matching_signature(
        self, node: DirectoryNode, signatures: list[Signature]
    ) -> Signature | None:
        """
        Check if a node's path matches any signature pattern.
        Uses a fast-path for simple name-based patterns.
        """
        if self._matchers is None:
            self._matchers = self._get_matchers(signatures)

        for is_simple, pattern, sig in self._matchers:
            matched = False
            if is_simple:
                if node.path.name == pattern:
                    matched = True
            else:
                if node.path.match(sig.pattern):
                    matched = True

            if matched and self._verify_sentinels(node, sig):
                return sig

        return None

    def calculate_score(self, node: DirectoryNode, signature: Signature | None) -> float:
        """
        Score a matched node by age × recovery-cap. Returns 0 if too young or unmatched.
        """
        if not signature:
            return 0.0

        # Use the freshest of atime/mtime — noatime mounts make atime unreliable,
        # mtime tracks dir-content churn which is the right "is this still used" signal.
        last_touched = max(node.atime, node.mtime)
        age_seconds = self.now - last_touched
        min_age_seconds = signature.min_age_days * 24 * 60 * 60

        # Hard cutoff: too young to even consider
        if age_seconds < min_age_seconds:
            return 0.0

        age_factor = min(1.0, max(0.0, age_seconds / self.age_threshold))
        return age_factor * RECOVERY_CAP[signature.recovery]

    def apply_scoring(
        self, node: DirectoryNode, signatures: list[Signature]
    ) -> dict[DirectoryNode, tuple[float, Signature]]:
        """
        Recursively score nodes and return a mapping of node to its score and signature.
        """
        scores: dict[DirectoryNode, tuple[float, Signature]] = {}

        # Use cached signature if available
        signature = node.signature or self.get_matching_signature(node, signatures)

        if signature:
            score = self.calculate_score(node, signature)
            if score > 0:
                scores[node] = (score, signature)
                # If we matched this folder, we don't usually need to suggest its subfolders
                return scores

        for child in node.children.values():
            scores.update(self.apply_scoring(child, signatures))

        return scores
