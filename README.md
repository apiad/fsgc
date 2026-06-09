# 🤖 fsgc — Filesystem Garbage Collector

<div align="center">

[![Release](https://img.shields.io/badge/Release-v0.5.0-blue.svg?style=for-the-badge)](https://github.com/apiad/fsgc/releases)
[![License](https://img.shields.io/github/license/apiad/fsgc?style=for-the-badge&color=success)](LICENSE)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg?style=for-the-badge)](https://github.com/apiad/fsgc/graphs/commit-activity)

**Clean your filesystem with precision.**

*A Python-based CLI utility that performs high-performance filesystem scanning using MCTS-informed search to identify space-intensive directories and suggests garbage collection based on intelligent heuristics.*

</div>

---

## 🚀 Getting Started

The `fsgc` tool is built with modern Python tooling (`uv`).

### Installation

For users (recommended):
```bash
uvx fsgc
```
Or via pipx:
```bash
pipx install fsgc
```

For developers:
```bash
# Clone the repository
git clone https://github.com/apiad/fsgc.git
cd fsgc

# Install dependencies and build the project
uv sync
```

### Usage

By default, running `fsgc` will perform a stochastic MCTS-informed scan of the current directory:

```bash
fsgc scan .
```

#### Options:
- `--workers` / `-w`: Number of concurrent workers (default: 8).
- `--depth` / `-d`: Maximum display depth (default: 2).
- `--min-percent` / `-p`: Minimum size percentage of parent to show child (default: 0.01).
- `--limit` / `-l`: Maximum number of children to list individually (default: 10).
- `--age` / `-a`: Age threshold in days for recency heuristic (default: 90).
- `--dry-run`: Show what would be collected without deleting.

---

## 🧠 The Core Philosophy

`fsgc` is "The Architect in the Machine." It uses a **Stochastic Search** architecture to turn raw metadata into actionable deletion proposals:

1.  **Scanner (The Playout):** Informed MCTS scanning using `GCTrail` history and known signatures.
2.  **Heuristic Engine (The Mark Phase):** Scores nodes based on patterns, recency, and sentinel verification (e.g., verifying `package.json` for `node_modules`).
3.  **Aggregator (The Sweep Phase):** Groups garbage into logical collections for interactive selection.

---

## 🛠️ Development Lifecycle

The project is managed using a highly customized Gemini CLI agent framework. For more information on the framework and available commands, refer to [GEMINI.md](GEMINI.md).

### Standard Targets:
- `make lint`: Run Ruff checks.
- `make format`: Apply code formatting.
- `make test`: Execute the test suite.
- `make check`: Run Mypy static analysis.
- `make all`: Run all checks and tests.

## ⚓ The Hook System

The framework uses a robust hook system (`.gemini/hooks/`) that synchronizes the agent with your project state, ensuring continuous validation and journaling.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
