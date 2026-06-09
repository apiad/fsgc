# fsgc

> **High-signal filesystem garbage collector.** Scans your home with an MCTS-informed stochastic search, proposes deletion of build caches, virtualenvs, dep trees, OS junk — and gives the recoverable user data (stale code projects, old downloads, big forgotten ML weights) its own safety-gated lane.

<figure markdown="span">
  ![fsgc scanning a 2.6 GB synthetic tree and proposing deletions](img/demo.gif){ width="100%" }
  <figcaption>Live MCTS scan → two-section proposal → interactive sweep. Recorded against a staged demo tree; no actual deletion.</figcaption>
</figure>

---

## Install

```bash
uvx fsgc                 # no-install, recommended
pipx install fsgc        # or pin globally
```

Python 3.12+, Linux + macOS.

## First scan

```bash
fsgc scan ~              # 30-second budget, interactive proposal
fsgc scan . --dry-run    # show what would be collected, change nothing
fsgc scan ~ --full       # disable the wall-clock budget; walk everything
```

The first run on a cold cache builds the trail; the second run on the same tree finishes in seconds (sub-second on the unchanged portion).

---

## How the proposal works

The scan produces **two sections**, deliberately separated.

### 🗑 Garbage — auto-suggested

Directories that match a signature in [`signatures.yaml`](signatures.md): build caches, virtualenvs, package manager stores, browser caches, JetBrains caches, system trash. Each is scored by `recovery_tier × age`:

| Tier | Cap | Meaning |
| :--- | :---: | :--- |
| `trivial` | 1.0 | Regenerates automatically on next use, offline. (`__pycache__`, browser caches, `.ruff_cache`) |
| `local`   | 0.7 | Rebuilds from local sources, offline. (Rust `target/`, C/C++ `build/`) |
| `network` | 0.4 | Requires the internet to refetch. (`node_modules`, `.venv`, `.cache/uv`) |

Groups with `score > 0.8` are pre-selected when the interactive checkbox opens. Everything else stays unchecked — you opt in.

### 🔍 Review — never auto-checked

Directories and files matched by [`behaviors.yaml`](behaviors.md) — *abandoned user data*, not regenerable garbage. v1 catalog:

- **Stale Code Project** — `.git/HEAD` mtime ≥ 180 days old.
- **Old Download** — files under `**/Downloads/*` older than 90 days.
- **Forgotten Archive** — `.zip` / `.tar.gz` / `.dmg` / `.iso` / `.deb` / `.AppImage` / `.pkg` / `.msi` older than 90 days.
- **Old Large ML Weights** — `.pt` / `.safetensors` / `.gguf` / `.ckpt` / `.bin` / `.h5` / `.onnx` ≥ 500 MB and ≥ 180 days.

REVIEW items are visually separated, never preselected, and gated by a typed-`yes` confirmation before the sweep runs. They are *user data*, and fsgc keeps that front-of-mind.

---

## Safety guards

Every node passes three guards on the way to deletion. None can be bypassed without editing the signature catalog or the source.

1. **Unsafe-root guard.** Refuses the filesystem root, your `$HOME` itself, and `/usr`, `/etc`, `/var`, `/boot`, `/bin`, `/lib`, `/opt`, `/proc`, `/root`, `/run`, `/sbin`, `/srv`, `/sys` — regardless of any signature match.
2. **Symlink guard.** Symlinks are never followed; the link stays, the target is untouched, even when the link's name matches a signature pattern.
3. **Sentinel re-verification.** Each directory is re-stat'd at sweep time. If the sentinel that justified the match (e.g. `package.json` for `node_modules`, `pyvenv.cfg` for `.venv`) has disappeared since the scan, the node is skipped — closes the race window between proposal and confirmation.

The deletion itself goes to the **system trash** by default (`send2trash`). For permanent `rmtree` semantics, pass `--permanent`. Every action — `trashed`, `deleted`, `dry-run`, `skipped`, `errored` — appends one JSONL line to `~/.local/share/fsgc/sweep-log.jsonl` so the sweep is auditable after the fact.

```bash
# What did fsgc move to trash today?
jq 'select(.action == "trashed")' ~/.local/share/fsgc/sweep-log.jsonl

# Which REVIEW items did I approve?
jq 'select(.review)' ~/.local/share/fsgc/sweep-log.jsonl
```

---

## Performance

### MCTS-guided scan + wall-clock budget

`fsgc scan` doesn't enumerate everything. It runs an MCTS playout: prioritizing children whose name matches a known garbage pattern (`directory_priors`), then children with high historical trash density from the previous scan (`top_subdirs`), then largest-estimated-size, then random.

A **30-second budget** caps the scan by default. The MCTS surfaces the highest-priority garbage first; the budget cuts off the long tail. Override with `--budget 60` or `--full` for an exhaustive walk.

### Trail cache → sub-second warm runs

After the first scan, `~/.cache/fsgc/trails.db` (single SQLite file, beaver-backed) holds a fingerprint, top children, and rolled-up size for every directory above 10 MB. On the next scan, an unchanged directory matches its cached fingerprint with one `os.stat` and skips the walk entirely — for an unchanged 5 GB subtree, that's one syscall instead of tens of thousands.

```bash
fsgc inspect              # browse what's cached
fsgc inspect ~/Workspace  # filter to a subtree
fsgc scan ~ --no-cache    # force a full walk (use weekly; in-place file edits don't bump parent mtime)
```

The cache TTL is 30 days; entries naturally age out.

---

## Customising

Two YAML catalogs control what fsgc looks for:

| Catalog | User override | What it covers |
| :--- | :--- | :--- |
| [`signatures.yaml`](signatures.md) | `~/.config/fsgc/signatures.yaml` | The structural "this directory IS garbage" rules. |
| [`behaviors.yaml`](behaviors.md) | `~/.config/fsgc/behaviors.yaml` | The behavioral "this user data has been abandoned" rules. |

Either user file fully replaces the bundled default — no merge in v1. Use this when you want a tighter or broader catalog than what ships.

---

## Commands

```text
fsgc scan [PATH]            Run the scan and interactive proposal.
fsgc inspect [PATH]         Show the cached trail entries.
```

To drop the whole trail cache from scratch, remove `~/.cache/fsgc/trails.db` — the next `fsgc scan` recreates it.

The most useful flags on `scan`:

| Flag | Default | What it does |
| :--- | :---: | :--- |
| `--dry-run` | off | Run the full pipeline, simulate the sweep, change nothing. |
| `--budget N` | 30 | Wall-clock cap on the scan phase in seconds. |
| `--full` | off | Disable the budget (mutually exclusive with `--budget`). |
| `--no-cache` | off | Bypass `trails.db` for this run. |
| `--trash` / `--permanent` | `--trash` | Move to system trash (recoverable) vs. permanent `rmtree`. |
| `--no-journal` | off | Suppress the JSONL audit log. |
| `--workers N` | 8 | Concurrent MCTS workers. |
| `--age N` | 90 | Age threshold (days) for the recency multiplier. |

`fsgc --help` and `fsgc scan --help` enumerate the rest.

---

## Where files live

```text
~/.cache/fsgc/trails.db            scan trail cache (beaver-backed SQLite)
~/.local/share/fsgc/sweep-log.jsonl post-sweep audit log
~/.config/fsgc/signatures.yaml     user-supplied signature override (optional)
~/.config/fsgc/behaviors.yaml      user-supplied behavioral override (optional)
```

---

## Going deeper

- **[Signature catalog](signatures.md)** — schema, recovery tiers, sentinel verification, full shipped catalog.
- **[Behavioral rules](behaviors.md)** — REVIEW section anatomy, cache interaction, typed-yes flow.
- **[Architecture](design.md)** — MCTS playout, incremental propagation, the three-phase pipeline.
- **[Development](develop.md)** — contributing, tests, lint+mypy gate, the Gemini CLI framework that the repo also hosts.
- **[Source on GitHub](https://github.com/apiad/fsgc)** — issues, releases, changelog.
