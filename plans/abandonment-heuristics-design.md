# Abandonment Heuristics: Behavioral Detection of Stale User Data

**Status:** spec — awaiting review before implementation plan
**Date:** 2026-06-08
**Authors:** Alex (intent) + Claude (design)

## Motivation

The existing `signatures.yaml` catalog answers a *structural* question:
"this directory IS X (a cache, a venv, a build artifact)." It surfaces
recoverable garbage with high confidence because the structure speaks for
itself.

But a large class of recoverable disk is *behavioral*: the user created
something with intent and then walked away from it. Examples:

- A code prototype repo from 2 years ago, last commit 400 days back.
- `~/Downloads/firefox-installer.dmg` from 8 months ago.
- A 5 GB ML weights file you downloaded once and forgot.
- A `.tar.gz` you decompressed once and never deleted.

None of these match a structural signature. The current scanner walks
right past them. They accumulate.

This spec adds **behavioral rules** as a second, parallel catalog
(`behaviors.yaml`) that the scanner evaluates inline during its existing
walk. Matches surface in the interactive proposal under a clearly-labelled
**REVIEW** section — visually separated from the deletion-grade groups,
never auto-checked, and gated by a typed `yes` confirmation. Behavioral
matches are *user data, not regenerable*, and the UI keeps that distinction
front-of-mind.

## Non-goals

- **Replacing or modifying the structural signature catalog.** Behavioral
  rules are additive; signatures.yaml is untouched.
- **Recovery tiers for behavioral matches.** They're user data — none of
  the trivial/local/network tiers apply. UI categorises them as REVIEW,
  not by recovery cost.
- **Auto-included behavioral sweeps.** A REVIEW item is never checked by
  default. The user must opt in explicitly.
- **Sub-file-level detection.** We work at file or directory granularity,
  not "the last 500 lines of this 10 MB log are uninteresting."
- **Inference about non-local activity.** No "I opened this in another
  app" detection — atime is unreliable on noatime mounts, and we don't
  plumb desktop notification logs.

## Component 1: Schema — `behaviors.yaml` + `BehavioralRule`

New file `src/fsgc/behaviors.yaml` shipped with fsgc. New dataclass
`BehavioralRule` in a new module `src/fsgc/behavior.py`. The catalog
file is loaded by a new `BehavioralRuleManager` (mirror of the existing
`SignatureManager`).

### Rule fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Display name in the REVIEW group. |
| `kind` | yes | `stale_dir` or `stale_file`. |
| `signal` | yes | What clock the rule reads. v1: `git_head_mtime` (stale_dir only) or `file_mtime` (stale_file only). |
| `min_age_days` | yes | Required gap, in days, between `now` and the signal's value. |
| `path_scope` | no | Glob restricting where the rule applies (e.g. `**/Downloads/*`). Matched with `Path.match` semantics. |
| `extensions` | no | List of file extensions (file rules only). At least one must match. |
| `min_size_bytes` | no | Size threshold (file rules only). |

### v1 catalog (shipped)

```yaml
rules:
  - name: "Stale Code Project"
    kind: stale_dir
    signal: git_head_mtime
    min_age_days: 180

  - name: "Old Download"
    kind: stale_file
    signal: file_mtime
    path_scope: "**/Downloads/*"
    min_age_days: 90

  - name: "Forgotten Archive"
    kind: stale_file
    signal: file_mtime
    extensions: [".zip", ".tar.gz", ".tar.xz", ".tgz", ".dmg", ".iso", ".deb", ".AppImage", ".pkg", ".msi"]
    min_age_days: 90

  - name: "Old Large ML Weights"
    kind: stale_file
    signal: file_mtime
    extensions: [".pt", ".pth", ".safetensors", ".bin", ".gguf", ".ckpt", ".onnx", ".h5"]
    min_size_bytes: 524288000   # 500 MB
    min_age_days: 180
```

Users can override by dropping `~/.config/fsgc/behaviors.yaml`, same
override mechanism as signatures.

### Dataclass

```python
# src/fsgc/behavior.py
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

class BehavioralKind(Enum):
    STALE_DIR = "stale_dir"
    STALE_FILE = "stale_file"

class BehavioralSignal(Enum):
    GIT_HEAD_MTIME = "git_head_mtime"
    FILE_MTIME = "file_mtime"

@dataclass
class BehavioralRule:
    name: str
    kind: BehavioralKind
    signal: BehavioralSignal
    min_age_days: int
    path_scope: str | None = None
    extensions: list[str] = field(default_factory=list)
    min_size_bytes: int = 0

@dataclass
class BehavioralMatch:
    path: Path
    rule_name: str
    size_bytes: int
    age_days: int
```

Validation at load time:
- `kind=stale_dir` requires `signal=git_head_mtime` (v1 only supports one
  dir signal).
- `kind=stale_file` requires `signal=file_mtime` (v1).
- `extensions` and `min_size_bytes` are only meaningful for `stale_file`;
  set on `stale_dir` → load-time error.

## Component 2: Detection — where the rules attach

Two pluggable check sites inside the existing scanner walk; no new
filesystem syscalls on the file-rule side, one extra `os.stat` per
candidate directory for the git-head signal.

### `stale_dir` rules — in `_process_directory`

Evaluated right after the existing single `os.stat(node.path)` runs. For
each loaded `stale_dir` rule:

```python
# inside Scanner._process_directory, after the trail short-circuit
for rule in self.behavioral_manager.dir_rules:
    if rule.signal is BehavioralSignal.GIT_HEAD_MTIME:
        head = node.path / ".git" / "HEAD"
        try:
            head_st = await asyncio.to_thread(os.stat, head)
        except (PermissionError, FileNotFoundError):
            continue
        age = self.now - head_st.st_mtime
        if age >= rule.min_age_days * 86400:
            self.behavioral_matches.append(BehavioralMatch(
                path=node.path,
                rule_name=rule.name,
                size_bytes=node.size,  # rolled up after walk; see below
                age_days=int(age / 86400),
            ))
```

Important nuance: `node.size` is only valid *after* MCTS has walked the
subtree. The match is appended at detection time but `size_bytes` is
recomputed in a final post-scan pass over the ledger before the proposal
prints. That pass walks the tree once and looks up `path_to_node[match.path].size`.

A `stale_dir` match does **not** stop the scanner from descending — the
dir might contain other garbage too, and we still want to scan + score it.
The match is purely advisory.

### `stale_file` rules — in `_get_entries`'s file branch

Evaluated inside the existing `for entry_name, entry_path, is_dir, stat in entries:` loop, on the file branch. We already have `stat.st_mtime` and
`stat.st_size` from `_get_entries`; no new syscalls.

```python
# inside Scanner._process_directory, file branch
else:  # is_dir is False
    if stat:
        node.files_size += stat.st_size
        # …existing atime/mtime/evidence code…

        # Behavioral checks
        for rule in self.behavioral_manager.file_rules:
            if rule.min_size_bytes and stat.st_size < rule.min_size_bytes:
                continue
            if rule.extensions and not any(
                entry_name.endswith(ext) for ext in rule.extensions
            ):
                continue
            if rule.path_scope and not entry_path.match(rule.path_scope):
                continue
            age = self.now - stat.st_mtime
            if age >= rule.min_age_days * 86400:
                self.behavioral_matches.append(BehavioralMatch(
                    path=entry_path,
                    rule_name=rule.name,
                    size_bytes=stat.st_size,
                    age_days=int(age / 86400),
                ))
```

### Cache interaction

The trail cache short-circuit (matching fingerprint → skip walking) means
cached subtrees are not re-evaluated against behavioral rules. Acceptable
trade-off for v1:

- **`stale_dir` matches on cached subtrees**: persisted alongside the trail
  on the run that created them, restored on cache hit. Schema addition to
  `TrailRecord`: `behavioral_matches: list[dict]`. On cache hit, these
  records are appended to `scanner.behavioral_matches` as-is.
- **`stale_file` matches inside cached subtrees**: lost on cache hit and
  not regenerated until the next `--no-cache` run. Documented limitation;
  user runs `fsgc scan --no-cache ~` weekly (same cadence as the
  cache-bypass for structural garbage).

The `stale_dir` cache restoration is implemented because the matches are
proportionally large (multi-GB stale repos) and high-value; file-level
restoration is deferred until we see how often it matters.

## Component 3: UX — REVIEW section in the proposal

After scoring and aggregating today's structural groups, a parallel pass
turns `scanner.behavioral_matches` into REVIEW groups. One group per
distinct `rule_name`. Each group lists matched paths sorted by
`size_bytes` descending.

### Proposal layout

```
🗑  Garbage (auto-suggested for cleanup)
   [x] Browser Profile Backup       2.21 GB   (4 dirs)
   [x] Python Bytecode             213.7 MB   (2065 dirs)
   [ ] Python Virtualenv             8.6 GB   (16 dirs)
   …

🔍 Review (suggested — never auto-checked, see and decide)
   [ ] Stale Code Project            4.3 GB   (3 dirs)
       repos/old-prototype-2022      2.1 GB   last commit 412 days ago
       repos/spike-deadline          1.4 GB   last commit 287 days ago
       repos/blog-rewrite            0.8 GB   last commit 198 days ago
   [ ] Old Download                  3.1 GB   (12 files)
       ~/Downloads/firefox-113.dmg   124 MB   downloaded 287 days ago
       …
   [ ] Forgotten Archive             1.4 GB   (8 files)
   [ ] Old Large ML Weights          5.6 GB   (3 files)
```

### Confirmation flow

When any REVIEW item is selected, the confirmation step grows a typed-
`yes` gate:

```
You have 14 items in REVIEW marked for collection.
These are user data, not regenerable garbage.

Continue with REVIEW items moved to trash? Type `yes` to confirm:
```

If the user types anything other than `yes`, the REVIEW items are
**unchecked** and the regular sweep proceeds without them. (Structural
items are untouched by this decision.)

Pure-garbage sweeps — no REVIEW items checked — keep today's
one-keystroke flow.

### Journal

The post-sweep JSONL journal entry gains a `review: true` field for
REVIEW-section deletions. Structural deletions omit the field (or set
`review: false`, TBD by implementer — see "Open implementation choices"
below).

This lets an audit query like:

```bash
jq 'select(.review)' ~/.local/share/fsgc/sweep-log.jsonl
```

…find exactly the behavioral deletions for the "did I just delete
something I shouldn't have?" panic moment.

## Data flow

```
fsgc scan
  ↓
HeuristicEngine + BehavioralRuleManager init
  ↓
Scanner(behavioral_manager=…) constructed
  ↓
scan() walks the tree
  → _process_directory: stale_dir rules check git_head_mtime
  → _get_entries file branch: stale_file rules check mtime/ext/size/scope
  → matches accumulate in scanner.behavioral_matches
  ↓
At scan end:
  → post-pass walks scanner.behavioral_matches, recomputes size for stale_dir
    matches (now that the subtree is fully walked)
  ↓
apply_scoring → group_by_signature → structural groups
  +
behavioral_matches → group_by_rule_name → REVIEW groups
  ↓
proposal renders both sections
  ↓
prompt_confirm_action(has_review_items=True/False)
  → if any REVIEW item selected, gate with typed-`yes`
  ↓
Sweeper sweeps both sets; journal entries flagged with review=bool
```

## Tests

New file `tests/test_behavior.py`:

- `test_behaviors_yaml_loads_to_rule_dataclasses` — the shipped YAML
  round-trips to a list of `BehavioralRule` with no missing fields.
- `test_behavior_rule_validation_rejects_extensions_on_stale_dir` — load-time
  error for nonsense combinations.
- `test_stale_dir_rule_matches_old_git_repo` — tmp fixture: dir with
  `.git/HEAD` mtime 200 days old → matches at 180-day threshold.
- `test_stale_dir_rule_skips_dirs_without_git` — plain dir → no match.
- `test_stale_dir_rule_skips_recent_git_repo` — `.git/HEAD` 30 days old → no
  match at 180-day threshold.
- `test_stale_file_rule_matches_by_age_and_extension` — old `.dmg`
  → matches Forgotten Archive.
- `test_stale_file_rule_respects_min_size_bytes` — 100 MB `.pt` → no
  match; 600 MB `.pt` → match.
- `test_stale_file_rule_respects_path_scope` — old `.dmg` in
  `~/Downloads/` → matches Old Download; same file in `~/Projects/` → does
  NOT match Old Download (but still matches Forgotten Archive).

Extensions to `tests/test_scanner.py`:

- `test_scanner_collects_behavioral_matches_in_ledger` — end-to-end: scanner
  walks a tmp tree, fills `scanner.behavioral_matches` correctly.
- `test_scanner_stale_dir_match_size_is_subtree_total` — git-repo match's
  `size_bytes` reflects the rolled-up subtree size, not just `.git/HEAD`.
- `test_scanner_cache_hit_restores_stale_dir_matches` — second scan with
  warm cache surfaces stale_dir matches via the TrailRecord roundtrip.
- `test_scanner_cache_hit_does_not_re_emit_stale_file_matches` — second
  scan with warm cache does NOT re-emit file-level matches inside cached
  subtrees (documented limitation; not a bug).

CLI integration in `tests/test_scanner_priors.py` or a new
`tests/test_review_flow.py`:

- `test_cli_review_section_only_appears_when_matches_found` — empty tree →
  no REVIEW header; with matches → REVIEW header present.
- `test_cli_review_items_require_typed_yes_confirmation` — InquirerPy mock;
  selecting a REVIEW item flips the prompt to typed-`yes` mode.
- `test_journal_records_review_flag_on_sweep` — JSONL has `review: true`
  for REVIEW items, absent for structural items.

## Acceptance (measured + reported in the implementation commit)

Real-world `fsgc scan ~` on the dev machine (cold cache, 30 s budget):

1. At least one of the four behavioral categories surfaces with **≥1 GB
   total** across at least one match. (Calibration: dev machine has
   confirmed-stale repos, large ML weights, and Downloads bloat.)
2. The REVIEW section is visually distinguished from the garbage section
   in the proposal.
3. The typed-`yes` gate fires when REVIEW items are selected; a
   pure-garbage sweep does NOT require typed confirmation.
4. After a sweep that includes REVIEW items,
   `jq 'select(.review)' ~/.local/share/fsgc/sweep-log.jsonl` returns
   exactly the REVIEW deletions and only them.

Exact numbers go in the commit message and the CHANGELOG.

## What ships in one commit

- `src/fsgc/behavior.py` (new) — `BehavioralKind`, `BehavioralSignal`,
  `BehavioralRule`, `BehavioralMatch`, `BehavioralRuleManager`.
- `src/fsgc/behaviors.yaml` (new) — v1 catalog above.
- `src/fsgc/scanner.py` — `Scanner.__init__` takes
  `behavioral_manager: BehavioralRuleManager | None = None`; new
  `self.behavioral_matches: list[BehavioralMatch]`; check sites in
  `_process_directory`. Post-scan pass recomputes `stale_dir` sizes.
- `src/fsgc/trail.py` — `TrailRecord.behavioral_matches: list[dict]`
  added (default empty). Scanner persists `stale_dir` matches alongside
  the trail; restores them on cache hit.
- `src/fsgc/aggregator.py` — new `group_behavioral_matches(matches)`
  helper, returns REVIEW groups in the proposal's expected shape (mirrors
  `group_by_signature` output enough to share the existing prompt code).
- `src/fsgc/__main__.py` — wires `BehavioralRuleManager`; renders REVIEW
  section in the proposal; threads `has_review_items` through
  `prompt_confirm_action` so it can demand typed-`yes`.
- `src/fsgc/ui/prompt.py` — typed-`yes` gate when REVIEW items present.
- `src/fsgc/sweeper.py` — `DeletionRecord` gains `review: bool = False`;
  journal serializer writes the field.
- `tests/test_behavior.py` (new), extensions to `tests/test_scanner.py`,
  CLI test file.
- `docs/behaviors.md` (new) — user-facing schema doc, mirrors
  `docs/signatures.md`.
- `know-how/adding-behaviors.md` (new) — repo-internal guide on extending
  the catalog.
- `CHANGELOG.md` — `## [Unreleased]` section.
- `TASKS.md` — archive item.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| User has a quietly-active code repo (e.g. they've been browsing files but not committing) → flagged as Stale Code Project. | Never auto-checked. Typed-`yes` gate. Documented signal: "stale git repos = no commits/checkouts in 180 days". The doc explains that "I've been thinking about it" is invisible to fsgc by design. |
| Old Downloads catches a user's deliberate "this is my archive of installers" Downloads folder. | `path_scope: **/Downloads/*` only matches the standard location; user can drop a personal `~/.config/fsgc/behaviors.yaml` overriding the rule. Sweep is trash-by-default, so a misfire is recoverable from system Trash. |
| ML-weights extension list misses a current format (`.gguf`, `.mlx`, etc.). | Extension list is in YAML; user adds an entry. The 500 MB size gate keeps false positives bounded. |
| stale_file rule churn on huge trees: thousands of file matches make the proposal unreadable. | Aggregator groups matches by rule name and shows top N per group; rest collapsed under "+N more". |
| Cache short-circuit hides new stale-file matches inside warm-cache subtrees. | Documented limitation; `--no-cache` is the escape hatch (already exists). |
| Behavioral rules execute git lookups on every scan and slow it down. | `git_head_mtime` signal uses one `os.stat`, not a subprocess. Negligible compared to the existing dir-walk cost. |

## Open implementation choices (let the implementer decide)

These are details where the spec doesn't take a position; the implementer
picks during the implementation plan:

- Whether `DeletionRecord.review` is omitted-when-false (smaller journal,
  `jq 'select(.review)'` still works) or always-emitted (uniform schema,
  audit queries can use `.review == true`).
- Whether the post-scan size recompute walks `scanner.path_to_node` or
  re-walks the tree (former is faster but tightly coupled to scanner
  internals).
- Whether the YAML loader uses a Pydantic model (consistency with beaver's
  conventions in the broader workspace) or a hand-rolled dataclass (no
  new dep beyond what's installed).

## Out of scope / future work

- **Other dir-level signals.** v1 only does `git_head_mtime`. Future:
  `max_subtree_mtime` (any file in the subtree younger than N days exempts
  the dir), `entry_count == 0` (empty dir flagged), `last_command_seen`
  (shell history correlation — privacy concern, deferred).
- **Sub-file analysis.** v1 leaves "first 500 lines of this log are dead"
  out.
- **Live activity correlation.** No notification log, no shell history,
  no desktop activity. Privacy + complexity for marginal signal.
- **Behavioral-rule TTL on `stale_dir` matches in the trail.** v1 reuses
  the 30-day trail TTL, which means a stale repo flagged today may
  silently un-flag itself in 30 days if the trail entry expires and the
  user happens to `git checkout` something in the meantime. Acceptable
  for v1; consider a separate, longer TTL for behavioral entries later.
