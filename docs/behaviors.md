# Behavioral Rules and the REVIEW Section

The `behaviors.yaml` catalog adds a second axis to fsgc: detecting **abandoned user data** that no signature can describe. Examples: a code prototype repo with no commits in 200 days, a `.dmg` installer from 8 months ago, a 5 GB ML weights file you forgot you downloaded.

Matches surface in the proposal's **REVIEW** section — visually separated from the deletion-grade groups, never auto-checked, and gated by a typed `yes` confirmation. Behavioral matches are *user data, not regenerable*, and fsgc keeps that distinction front-of-mind.

---

## 📜 Rule Schema

| Field | Required | Description |
| :--- | :--- | :--- |
| `name` | yes | Display name in the REVIEW group. |
| `kind` | yes | `stale_dir` (directory-level) or `stale_file` (file-level). |
| `signal` | yes | What clock the rule reads. v1: `git_head_mtime` for `stale_dir`, `file_mtime` for `stale_file`. |
| `min_age_days` | yes | Required gap, in days, between *now* and the signal's value. |
| `path_scope` | no | Glob (`Path.match` semantics) restricting where the rule applies. |
| `extensions` | no | List of file extensions (file rules only). At least one must match. |
| `min_size_bytes` | no | Size threshold (file rules only). |

### Example: Stale Code Project

```yaml
- name: "Stale Code Project"
  kind: stale_dir
  signal: git_head_mtime
  min_age_days: 180
```

A directory matches if its `.git/HEAD` exists and that file's mtime is at least 180 days old. The whole directory tree (size rolled up at scan end) is surfaced in REVIEW.

> **Why git HEAD mtime, not git log?** One stat call vs a subprocess per repo. False negatives (you've been browsing without committing) are cheap; false positives (we tell you to delete code you actually use) are costly. mtime updates on commit / checkout / fetch / merge — concrete actions, not "I was thinking about it."

### Example: Old Download

```yaml
- name: "Old Download"
  kind: stale_file
  signal: file_mtime
  path_scope: "**/Downloads/*"
  min_age_days: 90
```

Any file older than 90 days in any `Downloads` directory matches.

---

## 🎚 Confirmation flow

When you select any REVIEW group and choose "Run Collection" in the sweep prompt, fsgc demands you type `yes` verbatim before proceeding. Pure-garbage sweeps don't go through this gate.

The post-sweep JSONL journal at `~/.local/share/fsgc/sweep-log.jsonl` gains a `review: true` field on lines for REVIEW deletions:

```bash
# What REVIEW items did fsgc trash today?
jq 'select(.review)' ~/.local/share/fsgc/sweep-log.jsonl
```

---

## 🛡 Cache interaction

The trail cache short-circuit (matching fingerprint → skip walking) interacts with behavioral rules as follows:

- **`stale_dir` matches** are persisted alongside the trail and restored on cache hit. Once flagged, a stale repo stays flagged across subsequent runs (until the cache TTL expires or you `--no-cache`).
- **`stale_file` matches inside cached subtrees** are not regenerated on warm-cache scans. To force a full file-level re-check, run `fsgc scan --no-cache ~` (the same paranoid-weekly cadence as the structural cache bypass).

---

## ⚙ Customising the catalog

Drop `~/.config/fsgc/behaviors.yaml` to fully replace the built-in catalog (no merge in v1).

### Tips

- Use `path_scope` to constrain rules that would otherwise be too broad (e.g. anchor "old downloads" to your actual downloads folder).
- Pair `extensions` with `min_size_bytes` for "big files of type X" cases (ML weights, raw video).
- Default to conservative `min_age_days` (90+) — false positives in REVIEW are recoverable from system Trash but still annoying.
