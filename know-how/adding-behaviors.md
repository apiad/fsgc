# Adding behavioral rules

## When to reach for it

When you want fsgc to catch a new kind of abandoned user data — a directory or file pattern that signatures.yaml can't describe because the answer depends on *time* rather than *structure*. Examples that fit: stale download bloat, old export dumps, forgotten ML weight files.

If the answer is "this directory IS a cache" — that's a signature, not a behavior. Go to `know-how/adding-signatures.md`.

## The schema

Behavioral rules live in `src/fsgc/behaviors.yaml` and load via `BehavioralRuleManager` in `src/fsgc/behavior.py`. Each rule:

```yaml
- name: "Human-readable name"        # REVIEW group key
  kind: stale_dir | stale_file       # which check site fires
  signal: git_head_mtime | file_mtime  # what clock the rule reads
  min_age_days: 180                  # required gap
  path_scope: "**/Downloads/*"       # optional glob, restricts location
  extensions: [".pt", ".bin"]        # optional (stale_file only)
  min_size_bytes: 524288000          # optional (stale_file only)
```

Both `extensions` and `min_size_bytes` are file-only — the loader rejects them on `stale_dir` rules.

## Procedure

1. **Pick `kind` first** — directory-level matches (whole subtree suggested) or file-level matches (individual files).
2. **Pick the signal** — `git_head_mtime` is currently the only `stale_dir` signal; `file_mtime` is the only `stale_file` signal. New signals require code in `Scanner._check_behavioral_dir_rule` / `_check_behavioral_file_rules`.
3. **Add the entry to `behaviors.yaml`.**
4. **Add a test** in `tests/test_behavior.py` (loading) and `tests/test_scanner_behavioral.py` (matching). Existing tests are the templates.
5. **Run `make test`** to verify.
6. **Update `docs/behaviors.md`** if the rule introduces a new category worth surfacing to users.
7. **Update CHANGELOG.md** under `## [Unreleased]`.

## Adding a new signal

Currently:
- `git_head_mtime` (stale_dir only): mtime of `<dir>/.git/HEAD`.
- `file_mtime` (stale_file only): `stat.st_mtime` of the file.

To add a new signal (e.g. `max_subtree_mtime` for "any file in the subtree younger than N days exempts the dir"):

1. Add a variant to `BehavioralSignal` in `src/fsgc/behavior.py`.
2. Update the signal/kind compatibility check in `BehavioralRuleManager._parse`.
3. Extend `Scanner._check_behavioral_dir_rule` (or `_check_behavioral_file_rules`) with the new signal's logic.
4. Test the new signal's edge cases.

## Cache caveat

`stale_dir` matches persist alongside the trail and restore on cache hit. `stale_file` matches inside cached subtrees don't. Users mitigate with `fsgc scan --no-cache`. If your new rule must surface on warm-cache scans, plumb persistence through `TrailRecord.behavioral_matches` (the schema already accommodates extras).
