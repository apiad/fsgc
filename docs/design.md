# Architecture

**fsgc** runs three phases over the scanned tree: a stochastic walk (MCTS) that surfaces candidate directories, a heuristic mark pass that scores them against the signature catalog, and an aggregator that groups the scored nodes for the interactive sweep. This page is the deep dive — the [overview](index.md) is the user-facing summary.

---

## The three phases

```text
[Filesystem]
     │
     │  (os.scandir + os.stat, threaded via asyncio.to_thread)
     ▼
[Scanner]  ── MCTS playout — picks high-prior subtrees first
     │
     │  per-node: size, atime, mtime, file_evidence, fingerprint
     ▼
[HeuristicEngine]  ── mark phase — score = age_factor × recovery_cap
     │
     │  node → (score, Signature)
     ▼
[Aggregator]  ── group_by_signature + group_behavioral_matches
     │
     ▼
[CLI prompt] ── two-section proposal, interactive checkbox, typed-yes gate
     │
     ▼
[Sweeper]  ── guards + send2trash, JSONL audit
```

---

## Scanner — the MCTS playout

`Scanner` doesn't enumerate the tree. It runs an MCTS playout from the root, picking a child per iteration via `select_node`:

1. **Signature tier.** Among unexplored children, prefer the one whose own matched `Signature.recovery_cap` is highest. Trivial-recovery garbage (browser caches, `__pycache__`) surfaces first because it's the safest and cheapest to delete.
2. **Directory-prior tier (tier 1.5).** Children whose name appears as a literal path component of any signature pattern (e.g. `.cache`, `.config`, `node_modules`, `__pycache__`) get a prior. Terminal literals (the leaf of the pattern — "garbage IS here") score 1.0; interior literals (a step on the way — `.cache`, `mozilla`, `firefox`) score 0.5. This catches the cold-cache case where no trail history exists yet.
3. **Trail tier.** If a previous scan stored a trail for this directory, pick the child with the highest `score × size` from its `top_subdirs` ledger.
4. **Greedy fallback.** Largest estimated size among unvisited children; random if every child has been visited.

The playout calls `_process_directory` on the chosen node. That function does one `os.stat` to compute a structural fingerprint (`hash(mtime, st_nlink)`); if the fingerprint matches a cached trail entry, the walk is skipped entirely — the node's size, evidence, and top-children are restored from cache. Otherwise it scandirs, adds child nodes, and accumulates evidence.

A **wall-clock budget** (`--budget 30`, default) caps the scan phase. Workers check `time.monotonic()` between MCTS iterations; on deadline they cancel cleanly, the partial tree yields once more, and `timed_out = True` surfaces a "use `--full` for thorough" hint. Partial subtrees are deliberately not persisted to the trail — next run continues from where this one stopped, guided by the same priors.

## DirectoryNode — incremental propagation

Every `DirectoryNode` keeps the running totals it would otherwise need to recompute on every UI refresh: `confirmed_size`, `estimated_size`, `completion_ratio`, plus `_sum_child_*` counters that track contributions from children. When a child updates, it propagates the delta upward via `propagate_child_update`. The root node has an $O(1)$ snapshot of the entire scanned tree at any moment — that's what makes the live tree render in real time without re-walking on every frame.

## HeuristicEngine — mark phase

`HeuristicEngine.calculate_score`:

```text
age_factor = clamp(0, 1, age_seconds / age_threshold)
score      = age_factor × RECOVERY_CAP[signature.recovery]
```

`age_seconds` uses `max(atime, mtime)` because Linux `noatime` mounts make `atime` unreliable. `min_age_days` on the signature is a hard cutoff applied before scoring — younger directories return 0 and never reach the proposal.

`get_matching_signature` walks the signature catalog, matches the directory's path against each pattern, and (if the signature declares `sentinels`) verifies at least one sentinel file is present inside the directory. The check runs again at sweep time in the Sweeper — that's the "sentinel re-verification" guard — closing the race between proposal and confirmation.

## Aggregator — the two-section proposal

`group_by_signature` collapses scored nodes into one row per signature, sorted by `(avg_score, total_size)` descending. Old + trivial caches surface above large-but-fresh `.venv` trees (the recovery tier dominates the sort).

`group_behavioral_matches` collapses BehavioralMatch records (produced by Scanner during the walk, see [Behavioral rules](behaviors.md)) into REVIEW groups, sorted by size descending. The CLI renders the two sections separately with distinct headers and color, and the interactive checkbox refuses to auto-check REVIEW items.

## Trail cache

`TrailStore` is a single SQLite file at `~/.cache/fsgc/trails.db`, opened via `beaver-db`. Mid-scan reads and writes go to an in-memory dict (O(1), no contention); the bulk flush to disk happens once at `close()`. Each entry holds the directory's fingerprint, total size, entry count, atime/mtime, a sample of file evidence, and a `top_children` ledger of `(name, score, size)`.

The 30-day TTL is applied at write time, so stale entries naturally age out. `fsgc cleanup-trails --drop-cache` drops the whole store; `fsgc inspect [path]` browses what's there.

## Sweeper — the deletion path

Isolated from the rest of the pipeline. Takes a list of group dicts and produces a structured `SweepResult` with one `DeletionRecord` per processed node. Every record goes through:

1. **Unsafe-root guard** — refuses `/`, `$HOME` itself, and a built-in list of system paths regardless of signature match.
2. **Symlink guard** — `path.is_symlink()` short-circuits to a `SKIPPED` record before any stat happens.
3. **Sentinel re-verification** — for structural groups only; behavioral REVIEW items have no signature and skip this check.
4. **Action** — `send2trash` by default; `shutil.rmtree`/`path.unlink` only when `trash=False`.

Concurrency: `ThreadPoolExecutor(max_concurrency)` — `shutil.rmtree` and `send2trash` release the GIL during syscalls, so a million-file `node_modules` no longer blocks the rest of the queue. Records are reassembled in submission order; the JSONL journal serializes via a mutex so no entries are lost.
