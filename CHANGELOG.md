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
- **Test coverage for the deletion path:** 11 new tests in `tests/test_sweeper.py` covering dry-run, run, unsafe-root, symlinks, sentinel re-verification, missing paths, OSError tolerance, and freed-bytes accounting.

### Changed
- `aggregator.group_by_signature()` now includes the matched `Signature` in each group dict (used by the sweeper for sentinel re-verification).
- Sweep output is per-record (deleted / skipped / error) with skip reasons surfaced to the user; reclaimed-bytes total now reflects bytes actually freed rather than scanned size.

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
