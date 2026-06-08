# Installation and Usage Reference

**fsgc** is designed for high-performance filesystem scanning and garbage collection. This guide covers installation methods and a comprehensive reference for CLI commands.

---

## 💾 Installation

### For Users (Recommended)

The easiest way to run **fsgc** without installing it globally is via `uvx`:

```bash
uvx fsgc scan .
```

Alternatively, if you prefer `pipx`:

```bash
pipx install fsgc
```

### For Developers

To set up a local development environment, use `uv`:

```bash
# Clone the repository
git clone https://github.com/apiad/fsgc.git
cd fsgc

# Synchronize virtual environment and dependencies
uv sync

# Run from source
uv run fsgc scan .
```

---

## 🛠 CLI Reference

**fsgc** provides two primary commands: `scan` (the main engine) and `inspect` (the trail viewer).

### `gc scan [PATH]`

Scans the specified path for garbage and proposes collection.

| Option | Shorthand | Default | Description |
| :--- | :--- | :--- | :--- |
| `--dry-run` | | `False` | Show what would be collected without deleting anything. |
| `--min-size` | | `0` | Minimum size in bytes for a directory to be reported. |
| `--depth` | `-d` | `2` | Maximum display depth in the scan tree. |
| `--min-percent`| `-p` | `0.01` | Minimum size percentage relative to parent to show a child. |
| `--limit` | `-l` | `10` | Maximum number of children to list individually per node. |
| `--age` | `-a` | `90` | Age threshold in days for the recency heuristic. |
| `--workers` | `-w` | `8` | Number of concurrent MCTS workers. |

#### Example:
```bash
# Aggressively scan with 16 workers and show only large folders (>100MB)
uvx fsgc scan . -w 16 --min-size 104857600
```

### `gc inspect [PATH]`

Inspects the contents of `.gctrail` binary files to view historical data for a directory.

| Option | Shorthand | Default | Description |
| :--- | :--- | :--- | :--- |
| `--depth` | `-d` | `1` | Recursion depth for trail inspection. |

#### Example:
```bash
# View the historical size and top subdirectories of the current path
uvx fsgc inspect .
```

---

## 💻 Supported Environments

*   **Operating Systems:** Linux and macOS (POSIX-compliant filesystems).
*   **Python Versions:** 3.12+ (leveraging modern `asyncio` and `dataclasses`).
*   **Dependencies:** Built on top of `typer` for CLI logic and `rich` for TUI rendering.
