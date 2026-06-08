# Fast Scan: Signature-Derived MCTS Priors + Wall-Clock Budget

**Status:** spec — awaiting review before implementation plan
**Date:** 2026-06-08
**Authors:** Alex (intent) + Claude (design)

## Motivation

Today `fsgc scan` on a cold cache walks the entire tree before showing
results. On `~/Workspace/repos/` (18.5 GB, ~36k dirs) that's ~100 s; on a
full `~` it's many minutes. The user has to commit to a long scan even
when they just want to free a few GB from obvious hot spots
(`~/.cache/google-chrome`, `~/.cache/uv`, browser per-profile caches).

Two complementary changes turn `fsgc scan` into a "first few GBs in a few
seconds" tool while keeping a "scan the whole disk" mode available:

1. **Signature-derived MCTS prior** — let the catalog tell MCTS where to
   look first. `.cache`, `.config`, `node_modules`, `__pycache__` etc. all
   appear as literal components inside `signatures.yaml` patterns; that's
   already a curated list of "places likely to hold garbage." Reuse it as
   a selection prior so MCTS skips `Documents/`, `Music/`, etc. early.
2. **Wall-clock budget** — bound the scan phase to a default 10 s
   (configurable, `--full` for no cap). When the budget fires, surface
   whatever's been found and let the user sweep it; partial subtrees are
   skipped on cache write so the next run keeps exploring where this one
   left off.

The win shape: cold-cache scan that surfaces ≥1 GB of garbage in ≤10 s
without changing any existing behavior when `--full` is passed.

## Non-goals

- **Bytes-of-garbage budget** ("stop after finding 5 GB") — wall-clock is
  predictable and simpler; can be revisited if 10 s proves insufficient.
- **Path-aware priors** (e.g. "`uv` only matters under `.cache`") — the
  flat name-set is good enough for v1; the false-positive cost is just
  exploring an irrelevant dir for a few ms.
- **New telemetry / metrics** — `cache_hits`, `cache_misses`, and a new
  `timed_out` flag are enough.
- **Reworking the existing cache short-circuit** — it stays as-is; the
  budget only constrains the **cold** path.

## Component 1: Signature-derived MCTS prior

### Engine-side: precompute the priors map

At `HeuristicEngine.__init__`, after the signatures list is available,
walk every pattern and extract its literal components. A literal is any
slash-delimited segment that is neither `**` nor a glob (no `*`, `?`,
`[`). Each literal accumulates the **max** `recovery_cap` of any signature
whose pattern contains it.

```python
# fsgc/engine.py — pseudocode
def _build_directory_priors(self, signatures):
    priors: dict[str, float] = {}
    for sig in signatures:
        cap = sig.recovery_cap
        for part in sig.pattern.split("/"):
            if not part or part == "**":
                continue
            if any(c in part for c in "*?["):
                continue
            priors[part] = max(priors.get(part, 0.0), cap)
    return priors
```

From the current catalog this yields:

| Literal | Cap | Source signatures |
|---|---|---|
| `.cache` | 1.0 | `**/.cache/google-chrome`, `**/.cache/JetBrains`, `**/.cache/snap`, … |
| `.config` | 1.0 | `**/.config/google-chrome/*/Cache`, `**/.config/Code/Cache`, … |
| `.local` | 1.0 | `**/.local/share/Trash/files` |
| `__pycache__` | 1.0 | `**/__pycache__` |
| `.thumbnails` | 1.0 | `**/.thumbnails` |
| `.venv` | 0.4 | `**/.venv` |
| `node_modules` | 0.4 | `**/node_modules` |
| `Workspace` | (absent → 0) | — |
| `Documents` | (absent → 0) | — |

The map is precomputed once and exposed as `engine.directory_priors`. The
existing `_get_matchers` work is untouched.

### Scanner-side: new tier 1.5 in select_node

`Scanner.select_node` currently is:

```
Tier 1 (signature exact match) → tier 2 (trail-derived) → fallback (largest estimated size)
```

Insert a new tier **between** tier 1 and tier 2:

> **Tier 1.5 — directory-prior:** among unexplored children, pick the one
> whose `path.name` has the highest entry in `engine.directory_priors`.
> Ties broken by `estimated_size` (existing fallback ordering).

```python
# fsgc/scanner.py select_node, between tier 1 and tier 2
if self.engine and self.engine.directory_priors:
    best_prior = 0.0
    best_child: DirectoryNode | None = None
    for child in available_children:
        prior = self.engine.directory_priors.get(child.path.name, 0.0)
        if prior > best_prior or (
            prior == best_prior
            and best_child is not None
            and child.estimated_size > best_child.estimated_size
        ):
            best_prior = prior
            best_child = child
    if best_child is not None and best_prior > 0.0:
        return best_child
```

The `best_prior > 0.0` guard means: when **no** child has a known-garbage
name, fall through to tier 2 (trail) or the existing fallback. We don't
force the scan into low-value paths.

### Cost

- Engine init: one extra ~O(n_signatures × pattern_components) pass at
  startup. ~52 patterns × 3-4 components each ≈ 200 string ops. Trivial.
- Scanner per-selection: one dict lookup per child (~10s of children),
  same big-O as today.

## Component 2: Wall-clock budget

### Scanner-side: budget plumbing

`Scanner.__init__` gains:

```python
budget_seconds: float | None = 10.0  # None = infinite (the --full case)
```

`scan()` captures the deadline before spawning workers:

```python
self._deadline: float | None = (
    time.time() + self.budget_seconds if self.budget_seconds is not None else None
)
self.timed_out: bool = False
```

The worker loop checks the deadline **between MCTS iterations** (not
inside `_process_directory`, which is fast and we want it atomic):

```python
# fsgc/scanner.py worker()
while not node.is_fully_explored and iterations < max_iterations:
    if self._deadline is not None and time.time() > self._deadline:
        self.timed_out = True
        return  # graceful exit from this worker
    await self.mcts_iteration(node)
    iterations += 1
```

The top-level `scan()` generator also checks the deadline once per
yield-tick (every 100 ms anyway, for the live UI), and once `timed_out`
is set, it cancels remaining worker tasks and yields the partial tree
one final time.

### Trail cache interaction

No change to `persist_trail`. The existing condition
`node.state == ScanState.FINISHED` already gates persistence on full
exploration, so:

- Partial subtrees → never persisted, never pollute the cache.
- Fully-explored subtrees → persisted as today, including those that
  finished **before** the budget fired.

Next run benefits from whatever the budget allowed to complete; the
incomplete branches are re-explored, guided by the priors back to the
same hot spots, and likely finish.

### CLI surface

| Flag | Default | Meaning |
|---|---|---|
| `--budget N` | `10` (seconds; `0` = infinite) | Wall-clock cap on scan phase. |
| `--full` | off | Shorthand for `--budget 0`. |

Validation: `--full --budget X` (with X > 0) is a CLI error — they're
mutually exclusive.

Post-scan summary changes from:

```
Scanned 18.5 GB in 22.39s (avg 826 MB/s) · cache: 11549/11840 hits (98%)
```

to (on timeout):

```
Scanned 6.2 GB in 9.84s (avg 630 MB/s) · cache: 412/2103 hits (20%) · budget exhausted, 847 dirs incomplete (use --full for thorough)
```

The trailing hint is intentional; first-time users hitting the budget
see the escape hatch.

## Data flow

```
fsgc scan (default)
  ↓
HeuristicEngine.__init__ → builds directory_priors
  ↓
Scanner(budget_seconds=10, engine, ...)
  ↓
scan() captures deadline = now + 10
  ↓
worker pool runs MCTS iterations
  → select_node uses tier 1.5 prior, surfacing .cache/.config/.local first
  → fully-explored nodes persist trails into TrailStore
  → before each iteration, worker checks deadline → break if exceeded
  ↓
At timeout (or completion):
  → yield final partial tree once
  → TrailStore.close() bulk-flushes any new fully-explored entries
  ↓
Scoring → aggregation → interactive prompt → sweep (unchanged)
```

## Tests

New file `tests/test_scanner_priors.py`:

- `test_engine_builds_directory_priors_from_signatures` — given a 3-sig
  catalog, the priors map has the expected keys + caps.
- `test_engine_directory_priors_takes_max_cap_per_literal` — same literal
  in NETWORK + TRIVIAL sigs → maps to TRIVIAL (1.0).
- `test_engine_skips_glob_components_when_building_priors` —
  `**/.config/google-chrome/*/Cache` contributes `.config`,
  `google-chrome`, `Cache`; does NOT add `*` to the map.
- `test_scanner_select_node_prefers_high_prior_child_over_larger_size` —
  with priors active, scanner picks the smaller `.cache` child over a
  larger `Documents` child.
- `test_scanner_select_node_falls_through_when_no_prior` — with no
  matching child, behavior reverts to existing tier-2 / size fallback.

Extensions to `tests/test_scanner.py`:

- `test_scanner_respects_budget_seconds_yields_partial_tree` — inject a
  slow `_get_entries` via monkeypatch; budget=0.1s; assert
  `scanner.timed_out is True` and the tree has the root + at least one
  child explored.
- `test_scanner_timeout_does_not_persist_partial_subtrees` — after a
  forced timeout, count the trail store entries; only fully-explored
  nodes are present.
- `test_scanner_no_budget_runs_to_completion` — `budget_seconds=None`;
  every node ends up fully-explored, `timed_out` is False.

CLI integration (extend `tests/test_main_cli.py` if it exists, else
inline check in `tests/test_scanner_priors.py`):

- `test_cli_rejects_full_and_budget_together` — typer surfaces the
  mutual-exclusion error.

## Acceptance (measured + reported in the implementation commit)

Two measurements on this laptop:

1. **Default `fsgc scan ~`** (cold cache, budget=10s):
   - Wall-clock ≤ 11 s (10 s budget + flush overhead).
   - Garbage found ≥ 1 GB across ≥ 3 distinct groups.
   - Browser caches + uv cache visible in the proposal.

2. **`fsgc scan --full ~`** (cold cache, no budget):
   - Wall-clock matches current behavior within noise.
   - Total garbage found is the existing baseline.

The exact numbers go in the commit message and the CHANGELOG `Verification`
section.

## What ships in one commit

- `src/fsgc/engine.py`: `_build_directory_priors`, `directory_priors`
  attribute.
- `src/fsgc/scanner.py`: tier 1.5 in `select_node`, `budget_seconds`
  constructor arg, `_deadline` + `timed_out` plumbing, worker-loop
  deadline check, graceful cancel on timeout.
- `src/fsgc/__main__.py`: `--budget` / `--full` flags on `scan`,
  mutual-exclusion validation, summary line update.
- `tests/test_scanner_priors.py` (new).
- `tests/test_scanner.py` (extended).
- `docs/signatures.md`: note about how the catalog drives selection priors.
- `know-how/adding-signatures.md`: note that adding a sig also adjusts the
  MCTS prior for free.
- `CHANGELOG.md`: new section under `## [Unreleased]`.
- `TASKS.md`: archive the audit item this closes ("MCTS doesn't prioritize
  high-value paths on cold cache").

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| 10 s budget hides large garbage on a slow disk. | `--budget 30` or `--full` is one flag away; summary surfaces the hint. |
| Prior locks MCTS into low-value caches and misses a giant `node_modules` under `Workspace/`. | `Workspace` has no prior → tier 2 (trail) kicks in once warm. First-run miss is recovered on second run. |
| `_deadline` check race — worker reads `True` after scan already returned. | Workers exit cleanly via `return`; cancel is idempotent. |
| Time-source skew (e.g. NTP slew). | `time.monotonic()` instead of `time.time()` for the deadline. (Implementation detail; spec says "deadline" — use monotonic.) |

## Out of scope / future work

- **Adaptive budget** — if scan is still finding new high-score groups
  at deadline, extend by some delta. Easy add-on later if 10 s proves
  inadequate.
- **Path-context-aware priors** — track depth along the literal chain so
  `uv` is only a prior under `.cache`. Catches the "user has a project
  folder named `uv`" edge case.
- **Bytes-of-garbage stop** — terminate when N GB of high-score garbage
  found, regardless of clock.
