# Tasks

Legend:

- [ ] Todo
- [/] In Progress (@user) <-- indicates who is doing it
- [x] Done

**INSTRUCTIONS:**

Keep task descriptions short but descriptive. Do not add implementation details, those belong in task-specific plans. When adding new tasks, consider grouping them into meaningful clusters such as UX, Backend, Logic, Refactoring, etc.

Put done tasks into the Archive.

---

## Active Tasks

- [ ] Update PyPI trusted-publisher config from `apiad/gc` → `apiad/fsgc` before the next release (https://pypi.org/manage/project/fsgc/settings/publishing/). The GitHub redirect keeps clones working but `uv publish` will fail OIDC verification on the next tag until this is changed. (See `know-how/releasing.md`.)

### Sweep safety + speed (from 2026-06-08 audit)

- [ ] Drop the `auto_check = avg_score > 0.8` default in `aggregator.group_by_signature` — pre-selected checkboxes combine dangerously with the one-keystroke "Run Collection" option in `prompt_confirm_action`. With the new scoring almost nothing hits 0.8 so the practical risk is much lower, but the principle still stands.
- [ ] Investigate `subprocess.run(["rm", "-rf", path])` fast-path for million-file trees; only worth doing now that Slice C parallel sweep landed. Measure before adopting.
- [ ] Fix pre-existing mypy errors in `src/fsgc/scanner.py:476` (list invariance — switch `results` to `Sequence` or annotate the literal).

---

## Archive

- [x] **Fast scan: signature-derived MCTS priors + wall-clock budget.** Engine builds `directory_priors` from signature literals at startup; scanner select_node gains tier 1.5 (cold-cache exploration priority). Default `--budget 10` (configurable, `--full` opts out). 12 new tests. On `~/`: 0.46 GB across 9 groups in 10 s, surfacing browser caches + Python venvs first. Spec: `plans/fast-scan-priors-and-budget.md`. (2026-06-08)
- [x] **Trail cache rewrite on beaver-db:** Removed scattered `.gctrail` files (deleted 383 from the workspace), replaced with single `~/.cache/fsgc/trails.db` keyed by absolute path. In-memory cache layer dodges beaver's reactor-thread contention. Fingerprint short-circuit on `os.stat` cache hit; **18.5 GB tree dropped 100 s → 22 s on warm cache (5×)**. Top children now record `(name, score, size)` so MCTS tier-2 picks where garbage was found, not where bytes are. New `fsgc inspect` / `fsgc cleanup-trails` / `fsgc scan --no-cache`. (2026-06-08)
- [x] **Heuristics overhaul:** Recovery-tier schema (`trivial` / `local` / `network`), score formula rewritten as `age_factor × RECOVERY_CAP[recovery]`, group sort by score not raw size, `max(atime, mtime)`, `**/bin` and `**/obj` dropped, `node_modules` sentinel fixed, `__pycache__` gains `min_age_days: 1`. Catalog expanded from 32 → 52 (per-profile browser caches for Chrome/Chromium/Brave/Edge/Vivaldi/Firefox, Discord/Spotify/JetBrains, uv interpreters, system trash, Snap/Flatpak). Smoke test on `~/Workspace/repos/` surfaced 8.6 GB across 6 groups. (2026-06-08)
- [x] **Slice C — Speed:** Parallel sweep on `ThreadPoolExecutor(max_concurrency=workers)` with thread-safe JSONL journal writes; Rich `Progress` bar (bytes/s, M/N, elapsed) replaces per-record chatter, post-sweep summary lists errors + skipped. 5 new tests on parallelism + ordering + progress callback. (2026-06-08)
- [x] **Slice B — Recoverability:** `send2trash` default + `--permanent` opt-in; JSONL sweep log at `~/.local/share/fsgc/sweep-log.jsonl` with `--no-journal` opt-out. (2026-06-08)
- [x] **Slice A — Cleanup safety net:** Extract `Sweeper` from `__main__.sweep()` with unsafe-root + symlink + sentinel-reverify guards; 11 tests covering the previously-untested deletion path. (2026-06-08)
- [x] Implement CI/CD workflow for automated testing and PyPI publication (via `uv publish`). (2026-03-18)
- [x] Create comprehensive project documentation in `docs/` (Overview, Deployment, Design, Development). (2026-03-18) (@apiad) (See plan: plans/documentation.md)
- [x] Implement incremental metadata propagation for wide-tree performance. (2026-03-18) (@apiad) (See plan: plans/fix-scanner-performance.md)
- [x] Implement real-time scan speed indicator (MB/s) and final summary statistics. (2026-03-18)
- [x] Accelerate the scanner using a bounded worker pool and `asyncio.to_thread` for parallel MCTS exploration. (2026-03-18) (See plan: plans/parallel-scanner.md)

- [x] Refine garbage collection signatures for OS, applications, and build systems. (2026-03-18) (See research/garbage-collection-signatures/)
- [x] Implement graceful interruption of the scanning phase with `Ctrl+C`. (2026-03-18) (See plan: plans/graceful-interrupt-scan.md)
- [x] Implement content-based sentinel verification for refined signature matching. (2026-03-18) (See plan: plans/implement-sentinel-verification.md)
- [x] Redesign scan heuristics using two-tiered selection (Trail + Signatures). (2026-03-18) (See plan: plans/redesign-scan-heuristics.md)
- [x] Implement Canonical MCTS search strategy. (2026-03-18) (See plan: plans/implement-canonical-mcts.md)
- [x] Implement "Incremental Refinement" scanning animation. (2026-03-16) (See plan: plans/incremental-scan.md)
- [x] Implement stochastic search engine and .gctrail caching. (2026-03-16) (See plan: plans/implement-stochastic-scanner.md)
- [x] Implement Mark Phase (heuristic scoring) and interactive deletion. (2026-03-15) (See plan: plans/implement-mark-phase.md)
- [x] Implement directory scanning and tree-like summary functionality. (2026-03-15) (See plan: plans/implement-scanning-summary.md)
- [x] Update `install.sh` to be served via GitHub Pages and update all references to use the new URL. (2026-03-11)
- [x] Create comprehensive User Guide (`docs/user-guide.md`) based on "The Architect in the Machine" philosophy. (2026-03-11) (See plan: plans/user-guide-integration.md)
- [x] Refine `/plan` command to strictly enforce a non-execution mandate for generated plans. (2026-03-11)
- [x] Integrate MkDocs with Material theme and setup automatic GitHub Pages deployment via CI/CD. (2026-03-18) (See plan: plans/mkdocs-integration.md)
- [x] Create comprehensive project documentation in `docs/` (Overview, Deployment, Design, Development). (2026-03-11)
- [x] Refine `/onboard` command to include documentation or source code discovery. (2026-03-11)
- [x] Simplify `/onboard` command to use direct file analysis instead of sub-agents. (2026-03-11)
- [x] Implement conditional journal hook enforcement based on file modification times. (2026-03-11) (See plan: plans/conditional-journal-enforcement.md)
- [x] Implement conditional `make` hook execution based on file modification times. (2026-03-11) (See plan: plans/conditional-make-hook.md)
- [x] Consolidate `add-gemini.sh` into a unified, non-destructive `install.sh` for setup and updates. (2026-03-11) (See plan: plans/unified-installer.md)
- [x] Implement the `install.sh` scaffolding script for new projects. (2026-03-03) (See plan: plans/install-script-scaffolding.md)
- [x] Refactor the `/research` command to follow a more extensible, executive-style reporting workflow with iterative updates and asset linking. (2026-03-02)
- [x] Implement drafting (`/draft`) and editing (`/revise`) capabilities using specialized subagents. (2026-03-02) (See plan: plans/drafting-and-editing-capabilities.md)
- [x] Implement a custom `/plan` command workflow and a `planner` sub-agent for repository analysis and plan generation in `plans/`. (2026-03-02)
- [x] Implement a `/cron` command and synchronization hook with systemd user timers for scheduled tasks. (2026-03-02)
- [x] Add the /issues command to manage project issues with GitHub CLI. (2026-02-28)
- [x] Refactor the hook system: centralize shared logic into `.gemini/hooks/utils.py` and add PEP 257 docstrings. (2026-02-28)
- [x] Rewrite the `README.md` to explain the opinionated framework and its key features. (2026-02-28)
- [x] Refactor the `/research` command into a 3-phase workflow with researcher and reporter subagents. (2026-02-28)
- [x] Consolidate the `/task/*` commands into a single `/task` command. (2026-02-28)

> Done tasks go here, in the order they where finished, with a finished date.
