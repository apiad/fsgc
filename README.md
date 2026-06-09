# 🤖 fsgc — Filesystem Garbage Collector

<div align="center">

[![PyPI](https://img.shields.io/pypi/v/fsgc?style=for-the-badge&color=blue)](https://pypi.org/project/fsgc/)
[![License](https://img.shields.io/github/license/apiad/fsgc?style=for-the-badge&color=success)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-apiad.github.io%2Ffsgc-teal?style=for-the-badge)](https://apiad.github.io/fsgc/)

**High-signal filesystem garbage collector.**

*Scans your home with an MCTS-informed stochastic search, proposes deletion of build caches, virtualenvs, dep trees, OS junk — and gives the recoverable user data (stale projects, old downloads, big forgotten ML weights) its own safety-gated lane.*

</div>

<p align="center">
  <img src="docs/img/demo.gif" alt="fsgc scanning a 2.6 GB synthetic tree and proposing deletions" width="100%"/>
</p>

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
fsgc scan ~ --full       # disable the budget; walk everything
```

The first run on a cold cache builds the trail; the second run on the same tree finishes in seconds.

---

## What you'll see

The interactive proposal has **two sections**, deliberately separated:

- **🗑 Garbage** — directories that match a signature (`signatures.yaml`): build caches, virtualenvs, package stores, browser caches, JetBrains caches, system trash. Scored by `recovery_tier × age`; pre-selected when `score > 0.8`.
- **🔍 Review** — abandoned user data caught by behavioral rules (`behaviors.yaml`): stale code projects (180-day `.git/HEAD` mtime), old downloads, forgotten archives, big stale ML weights. Never preselected; gated by a typed-`yes` confirmation before any deletion runs.

## Safety guards

Every node passes three guards on its way to deletion:

1. **Unsafe-root guard** — refuses `/`, `$HOME` itself, and `/usr`, `/etc`, `/var`, `/boot`, `/bin`, `/lib`, `/opt`, `/proc`, `/root`, `/run`, `/sbin`, `/srv`, `/sys` regardless of any signature match.
2. **Symlink guard** — symlinks are never followed; the link stays, the target is untouched.
3. **Sentinel re-verification** — re-stat'd at sweep time; if the sentinel (e.g. `package.json` for `node_modules`) disappeared since the scan, the node is skipped.

Deletion goes to the **system trash** by default (`send2trash`). Pass `--permanent` for `rmtree` semantics. Every action — `trashed`, `deleted`, `dry-run`, `skipped`, `errored` — appends one JSONL line to `~/.local/share/fsgc/sweep-log.jsonl` so the sweep is auditable.

## Performance

`fsgc scan` doesn't enumerate the tree. It runs an MCTS playout, prioritizing children whose name appears in the signature catalog, then children with high historical trash density from the previous scan. A **30-second budget** caps the scan by default; override with `--budget 60` or `--full`.

After the first run, `~/.cache/fsgc/trails.db` holds a fingerprint, top children, and rolled-up size for every directory above 10 MB. On the next scan, unchanged directories match by fingerprint with one `os.stat` and skip the walk entirely — for an unchanged 5 GB subtree, one syscall instead of tens of thousands.

---

## Documentation

Full docs at **<https://apiad.github.io/fsgc/>**:

- [Overview](https://apiad.github.io/fsgc/) — the user-facing tour (with the animation above)
- [Signature catalog](https://apiad.github.io/fsgc/signatures/) — schema, recovery tiers, sentinel verification
- [Behavioral rules](https://apiad.github.io/fsgc/behaviors/) — REVIEW section anatomy, cache interaction, typed-yes flow
- [Architecture](https://apiad.github.io/fsgc/design/) — MCTS playout, incremental propagation, the three-phase pipeline
- [Development](https://apiad.github.io/fsgc/develop/) — contributing, tests, the lint+mypy gate

## Development

```bash
git clone https://github.com/apiad/fsgc.git
cd fsgc
uv sync
make all            # format + lint (ruff + mypy strict) + test
```

See [`AGENTS.md`](AGENTS.md) for orientation if you're an AI agent picking up the codebase, and [`CHANGELOG.md`](CHANGELOG.md) for the release history.

## License

MIT — see [LICENSE](LICENSE).
