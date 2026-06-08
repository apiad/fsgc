# GC Signatures and the Recovery Model

The `signatures.yaml` file is the knowledge base of **fsgc**: which directories qualify as garbage, how to verify them at sweep time, and how their loss compares against the cost of restoring them. This document describes the schema and the scoring it drives.

---

## 📜 Signature Schema

| Field | Required | Description |
| :--- | :--- | :--- |
| `name` | yes | Human-readable group name; surfaces in the UI. |
| `pattern` | yes | Glob; `**/` prefix matches the directory anywhere in the tree. |
| `recovery` | yes | One of `trivial` / `local` / `network`. Sets the score ceiling. |
| `min_age_days` | no | Hard cutoff in days. Younger directories are filtered out entirely. |
| `sentinels` | no | At least one of these filenames/globs must exist inside the directory for the signature to apply — both during the initial scan and again at sweep time. |

### Recovery tiers

The `recovery` tier is the dominant axis of the score. It expresses *how costly it is to restore this directory after fsgc deletes it*:

| Tier | Score cap | Meaning | Examples |
| :--- | :---: | :--- | :--- |
| `trivial` | 1.0 | Regenerates automatically on next use, offline. | `__pycache__`, browser caches, ruff/mypy/pytest cache, JetBrains caches, system trash |
| `local` | 0.7 | Can be rebuilt from local sources, offline. | Rust `target/`, C/C++ `build/`, Go build cache |
| `network` | 0.4 | Requires an internet connection to refetch. | `node_modules`, `.venv`, `.cache/uv`, Hugging Face / Cargo / Maven / Gradle caches |

### Example: Python Virtualenv

```yaml
- name: "Python Virtualenv"
  pattern: "**/.venv"
  recovery: network
  min_age_days: 14
  sentinels: ["pyvenv.cfg"]
```

A `.venv` directory is suggested for collection only if it is at least 14 days old (by `max(atime, mtime)`) and still contains a `pyvenv.cfg`. Because the recovery tier is `network`, the highest score it can reach is 0.4 — it'll always rank below a comparably-stale trivial cache.

---

## 🎚 Scoring formula

```
last_touched = max(node.atime, node.mtime)
age_factor   = clamp(0, 1, (now - last_touched) / age_threshold)
score        = age_factor * RECOVERY_CAP[signature.recovery]
```

`age_threshold` defaults to 90 days (overridable with `--age`). The order that falls out:

| Item | Age | Recovery | Score |
| :--- | :---: | :---: | :---: |
| 6-month-old `__pycache__` | 1.0 | trivial 1.0 | **1.00** |
| 6-month-old Chrome cache | 1.0 | trivial 1.0 | **1.00** |
| 6-month-old Rust `target/` | 1.0 | local 0.7 | 0.70 |
| 6-month-old `node_modules` | 1.0 | network 0.4 | 0.40 |
| 1-week-old `node_modules` | 0.08 | network 0.4 | 0.03 |
| Today's `__pycache__` | 0 | trivial | filtered by `min_age_days: 1` |

The UI sorts groups by `(avg_score, total_size)` descending, so old-and-trivial surfaces first, network-bound and recently-used groups sink to the bottom.

> **Why `max(atime, mtime)`?** Linux mounts default to `noatime`, which means `atime` never updates on read. `mtime` tracks directory-content churn (entries added or removed) and is the right signal for "is this cache still in active use". Taking the max is robust to either being unreliable on a given filesystem.

---

## 🛡 Sentinel verification — at scan and at sweep

To avoid false positives, **fsgc** runs sentinel verification *twice*:

1. **Scan time:** when matching a directory's pattern, the engine checks `node.file_evidence` for at least one sentinel match.
2. **Sweep time:** before deletion, `Sweeper` re-stats the directory and re-checks the sentinels. This catches the race where the scan saw a sentinel that has since disappeared (e.g. you started `npm install` in another shell between scan and confirm).

### Why sentinels matter

Caches and build outputs often have common names like `build` or `dist`. Without sentinels, **fsgc** could suggest deleting a user's source folder called `build`. Strong sentinels keep the catalog safe:

| Pattern | Sentinels |
| :--- | :--- |
| `**/node_modules` | `package.json` |
| `**/.venv` | `pyvenv.cfg` |
| `**/target` | `CACHEDIR.TAG`, `.rustc_info.json` |
| `**/build` | `*.o`, `*.a`, `*.lib`, `CMakeCache.txt` |
| `**/dist` | `*.whl`, `*.tar.gz`, `*.egg-info` |

Generic `**/bin` and `**/obj` were intentionally **removed** from the built-in catalog (no robust sentinel exists; they would match `~/Workspace/bin/`, every `.venv/bin/`, `~/.local/bin/`). Re-add per-user via `~/.config/fsgc/signatures.yaml` if you need C# / .NET cleanup.

---

## ⚙ Customizing the catalog

Drop a `~/.config/fsgc/signatures.yaml` to fully replace the built-in catalog (no merge in v1).

### Tips for new signatures

- **`**/` prefix** — for patterns that should match anywhere in the tree.
- **Conservative `recovery`** — when unsure, prefer `network` so the signature caps low and sorts below trivially-rebuilt caches.
- **Specific sentinels** — `pyvenv.cfg` beats `*.txt`. Unique filenames are the safest evidence.
- **Set `min_age_days`** — at least 1 for actively-written caches (`__pycache__`); larger (7–30) for caches you don't want sweeped on the first day of disuse.

---

## 🏗 Supported ecosystems (built-in)

- **Python:** `__pycache__`, `.venv`, `.tox`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.cache/uv`, `.local/share/uv/python`, `.cache/pip`
- **Node.js:** `node_modules`, `.npm`, `.yarn/cache`, `.pnpm-store`
- **Rust:** `target`, `.cargo/registry`
- **Go:** `.cache/go-build`, `pkg/mod`
- **Java:** `.m2/repository`, `.gradle/caches`, `.gradle/wrapper/dists`
- **ML model caches:** Hugging Face, PyTorch Hub, TensorFlow Hub, KaggleHub
- **Browsers (per-profile):** Chrome, Chromium, Brave, Edge, Vivaldi, Firefox `cache2`
- **Electron desktop apps:** VS Code, Cursor, Slack, Discord, Spotify, JetBrains
- **System:** macOS `.DS_Store`, `.thumbnails`, Linux Trash, Snap, Flatpak per-app caches
- **Generic build outputs:** `build/`, `dist/` (sentinel-gated; bare `bin`/`obj` removed)
