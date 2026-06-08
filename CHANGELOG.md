# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Sweeper module (`fsgc.sweeper`):** Extracted the deletion path into a dedicated `Sweeper` class with structured `SweepResult` / `DeletionRecord` records, replacing the inline loop in `__main__.sweep()`. The CLI now formats results; the sweeper decides what to delete.
- **Unsafe-root guard:** Sweeper refuses to delete the filesystem root, the user's home directory, and a built-in list of system paths (`/usr`, `/etc`, `/var`, `/boot`, `/bin`, `/lib`, …) regardless of signature match.
- **Symlink guard:** Symlinks are never followed during sweep — the symlink itself is preserved and the target is untouched, even when the symlink's name matches a signature pattern.
- **Sentinel re-verification at sweep time:** Each node is re-stat'd before deletion to confirm at least one signature sentinel is still present, catching the race where a sentinel disappeared between scan and confirm.
- **Trash-by-default deletion (`send2trash`):** Confirmed sweeps now move directories to the system trash instead of unlinking them. Opt out with `--permanent` for the prior rmtree behavior. The confirmation prompt distinguishes "Move to Trash" from "PERMANENT Deletion" so the user knows which mode is active.
- **JSONL sweep journal:** Every record (trashed, deleted, dry-run, skipped, or errored) is appended as one JSON line to `~/.local/share/fsgc/sweep-log.jsonl` for audit + recovery. Disable with `--no-journal`.
- **Test coverage for the deletion path:** 18 tests in `tests/test_sweeper.py` covering dry-run/run, unsafe-root, symlinks, sentinel re-verification, missing paths, OSError tolerance, freed-bytes accounting, trash vs permanent modes, trash failure handling, and journal output (single + multi-invocation + every-outcome).

### Changed
- `aggregator.group_by_signature()` now includes the matched `Signature` in each group dict (used by the sweeper for sentinel re-verification).
- Sweep output is per-record (trashed / deleted / skipped / errored) with skip reasons surfaced to the user; reclaimed-bytes total now reflects bytes actually freed rather than scanned size.

### Dependencies
- Added `send2trash >= 1.8` for cross-platform recoverable deletion.

### Performance
- **Parallel sweep:** `Sweeper.max_concurrency` runs deletions on a `ThreadPoolExecutor` (default 1 for library use; CLI threads through `--workers`, default 8). `shutil.rmtree` and `send2trash` release the GIL during syscalls so a million-file `node_modules` no longer blocks the rest of the queue. Records stay reassembled in submission order; the journal serializes via a mutex so no entries are lost under concurrency.
- **Live progress bar:** Sweeps now render a Rich `Progress` (spinner, bar, M/N items, bytes/s, elapsed) that updates per-record. The per-record chatter in the previous output was replaced by a post-sweep summary listing every error and skipped item, so failures stay visible without scrolling through the deletion log.

### Heuristics overhaul (BREAKING — no backcompat)
- **Recovery-tier schema:** `Signature.priority: float` removed; `Signature.recovery: Recovery` (enum: `trivial` / `local` / `network`) takes its place. The tier caps the score (1.0 / 0.7 / 0.4) and expresses how costly the directory is to restore — `trivial` regenerates automatically offline, `local` rebuilds from sources in the same tree, `network` requires re-downloading.
- **Score formula rewritten:** `score = age_factor × RECOVERY_CAP[recovery]`. Recency was 10% of the prior formula; it's now the multiplier. The dead `p_score = 1.0 * 0.6` constant was removed entirely. Old + trivial surfaces first; young or network-bound sinks to the bottom.
- **Group sort by score, not raw size:** `aggregator.group_by_signature` now sorts by `(avg_score, size)` descending so the user-facing proposal matches the recovery-tier ordering. Previously, large but actively-used `.venv` trees would appear above small but stale browser caches.
- **Min-age check uses `max(atime, mtime)`:** Linux defaults to `noatime` mounts, making `atime` unreliable. mtime tracks directory-content churn (entries added/removed), which is the right "still in use" signal.
- **Dangerous signatures removed:** Bare `**/bin` and `**/obj` (no sentinels in the YAML, despite docs claiming `.dll`/`.pdb`) are no longer shipped — they would have matched `~/Workspace/bin/`, `.venv/bin/`, `~/.local/bin/`. Re-add per-user via `~/.config/fsgc/signatures.yaml` if needed.
- **`node_modules` sentinel fixed:** Removed the bogus literal `"node_modules"` sentinel (a directory name, not a file). `package.json` remains the sole sentinel.
- **`__pycache__` gains `min_age_days: 1`** to avoid being swept mid-build.

### Catalog expansion
- **Per-profile browser caches (Linux):** Chrome / Chromium / Brave / Microsoft Edge / Vivaldi each get `**/.config/<browser>/<Profile>/Cache` patterns (plus Chrome's `Code Cache`, `GPUCache`, `Service Worker/CacheStorage` — where the multi-GB actually lives, vs the often-empty `~/.cache/google-chrome`).
- **Firefox** `**/.cache/mozilla/firefox/*/cache2`.
- **Electron desktop apps:** Discord, Spotify, JetBrains, plus Cursor and VS Code `CachedData` on top of the existing VS Code / Slack rules.
- **uv interpreters** `**/.local/share/uv/python` (often multi-GB of downloaded CPython builds).
- **System trash** `**/.local/share/Trash/{files,info}` — emptying the trash is now part of the sweep proposal.
- **Snap / Flatpak per-app caches** `**/.cache/snap`, `**/.var/app/*/cache`.
- **Generic build outputs** now require strong sentinels: `**/build` requires `*.o`/`*.a`/`*.lib`/`CMakeCache.txt`; `**/dist` requires `*.whl`/`*.tar.gz`/`*.egg-info`.

### Verification
- Total signatures: **52** (up from 32).
- All 60 tests pass; `test_engine.py` rewritten with 9 focused tests on the new formula (recovery cap, age scaling, min-age cutoff, atime-vs-mtime, tier ordering).
- Smoke test on `~/Workspace/repos/` surfaced **8.6 GB** recoverable across 6 groups in a single scan; with the new sort, old + trivial caches sit above large-but-fresh `.venv` trees as intended.

### Trail cache rewrite (BREAKING — no migration)
- **All `.gctrail` files are gone.** The previous implementation wrote a binary `.gctrail` per directory >100 MB, scattering hundreds of pollution files across `~`, including inside `.venv` trees that were themselves about to be deleted (self-defeating). Removed the format, the persist, and the read paths. **383 stale `.gctrail` files were deleted from the workspace at the same time.**
- **New `TrailStore` backed by `beaver-db` (SQLite).** Single file at `~/.cache/fsgc/trails.db`, keyed by absolute path. Schema: `{scanned_at, fingerprint, total_size, entry_count, atime, mtime, file_evidence, top_children: [(name, score, size)]}`. 30-day TTL applied at write time so stale entries naturally age out.
- **In-memory cache layer.** `BeaverDB`'s sync facade marshals every call onto a single background "Reactor" thread, causing SQLite lock contention under 8 concurrent workers. `TrailStore` now bulk-loads every record on open and bulk-flushes on close; mid-scan `get`/`put` operates on a plain Python dict (O(1), zero contention).
- **Fingerprint short-circuit on cache hit.** `_process_directory` does one `os.stat`, computes `fingerprint = hash(mtime, st_nlink)`, looks up the trail. On match: restore size/atime/mtime/evidence/top_children from the cache, mark fully-explored, return. The 30-second second-run win: **a 18.5 GB tree dropped from 100 s → 22 s** (5× speed-up) once warm. Caveat: garbage created INSIDE an unchanged parent dir (mtime didn't bubble up) is missed until the parent's mtime ticks or the TTL expires; `--no-cache` forces a full walk.
- **Top children recorded by trash density.** Previous format stored top subdirs by raw size. New format stores `(name, score, size)` where `score = engine.calculate_score(child, child.signature)`. Tier-2 MCTS selection now picks children by `score × size` so future scans prioritize the dirs where garbage was actually found, not just the biggest dirs.
- **New `fsgc inspect`** queries `TrailStore` (cached path list + scores) instead of reading `.gctrail` files from disk.
- **New `fsgc cleanup-trails`** subcommand removes any leftover legacy `.gctrail` files (`--home` to scope, `--drop-cache` to also wipe the beaver DB, `--dry-run` to preview).
- **New `fsgc scan --no-cache`** flag bypasses `TrailStore` for one run — use weekly to recheck dirs whose mtime never changes (in-place file edits).
- Added `beaver-db>=1.3` dependency.

### Fast scan: signature-derived MCTS priors + wall-clock budget
- **Engine `directory_priors` map.** `HeuristicEngine` precomputes a `name → max recovery_cap` map at first signature lookup, walking every pattern's literal path components. From the current catalog: `.cache → 1.0`, `.config → 1.0`, `.local → 1.0`, `__pycache__ → 1.0`, `node_modules → 0.4`, etc. The catalog is now the single source of truth for both scoring AND exploration order — no parallel hardcoded list.
- **Scanner tier-1.5 selection.** New tier between exact-signature-match (tier 1) and trail-derived (tier 2) in `Scanner.select_node`: among unexplored children, prefer the one whose `path.name` has the highest entry in `directory_priors`. Ties broken by `estimated_size`. On cold cache, MCTS now rushes into `.cache/.config/.local` first instead of wandering into `Documents/Music/Pictures`.
- **Wall-clock budget (`--budget N`, default 10 s).** `Scanner.__init__` takes `budget_seconds`. Worker loops check `time.monotonic()` between MCTS iterations; on deadline, mark `timed_out = True`, cancel queued workers, yield the partial tree once. Scoring + sweep run on whatever was found. Partial subtrees skip `persist_trail` (existing condition), so the cache stays clean.
- **`--full` flag.** Shorthand for "no budget". Mutually exclusive with explicit `--budget N` (Typer raises a clean error).
- **Summary line shows budget status.** New trailing chunk on timeout: `· budget exhausted, N dirs incomplete (use --full for thorough)`.

### Verification (Fast scan)
- 9 new tests in `test_scanner_priors.py` (3 engine map building, 2 scanner tier-1.5 selection, 1 CLI mutex) + 3 in `test_scanner.py` (budget fires, full mode completes, timeout doesn't persist partials). 72 tests pass total.
- Acceptance on `~/` (cold cache, **30 s budget** default):
  - Wall-clock: **30.05 s**. `timed_out: True` as expected.
  - **Found: 0.78 GB across 16 groups.** Surfaced Firefox Cache (286 MB), Discord Cache (117 MB), Go Module Cache (72 MB), Gradle (40 MB), Flatpak (18 MB), Go Build (16 MB), uv Cache, PyTorch Hub, Pytest/Ruff/Mypy caches, Python Bytecode, distribution artifacts.
  - Large reclaim targets deeper in the tree (`Workspace/repos/*/.venv` at 8.6 GB, `.local/share/Trash` at 1.1 GB) need `--full` to reach — they sit behind 3–4 levels of zero-prior intermediate dirs (`Workspace`, `repos`, `<repo_name>`) which MCTS can only reach by random selection at cold cache.
- `fsgc scan --full ~` recovers today's full-walk behavior; no behavior change in `--full` mode.

### Subsequent tuning (after initial acceptance)
- **Default budget bumped from 10 s → 30 s.** 10 s consistently missed `.cache/uv`, `.cache/huggingface` and the Trash; 30 s hits them with room to spare.
- **Prior weighting redesigned: terminal vs. interior, not max recovery cap.**
  Previous: `directory_priors[name] = max(recovery_cap of any sig using name as a literal)`. This deprioritized network-recovery targets at selection time — `.cache/uv` (NETWORK 0.4) lost to `.cache/snap` (TRIVIAL 1.0) even when `uv` was 30× larger. Recovery is for *scoring*, not selection.
  New: a literal scores **1.0 if terminal** (last literal of any pattern — "garbage IS here": `uv`, `huggingface`, `node_modules`, `__pycache__`, `Cache`, `cache2`, …) or **0.5 if interior** (a step on the way: `.cache`, `.config`, `mozilla`, `firefox`). Once at `~/.cache/`, MCTS treats `uv`, `huggingface`, `JetBrains`, `google-chrome`, and `snap` as equally promising — eight workers will pick all of them. Network-recovery garbage no longer hides behind trivial siblings.

## [0.3.0] - 2026-03-18

### Added
- **Stochastic MCTS-based Scanner:** New informed search strategy using Monte Carlo Tree Search (MCTS) to prioritize high-value garbage branches.
- **Parallelization:** Bounded worker pool using `asyncio.to_thread` for concurrent filesystem exploration.
- **Incremental Metadata Propagation:** Push-based upward metadata updates for $O(1)$ root snapshots and improved wide-tree performance.
- **Documentation Suite:** Comprehensive `docs/` directory with MkDocs/Material theme integration.
- **CI/CD:** Automated testing and PyPI publication workflows via GitHub Actions.
- **Real-time Metrics:** Scan speed indicator (MB/s) and summary statistics in the TUI.
- **Graceful Interruption:** Robust `Ctrl+C` handling in the scanning phase.
- **Sentinel Verification:** Content-based verification for garbage signatures (e.g., checking for `package.json` in `node_modules`).

### Changed
- Refactored `Scanner` to an async-first model.
- Optimized signature matching with name-based fast-paths and caching.
- Enhanced `GCTrail` binary schema to store top subdirectories for informed selection.

### Fixed
- Performance bottlenecks in wide directory tree traversals.
- Quadratic complexity in MCTS node selection.
- Redundant signature matching across iterations.

## [0.2.0] - 2026-03-15

### Added
- **Core Package (`fsgc`):** Initial implementation of the "Garbage Collector" CLI utility.
- **Scanner Engine:** High-performance filesystem scanner using `os.scandir` and a Breadth-First Search (BFS) approach.
- **Tree-based Aggregation:** Logic to build a directory tree and aggregate sizes from the bottom up.
- **Hierarchical Summary:** Tree-like TUI summary using `Rich`, featuring configurable depth, child limits, and size-based grouping.
- **Human-Readable Sizes:** Automatic formatting of byte counts into KB, MB, GB, etc., across the entire CLI output.
- **Modern Tooling:** Project initialized with `uv`, `ruff`, `mypy`, and `pytest`.

### Changed
- Refactored CLI to use `scan` as the default command when invoked without subcommands.
- Updated `makefile` with standardized `lint`, `test`, `check`, and `format` targets.

## [0.11.0] - 2026-03-11

...
