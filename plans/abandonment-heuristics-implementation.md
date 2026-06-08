# Abandonment Heuristics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a parallel behavioral catalog (`behaviors.yaml`) that surfaces stale code projects, old downloads, forgotten archives, and abandoned large ML weights in a clearly-labelled REVIEW section of the proposal — never auto-checked, gated by typed `yes`.

**Architecture:** Two new dataclasses (`BehavioralRule`, `BehavioralMatch`) and a `BehavioralRuleManager` in a new `src/fsgc/behavior.py`. Two check sites added to `Scanner._process_directory` (one extra `os.stat` per candidate dir for git-head; zero extra syscalls on file rules — reuses the `stat` from `_get_entries`). Matches accumulate in `scanner.behavioral_matches`; `stale_dir` matches persist to `TrailRecord` and restore on cache hit. `Sweeper.DeletionRecord` gains a `review: bool` field that flows through to the JSONL journal.

**Tech Stack:** Python 3.12, dataclasses, PyYAML (already a dep), pytest, beaver-db (existing trail backend), typer (CLI), InquirerPy (interactive prompt).

**Spec:** `plans/abandonment-heuristics-design.md` (committed in `4f9c34b`).

---

## File Structure

| File | Role | Status |
|---|---|---|
| `src/fsgc/behavior.py` | New: enums + dataclasses + `BehavioralRuleManager` | Create |
| `src/fsgc/behaviors.yaml` | New: v1 rule catalog | Create |
| `src/fsgc/scanner.py` | Add `behavioral_manager` arg, `behavioral_matches` ledger, two check sites, post-scan size finalization | Modify |
| `src/fsgc/trail.py` | Extend `TrailRecord` with `behavioral_matches: list[dict]` | Modify |
| `src/fsgc/aggregator.py` | Add `group_behavioral_matches()` helper | Modify |
| `src/fsgc/sweeper.py` | Add `DeletionRecord.review: bool`, plumb through journal | Modify |
| `src/fsgc/ui/prompt.py` | Add typed-`yes` gate when REVIEW items selected | Modify |
| `src/fsgc/__main__.py` | Wire `BehavioralRuleManager`, render REVIEW section, thread `has_review` through prompt | Modify |
| `tests/test_behavior.py` | New: rule loading, validation, per-rule matching | Create |
| `tests/test_scanner_behavioral.py` | New: scanner integration, ledger, cache roundtrip | Create |
| `tests/test_review_flow.py` | New: CLI prompt gating, journal flag | Create |
| `docs/behaviors.md` | New: user-facing schema doc | Create |
| `know-how/adding-behaviors.md` | New: repo-internal guide | Create |
| `CHANGELOG.md` | Add `## [Unreleased]` entry | Modify |
| `TASKS.md` | Archive item under "Done" | Modify |

---

## Task 1: Data types — enums + dataclasses

**Files:**
- Create: `src/fsgc/behavior.py`
- Test: `tests/test_behavior.py`

- [ ] **Step 1: Write failing tests for the dataclasses**

Add to `tests/test_behavior.py`:

```python
from pathlib import Path

from fsgc.behavior import (
    BehavioralKind,
    BehavioralMatch,
    BehavioralRule,
    BehavioralSignal,
)


def test_behavioral_rule_defaults() -> None:
    rule = BehavioralRule(
        name="X",
        kind=BehavioralKind.STALE_DIR,
        signal=BehavioralSignal.GIT_HEAD_MTIME,
        min_age_days=180,
    )
    assert rule.path_scope is None
    assert rule.extensions == []
    assert rule.min_size_bytes == 0


def test_behavioral_rule_with_all_fields() -> None:
    rule = BehavioralRule(
        name="ML Weights",
        kind=BehavioralKind.STALE_FILE,
        signal=BehavioralSignal.FILE_MTIME,
        min_age_days=180,
        path_scope="**/models/*",
        extensions=[".pt", ".safetensors"],
        min_size_bytes=500_000_000,
    )
    assert rule.path_scope == "**/models/*"
    assert ".pt" in rule.extensions
    assert rule.min_size_bytes == 500_000_000


def test_behavioral_match_carries_metadata() -> None:
    match = BehavioralMatch(
        path=Path("/x/y"),
        rule_name="Stale Code Project",
        size_bytes=1024,
        age_days=200,
    )
    assert match.path == Path("/x/y")
    assert match.rule_name == "Stale Code Project"
    assert match.size_bytes == 1024
    assert match.age_days == 200
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd /home/apiad/Workspace/repos/fsgc && uv run pytest tests/test_behavior.py -v`
Expected: `ModuleNotFoundError: No module named 'fsgc.behavior'`

- [ ] **Step 3: Create the module with enums and dataclasses**

Create `src/fsgc/behavior.py`:

```python
"""
Behavioral rules — catches abandoned user data that signatures can't.

Signatures answer "this directory IS X" (a cache, a venv, …). Behavioral
rules answer "this thing was created with intent but has been ignored for
N days." Matches surface in the proposal's REVIEW section, never auto-
checked, gated by typed-yes confirmation. They are NOT regenerable garbage.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class BehavioralKind(Enum):
    STALE_DIR = "stale_dir"
    STALE_FILE = "stale_file"


class BehavioralSignal(Enum):
    GIT_HEAD_MTIME = "git_head_mtime"  # stale_dir only
    FILE_MTIME = "file_mtime"  # stale_file only


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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/fsgc && uv run pytest tests/test_behavior.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/behavior.py tests/test_behavior.py
git commit -m "feat(behavior): introduce BehavioralKind/Signal/Rule/Match dataclasses

Foundation for the abandonment-heuristics feature. Pure data types — no
loader, no detection logic yet. Three tests cover defaults and full-field
construction."
```

---

## Task 2: `BehavioralRuleManager` — YAML loader + validation

**Files:**
- Modify: `src/fsgc/behavior.py`
- Test: `tests/test_behavior.py`

- [ ] **Step 1: Write failing tests for the loader**

Append to `tests/test_behavior.py`:

```python
import pytest
import yaml

from fsgc.behavior import BehavioralRuleManager


def test_rule_manager_loads_minimal_yaml(tmp_path):
    config = tmp_path / "behaviors.yaml"
    config.write_text(yaml.safe_dump({
        "rules": [
            {
                "name": "Stale Code Project",
                "kind": "stale_dir",
                "signal": "git_head_mtime",
                "min_age_days": 180,
            },
            {
                "name": "Old Download",
                "kind": "stale_file",
                "signal": "file_mtime",
                "min_age_days": 90,
                "path_scope": "**/Downloads/*",
            },
        ]
    }))
    mgr = BehavioralRuleManager(config_path=config)
    assert len(mgr.rules) == 2
    assert mgr.rules[0].kind is BehavioralKind.STALE_DIR
    assert mgr.rules[1].path_scope == "**/Downloads/*"


def test_rule_manager_separates_dir_and_file_rules(tmp_path):
    config = tmp_path / "behaviors.yaml"
    config.write_text(yaml.safe_dump({
        "rules": [
            {"name": "A", "kind": "stale_dir", "signal": "git_head_mtime", "min_age_days": 30},
            {"name": "B", "kind": "stale_file", "signal": "file_mtime", "min_age_days": 30},
            {"name": "C", "kind": "stale_file", "signal": "file_mtime", "min_age_days": 30},
        ]
    }))
    mgr = BehavioralRuleManager(config_path=config)
    assert len(mgr.dir_rules) == 1
    assert len(mgr.file_rules) == 2
    assert mgr.dir_rules[0].name == "A"


def test_rule_manager_rejects_extensions_on_stale_dir(tmp_path):
    config = tmp_path / "behaviors.yaml"
    config.write_text(yaml.safe_dump({
        "rules": [{
            "name": "X",
            "kind": "stale_dir",
            "signal": "git_head_mtime",
            "min_age_days": 30,
            "extensions": [".zip"],  # nonsense on a dir rule
        }]
    }))
    with pytest.raises(ValueError, match="extensions"):
        BehavioralRuleManager(config_path=config)


def test_rule_manager_rejects_wrong_signal_for_kind(tmp_path):
    config = tmp_path / "behaviors.yaml"
    config.write_text(yaml.safe_dump({
        "rules": [{
            "name": "X",
            "kind": "stale_file",
            "signal": "git_head_mtime",  # nonsense on a file rule
            "min_age_days": 30,
        }]
    }))
    with pytest.raises(ValueError, match="signal"):
        BehavioralRuleManager(config_path=config)


def test_rule_manager_empty_when_config_missing(tmp_path):
    mgr = BehavioralRuleManager(config_path=tmp_path / "nope.yaml")
    assert mgr.rules == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_behavior.py::test_rule_manager_loads_minimal_yaml -v`
Expected: `ImportError: cannot import name 'BehavioralRuleManager'`

- [ ] **Step 3: Implement the manager**

Append to `src/fsgc/behavior.py`:

```python
import yaml


class BehavioralRuleManager:
    """
    Loads behavioral rules from a YAML catalog. Mirrors SignatureManager's
    shape: optional config_path, falls back to the bundled default in the
    package directory, then to ~/.config/fsgc/behaviors.yaml for user
    overrides.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.rules: list[BehavioralRule] = []
        self.default_path = Path(__file__).parent / "behaviors.yaml"
        self.user_path = Path.home() / ".config" / "fsgc" / "behaviors.yaml"
        self.config_path = config_path or (
            self.user_path if self.user_path.exists() else self.default_path
        )
        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            return
        with open(self.config_path) as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("rules", []):
            self.rules.append(self._parse(entry))

    @staticmethod
    def _parse(entry: dict) -> BehavioralRule:
        kind = BehavioralKind(entry["kind"])
        signal = BehavioralSignal(entry["signal"])
        extensions = list(entry.get("extensions", []))
        min_size_bytes = int(entry.get("min_size_bytes", 0))

        # Signal-kind compatibility: each signal is valid for exactly one kind.
        if kind is BehavioralKind.STALE_DIR and signal is not BehavioralSignal.GIT_HEAD_MTIME:
            raise ValueError(
                f"rule {entry['name']!r}: signal {signal.value!r} is not "
                f"valid for kind=stale_dir"
            )
        if kind is BehavioralKind.STALE_FILE and signal is not BehavioralSignal.FILE_MTIME:
            raise ValueError(
                f"rule {entry['name']!r}: signal {signal.value!r} is not "
                f"valid for kind=stale_file"
            )
        # stale_dir rules cannot use file-only fields.
        if kind is BehavioralKind.STALE_DIR and (extensions or min_size_bytes):
            raise ValueError(
                f"rule {entry['name']!r}: extensions and min_size_bytes are "
                f"only valid for kind=stale_file"
            )

        return BehavioralRule(
            name=entry["name"],
            kind=kind,
            signal=signal,
            min_age_days=int(entry["min_age_days"]),
            path_scope=entry.get("path_scope"),
            extensions=extensions,
            min_size_bytes=min_size_bytes,
        )

    @property
    def dir_rules(self) -> list[BehavioralRule]:
        return [r for r in self.rules if r.kind is BehavioralKind.STALE_DIR]

    @property
    def file_rules(self) -> list[BehavioralRule]:
        return [r for r in self.rules if r.kind is BehavioralKind.STALE_FILE]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_behavior.py -v`
Expected: 8 passed (3 from Task 1 + 5 from Task 2)

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/behavior.py tests/test_behavior.py
git commit -m "feat(behavior): BehavioralRuleManager with YAML loading + validation

Mirrors SignatureManager: default path bundled in the package, user
override at ~/.config/fsgc/behaviors.yaml. Validates signal/kind
compatibility and rejects file-only fields on stale_dir rules.
dir_rules and file_rules properties partition by kind."
```

---

## Task 3: Ship `behaviors.yaml` with v1 catalog

**Files:**
- Create: `src/fsgc/behaviors.yaml`
- Test: `tests/test_behavior.py`

- [ ] **Step 1: Write failing test for the shipped catalog**

Append to `tests/test_behavior.py`:

```python
def test_shipped_behaviors_yaml_loads():
    """The catalog shipped in the package must parse without error."""
    mgr = BehavioralRuleManager()  # uses default path
    rule_names = {r.name for r in mgr.rules}
    assert "Stale Code Project" in rule_names
    assert "Old Download" in rule_names
    assert "Forgotten Archive" in rule_names
    assert "Old Large ML Weights" in rule_names

    # Sanity: each rule is well-formed.
    for rule in mgr.rules:
        assert rule.min_age_days > 0
        if rule.kind is BehavioralKind.STALE_FILE:
            assert rule.signal is BehavioralSignal.FILE_MTIME
        else:
            assert rule.signal is BehavioralSignal.GIT_HEAD_MTIME
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_behavior.py::test_shipped_behaviors_yaml_loads -v`
Expected: FAIL — `Stale Code Project` not in rule names (file doesn't exist yet).

- [ ] **Step 3: Create the shipped catalog**

Create `src/fsgc/behaviors.yaml`:

```yaml
# fsgc behavioral rules catalog v1.
#
# Each rule declares:
#   name          — human-readable name (REVIEW group key).
#   kind          — "stale_dir" or "stale_file".
#   signal        — clock the rule reads:
#                   "git_head_mtime" (stale_dir only) → mtime of <dir>/.git/HEAD
#                   "file_mtime"     (stale_file only) → stat.st_mtime of the file
#   min_age_days  — required gap between now and the signal's value.
#   path_scope    — optional glob (Path.match semantics) restricting where the
#                   rule applies.
#   extensions    — optional list (file rules only); at least one must match.
#   min_size_bytes — optional threshold (file rules only).
#
# Matches surface in the proposal's REVIEW section, never auto-checked,
# gated by typed-yes confirmation. They are USER DATA, not garbage.

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
    extensions:
      - ".zip"
      - ".tar.gz"
      - ".tar.xz"
      - ".tgz"
      - ".dmg"
      - ".iso"
      - ".deb"
      - ".AppImage"
      - ".pkg"
      - ".msi"
    min_age_days: 90

  - name: "Old Large ML Weights"
    kind: stale_file
    signal: file_mtime
    extensions:
      - ".pt"
      - ".pth"
      - ".safetensors"
      - ".bin"
      - ".gguf"
      - ".ckpt"
      - ".onnx"
      - ".h5"
    min_size_bytes: 524288000   # 500 MB
    min_age_days: 180
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_behavior.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/behaviors.yaml tests/test_behavior.py
git commit -m "feat(behavior): ship v1 behaviors.yaml catalog

Four rules: Stale Code Project (180d git head), Old Download (90d in
**/Downloads/*), Forgotten Archive (90d, archive/installer extensions
anywhere), Old Large ML Weights (180d, weight extensions, ≥500 MB)."
```

---

## Task 4: Scanner — `stale_dir` detection + size finalization

**Files:**
- Modify: `src/fsgc/scanner.py`
- Create: `tests/test_scanner_behavioral.py`

- [ ] **Step 1: Write failing test for stale_dir detection**

Create `tests/test_scanner_behavioral.py`:

```python
import asyncio
import os
import time
from pathlib import Path

from fsgc.behavior import (
    BehavioralKind,
    BehavioralRule,
    BehavioralRuleManager,
    BehavioralSignal,
)
from fsgc.scanner import Scanner


def _make_manager(rule: BehavioralRule) -> BehavioralRuleManager:
    """Build a manager with a single rule, bypassing YAML loading."""
    mgr = BehavioralRuleManager.__new__(BehavioralRuleManager)
    mgr.rules = [rule]
    mgr.default_path = Path("/dev/null")
    mgr.user_path = Path("/dev/null")
    mgr.config_path = Path("/dev/null")
    return mgr


def test_scanner_flags_old_git_repo(tmp_path: Path) -> None:
    """A directory with .git/HEAD mtime older than min_age_days is flagged."""
    repo = tmp_path / "old-prototype"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n")
    (repo / "src.py").write_bytes(b"x" * 4096)

    # Set HEAD mtime to 200 days ago.
    ancient = time.time() - (200 * 86400)
    os.utime(head, (ancient, ancient))

    mgr = _make_manager(BehavioralRule(
        name="Stale Code Project",
        kind=BehavioralKind.STALE_DIR,
        signal=BehavioralSignal.GIT_HEAD_MTIME,
        min_age_days=180,
    ))
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert len(scanner.behavioral_matches) == 1
    m = scanner.behavioral_matches[0]
    assert m.path == repo
    assert m.rule_name == "Stale Code Project"
    assert m.age_days >= 199  # rounding tolerance
    # size_bytes is rolled up after walk (includes src.py).
    assert m.size_bytes > 0


def test_scanner_skips_recent_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "active-repo"
    head = repo / ".git" / "HEAD"
    head.parent.mkdir(parents=True)
    head.write_text("ref: refs/heads/main\n")  # fresh mtime by default

    mgr = _make_manager(BehavioralRule(
        name="Stale Code Project",
        kind=BehavioralKind.STALE_DIR,
        signal=BehavioralSignal.GIT_HEAD_MTIME,
        min_age_days=180,
    ))
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []


def test_scanner_skips_dirs_without_git(tmp_path: Path) -> None:
    """A non-git directory, however old, is not a stale code project."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "doc.txt").write_text("x")

    mgr = _make_manager(BehavioralRule(
        name="Stale Code Project",
        kind=BehavioralKind.STALE_DIR,
        signal=BehavioralSignal.GIT_HEAD_MTIME,
        min_age_days=180,
    ))
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_scanner_behavioral.py -v`
Expected: FAIL — `Scanner.__init__() got an unexpected keyword argument 'behavioral_manager'`

- [ ] **Step 3: Add `behavioral_manager` arg + `behavioral_matches` ledger to Scanner**

In `src/fsgc/scanner.py`, add the import near the top (alongside the existing `from fsgc.trail import …` line):

```python
from fsgc.behavior import BehavioralKind, BehavioralMatch, BehavioralRuleManager, BehavioralSignal
```

Modify `Scanner.__init__` signature (locate the existing `__init__` and add the parameter to the keyword args, plus the two new instance fields at the end of the body):

```python
    def __init__(
        self,
        root: Path,
        stay_on_mount: bool = True,
        engine: "Any" = None,
        signatures: list[Signature] | None = None,
        max_concurrency: int = 4,
        trail_store: TrailStore | None = None,
        trail_threshold_mb: int = 0,
        budget_seconds: float | None = None,
        behavioral_manager: BehavioralRuleManager | None = None,
    ) -> None:
        # … existing body unchanged …
        self.behavioral_manager = behavioral_manager
        self.behavioral_matches: list[BehavioralMatch] = []
```

- [ ] **Step 4: Add the stale_dir check in `_process_directory`**

Locate `_process_directory` in `src/fsgc/scanner.py`. Inside the `try:` block, **after** the cache-hit short-circuit `return` and **before** the `entries = await asyncio.to_thread(self._get_entries, node.path)` line, add:

```python
            # Behavioral stale_dir rules — one extra os.stat per candidate per rule.
            if self.behavioral_manager is not None:
                for rule in self.behavioral_manager.dir_rules:
                    await self._check_behavioral_dir_rule(node, rule)
```

Then add a new method on Scanner (place it next to `_process_directory`):

```python
    async def _check_behavioral_dir_rule(
        self, node: DirectoryNode, rule: BehavioralRule
    ) -> None:
        """Apply a single stale_dir rule to a directory node."""
        if rule.signal is BehavioralSignal.GIT_HEAD_MTIME:
            head = node.path / ".git" / "HEAD"
            try:
                head_st = await asyncio.to_thread(os.stat, head)
            except (PermissionError, FileNotFoundError):
                return
            age_seconds = time.time() - head_st.st_mtime
            if age_seconds < rule.min_age_days * 86400:
                return
            # size_bytes is provisional — it'll be rewritten in the post-scan
            # finalize pass once the subtree is fully walked.
            self.behavioral_matches.append(BehavioralMatch(
                path=node.path,
                rule_name=rule.name,
                size_bytes=node.size,
                age_days=int(age_seconds / 86400),
            ))
```

Also import `BehavioralRule` if not already imported (add to the existing fsgc.behavior import).

- [ ] **Step 5: Add the post-scan size finalize**

Locate the end of `scan()` in `src/fsgc/scanner.py` — the `finally:` block + final `yield root_node`. **Before** the final `yield root_node` line, add:

```python
        self._finalize_behavioral_matches()
```

Then add the method:

```python
    def _finalize_behavioral_matches(self) -> None:
        """
        Re-stat stale_dir match sizes from the walked tree. During detection
        we wrote a provisional node.size; by scan end it's been rolled up.
        """
        for i, match in enumerate(self.behavioral_matches):
            node = self.path_to_node.get(match.path)
            if node is not None:
                self.behavioral_matches[i] = BehavioralMatch(
                    path=match.path,
                    rule_name=match.rule_name,
                    size_bytes=node.size,
                    age_days=match.age_days,
                )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_scanner_behavioral.py -v`
Expected: 3 passed

- [ ] **Step 7: Run full test suite as a regression guard**

Run: `cd /home/apiad/Workspace/repos/fsgc && make all`
Expected: every test passes (the 73-74 from before + 3 new). If a pre-existing test fails because of the new param, it means the integration is wrong — fix it before continuing.

- [ ] **Step 8: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/scanner.py tests/test_scanner_behavioral.py
git commit -m "feat(scanner): stale_dir detection via git_head_mtime

Scanner gains optional behavioral_manager + behavioral_matches ledger.
_process_directory checks every stale_dir rule (one extra os.stat per
rule per candidate dir; the git HEAD probe). Matches are appended with
a provisional size that gets rewritten by _finalize_behavioral_matches
once the subtree is walked. Three tests cover the happy path, recent
repos, and non-git dirs."
```

---

## Task 5: Scanner — `stale_file` detection

**Files:**
- Modify: `src/fsgc/scanner.py`
- Modify: `tests/test_scanner_behavioral.py`

- [ ] **Step 1: Write failing tests for file-rule matching**

Append to `tests/test_scanner_behavioral.py`:

```python
def test_scanner_flags_old_download_by_path_scope(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old_dmg = downloads / "firefox-old.dmg"
    old_dmg.write_bytes(b"x" * 4096)
    ancient = time.time() - (100 * 86400)
    os.utime(old_dmg, (ancient, ancient))

    mgr = _make_manager(BehavioralRule(
        name="Old Download",
        kind=BehavioralKind.STALE_FILE,
        signal=BehavioralSignal.FILE_MTIME,
        min_age_days=90,
        path_scope="**/Downloads/*",
    ))
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    matches = [m for m in scanner.behavioral_matches if m.rule_name == "Old Download"]
    assert len(matches) == 1
    assert matches[0].path == old_dmg
    assert matches[0].size_bytes == 4096


def test_scanner_respects_min_size_bytes(tmp_path: Path) -> None:
    """A .pt file at 1 KB never matches the 500 MB rule, even if ancient."""
    tiny = tmp_path / "tiny.pt"
    tiny.write_bytes(b"x" * 1024)
    ancient = time.time() - (365 * 86400)
    os.utime(tiny, (ancient, ancient))

    mgr = _make_manager(BehavioralRule(
        name="Old Large ML Weights",
        kind=BehavioralKind.STALE_FILE,
        signal=BehavioralSignal.FILE_MTIME,
        min_age_days=180,
        extensions=[".pt"],
        min_size_bytes=524_288_000,
    ))
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []


def test_scanner_respects_extensions(tmp_path: Path) -> None:
    """A non-archive file is never a Forgotten Archive, even if old."""
    misc = tmp_path / "old_notes.txt"
    misc.write_bytes(b"x")
    ancient = time.time() - (365 * 86400)
    os.utime(misc, (ancient, ancient))

    mgr = _make_manager(BehavioralRule(
        name="Forgotten Archive",
        kind=BehavioralKind.STALE_FILE,
        signal=BehavioralSignal.FILE_MTIME,
        min_age_days=90,
        extensions=[".zip", ".dmg"],
    ))
    scanner = Scanner(tmp_path, behavioral_manager=mgr, budget_seconds=None)

    async def run() -> None:
        async for _ in scanner.scan():
            pass

    asyncio.run(run())

    assert scanner.behavioral_matches == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_scanner_behavioral.py::test_scanner_flags_old_download_by_path_scope -v`
Expected: FAIL — `behavioral_matches` is empty (file checks not implemented).

- [ ] **Step 3: Add file-rule checks in `_process_directory`'s file branch**

Locate the file branch in `_process_directory` — the `else:` clause inside `for entry_name, entry_path, is_dir, stat in entries:`. After the existing `node.files_size += stat.st_size` / `node.atime = …` / file_evidence block, add:

```python
                if self.behavioral_manager is not None and stat is not None:
                    self._check_behavioral_file_rules(entry_name, entry_path, stat)
```

Then add the method:

```python
    def _check_behavioral_file_rules(
        self, name: str, path: Path, stat: os.stat_result
    ) -> None:
        """Apply every stale_file rule to a single file entry."""
        assert self.behavioral_manager is not None
        for rule in self.behavioral_manager.file_rules:
            if rule.min_size_bytes and stat.st_size < rule.min_size_bytes:
                continue
            if rule.extensions and not any(name.endswith(ext) for ext in rule.extensions):
                continue
            if rule.path_scope and not path.match(rule.path_scope):
                continue
            age_seconds = time.time() - stat.st_mtime
            if age_seconds < rule.min_age_days * 86400:
                continue
            self.behavioral_matches.append(BehavioralMatch(
                path=path,
                rule_name=rule.name,
                size_bytes=stat.st_size,
                age_days=int(age_seconds / 86400),
            ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scanner_behavioral.py -v`
Expected: 6 passed

- [ ] **Step 5: Run full suite as regression guard**

Run: `make all`
Expected: every test still green.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/scanner.py tests/test_scanner_behavioral.py
git commit -m "feat(scanner): stale_file detection (path_scope, extensions, min_size)

File-rule checks fire inside the existing _get_entries file branch using
the stat we already paid for — zero extra syscalls. Three tests cover
path_scope, min_size_bytes, and extensions filters."
```

---

## Task 6: TrailRecord — persist + restore `stale_dir` matches

**Files:**
- Modify: `src/fsgc/trail.py`
- Modify: `src/fsgc/scanner.py`
- Modify: `tests/test_scanner_behavioral.py`

- [ ] **Step 1: Write failing test for the cache roundtrip**

Append to `tests/test_scanner_behavioral.py`:

```python
def test_scanner_stale_dir_match_survives_cache_roundtrip(tmp_path: Path) -> None:
    """
    A stale_dir match found on a cold scan must reappear on a warm-cache scan
    without re-stating the .git/HEAD file. This is how the REVIEW section
    stays populated when fsgc skips walking known-stale subtrees.
    """
    from fsgc.trail import TrailStore

    repo = tmp_path / "old-prototype"
    head = repo / ".git" / "HEAD"
    head.parent.mkdir(parents=True)
    head.write_text("ref: refs/heads/main\n")
    (repo / "blob.bin").write_bytes(b"x" * 4096)
    ancient = time.time() - (200 * 86400)
    os.utime(head, (ancient, ancient))

    mgr = _make_manager(BehavioralRule(
        name="Stale Code Project",
        kind=BehavioralKind.STALE_DIR,
        signal=BehavioralSignal.GIT_HEAD_MTIME,
        min_age_days=180,
    ))
    store = TrailStore(db_path=tmp_path / "trails.db")

    async def cold() -> Scanner:
        s = Scanner(
            tmp_path,
            behavioral_manager=mgr,
            trail_store=store,
            trail_threshold_mb=0,
            budget_seconds=None,
        )
        async for _ in s.scan():
            pass
        return s

    s1 = asyncio.run(cold())
    assert len(s1.behavioral_matches) == 1

    # Warm scan: don't re-walk inside, but the match should be restored
    # from the cached trail.
    async def warm() -> Scanner:
        s = Scanner(
            tmp_path,
            behavioral_manager=mgr,
            trail_store=store,
            trail_threshold_mb=0,
            budget_seconds=None,
        )
        async for _ in s.scan():
            pass
        return s

    s2 = asyncio.run(warm())
    matches = [m for m in s2.behavioral_matches if m.rule_name == "Stale Code Project"]
    assert len(matches) == 1
    assert matches[0].path == repo
    store.close()
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_scanner_behavioral.py::test_scanner_stale_dir_match_survives_cache_roundtrip -v`
Expected: FAIL — second scan has no matches (cache short-circuit skipped detection, no persistence).

- [ ] **Step 3: Extend `TrailRecord` schema**

Modify the `TrailRecord` dataclass in `src/fsgc/trail.py`. Add a new field after `top_children`:

```python
@dataclass
class TrailRecord:
    scanned_at: float
    fingerprint: int
    total_size: int
    entry_count: int
    atime: float
    mtime: float
    file_evidence: list[str]
    top_children: list[TopChild]
    behavioral_matches: list[dict] = field(default_factory=list)
```

Update `to_dict`:

```python
    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_at": self.scanned_at,
            "fingerprint": self.fingerprint,
            "total_size": self.total_size,
            "entry_count": self.entry_count,
            "atime": self.atime,
            "mtime": self.mtime,
            "file_evidence": self.file_evidence,
            "top_children": [
                {"name": c.name, "score": c.score, "size": c.size} for c in self.top_children
            ],
            "behavioral_matches": self.behavioral_matches,
        }
```

Update `from_dict`:

```python
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrailRecord":
        return cls(
            scanned_at=float(data["scanned_at"]),
            fingerprint=int(data["fingerprint"]),
            total_size=int(data["total_size"]),
            entry_count=int(data["entry_count"]),
            atime=float(data["atime"]),
            mtime=float(data["mtime"]),
            file_evidence=list(data.get("file_evidence", [])),
            top_children=[
                TopChild(name=c["name"], score=float(c["score"]), size=int(c["size"]))
                for c in data.get("top_children", [])
            ],
            behavioral_matches=list(data.get("behavioral_matches", [])),
        )
```

- [ ] **Step 4: Make Scanner.persist_trail include behavioral matches**

In `src/fsgc/scanner.py`, locate `persist_trail`. Before building the `TrailRecord(...)` call, gather this node's stale_dir matches:

```python
        # Behavioral stale_dir matches that point at this exact node.
        this_node_matches = [
            {
                "rule_name": m.rule_name,
                "size_bytes": m.size_bytes,
                "age_days": m.age_days,
            }
            for m in self.behavioral_matches
            if m.path == node.path
        ]
```

Then add `behavioral_matches=this_node_matches` to the `TrailRecord(...)` constructor call.

- [ ] **Step 5: Restore matches on cache hit**

In `src/fsgc/scanner.py`, find the cache-hit branch inside `_process_directory` (the block right after `if cached is not None and cached.fingerprint == current_fp:`). After the existing `node.is_fully_explored = True` / `node.state = ScanState.FINISHED` / `self.cache_hits += 1` lines and **before** the `return`, add:

```python
                    # Replay any stale_dir matches recorded for this node.
                    for raw in cached.behavioral_matches:
                        self.behavioral_matches.append(BehavioralMatch(
                            path=node.path,
                            rule_name=raw["rule_name"],
                            size_bytes=int(raw["size_bytes"]),
                            age_days=int(raw["age_days"]),
                        ))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_scanner_behavioral.py -v`
Expected: 7 passed

Run: `uv run pytest tests/test_trail.py -v`
Expected: all existing trail tests still pass (the new field has a default, no test changes required).

Run: `make all`
Expected: full suite green.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/trail.py src/fsgc/scanner.py tests/test_scanner_behavioral.py
git commit -m "feat(trail): persist stale_dir matches alongside trail, restore on cache hit

TrailRecord gains a behavioral_matches list (default empty for back-compat).
persist_trail records the stale_dir matches whose path equals this node's
path. _process_directory's cache-hit branch replays them so the REVIEW
section stays populated when the trail short-circuits the walk. File-level
matches deliberately remain a cache miss — documented limitation."
```

---

## Task 7: Aggregator — `group_behavioral_matches`

**Files:**
- Modify: `src/fsgc/aggregator.py`
- Modify: `tests/test_aggregator_grouping.py`

- [ ] **Step 1: Write failing test for the helper**

Append to `tests/test_aggregator_grouping.py`:

```python
from pathlib import Path

from fsgc.aggregator import group_behavioral_matches
from fsgc.behavior import BehavioralMatch


def test_group_behavioral_matches_groups_by_rule_name() -> None:
    matches = [
        BehavioralMatch(Path("/x/a"), "Old Download", 100, 95),
        BehavioralMatch(Path("/x/b"), "Old Download", 200, 120),
        BehavioralMatch(Path("/y/c"), "Stale Code Project", 1024, 200),
    ]
    groups = group_behavioral_matches(matches)

    assert len(groups) == 2
    by_name = {g["name"]: g for g in groups}
    assert by_name["Old Download"]["size"] == 300
    assert len(by_name["Old Download"]["matches"]) == 2
    assert by_name["Stale Code Project"]["size"] == 1024
    # Sorted by total size descending.
    assert groups[0]["name"] == "Stale Code Project"
    # auto_check is always False for REVIEW groups.
    assert all(g["auto_check"] is False for g in groups)
    # review flag set to True so __main__ knows these go to the REVIEW section.
    assert all(g["review"] is True for g in groups)


def test_group_behavioral_matches_empty_when_no_matches() -> None:
    assert group_behavioral_matches([]) == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_aggregator_grouping.py::test_group_behavioral_matches_groups_by_rule_name -v`
Expected: FAIL — `cannot import name 'group_behavioral_matches'`

- [ ] **Step 3: Implement the helper**

Append to `src/fsgc/aggregator.py`:

```python
from fsgc.behavior import BehavioralMatch


def group_behavioral_matches(matches: list[BehavioralMatch]) -> list[dict[str, Any]]:
    """
    Group behavioral matches by rule name into the same shape the
    interactive prompt consumes for structural groups, plus:

      - review: True            — marks the group as REVIEW (vs garbage)
      - auto_check: False       — REVIEW items are never preselected
      - matches: list[BehavioralMatch]  — per-item detail for the proposal

    Groups are sorted by total size descending so the heaviest review items
    surface first.
    """
    by_name: dict[str, list[BehavioralMatch]] = {}
    for m in matches:
        by_name.setdefault(m.rule_name, []).append(m)

    groups: list[dict[str, Any]] = []
    for name, items in by_name.items():
        groups.append({
            "name": name,
            "size": sum(m.size_bytes for m in items),
            "matches": items,
            "review": True,
            "auto_check": False,
        })
    return sorted(groups, key=lambda g: g["size"], reverse=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_aggregator_grouping.py -v`
Expected: all existing aggregator tests + 2 new ones pass.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/aggregator.py tests/test_aggregator_grouping.py
git commit -m "feat(aggregator): group_behavioral_matches helper

Groups BehavioralMatch by rule_name into the same dict shape the prompt
consumes for structural groups, plus review=True and auto_check=False
flags. Sorted by total size descending."
```

---

## Task 8: Sweeper — `DeletionRecord.review` + journal serialization

**Files:**
- Modify: `src/fsgc/sweeper.py`
- Modify: `tests/test_sweeper.py`

- [ ] **Step 1: Write failing test for the review flag**

Append to `tests/test_sweeper.py`:

```python
def test_sweeper_records_review_flag_on_group(tmp_path: Path) -> None:
    """
    A group marked with review=True flows that flag into every DeletionRecord
    and (when journal_path is set) the JSONL journal line.
    """
    import json

    from fsgc.sweeper import Sweeper

    target = tmp_path / "doomed.bin"
    target.write_bytes(b"x" * 1024)

    group = {
        "name": "Old Download",
        "signature": None,  # behavioral groups carry no signature
        "review": True,
        "nodes": [],  # unused for behavioral
        "matches": [],
        "behavioral_paths": [target],  # paths to sweep
    }
    # The sweeper will need a new affordance to handle review groups whose
    # items are paths, not DirectoryNodes. Done in this same step.
    journal = tmp_path / "log.jsonl"
    sweeper = Sweeper(
        dry_run=False,
        trash=False,  # permanent so the test asserts file deletion
        unsafe_roots=frozenset(),
        journal_path=journal,
    )
    result = sweeper.sweep([group])

    assert not target.exists()
    assert len(result.deleted) == 1
    assert result.deleted[0].review is True

    lines = [json.loads(l) for l in journal.read_text().splitlines() if l]
    assert lines[0]["review"] is True
    assert lines[0]["signature"] == "Old Download"


def test_sweeper_records_review_false_for_structural(tmp_path: Path) -> None:
    """Structural groups omit the review flag (or set it to False)."""
    import json

    from fsgc.scanner import DirectoryNode
    from fsgc.config import Recovery, Signature
    from fsgc.sweeper import Sweeper

    target = tmp_path / "__pycache__"
    target.mkdir()
    (target / "x.pyc").write_bytes(b"x")

    node = DirectoryNode(path=target, size=1)
    sig = Signature(
        name="Python Bytecode", pattern="**/__pycache__", recovery=Recovery.TRIVIAL
    )
    group = {
        "name": "Python Bytecode",
        "signature": sig,
        "nodes": [node],
    }
    journal = tmp_path / "log.jsonl"
    sweeper = Sweeper(
        dry_run=False, trash=False, unsafe_roots=frozenset(), journal_path=journal
    )
    result = sweeper.sweep([group])

    assert len(result.deleted) == 1
    assert result.deleted[0].review is False
    lines = [json.loads(l) for l in journal.read_text().splitlines() if l]
    assert lines[0]["review"] is False
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_sweeper.py::test_sweeper_records_review_flag_on_group -v`
Expected: FAIL — `DeletionRecord` has no `review` attribute / behavioral path handling missing.

- [ ] **Step 3: Add `review: bool` to `DeletionRecord`**

In `src/fsgc/sweeper.py`, locate the `DeletionRecord` dataclass. Add the field:

```python
@dataclass
class DeletionRecord:
    path: Path
    signature_name: str
    action: Action = Action.SKIPPED
    deleted: bool = False
    freed_bytes: int = 0
    skip_reason: SkipReason | None = None
    error: str | None = None
    review: bool = False
```

- [ ] **Step 4: Teach `Sweeper.sweep` to handle behavioral groups**

In `Sweeper.sweep`, the work loop currently iterates `group["nodes"]`. Behavioral groups use `group["behavioral_paths"]` and don't carry a `signature`. Update the loop:

```python
    def sweep(
        self,
        groups: list[dict[str, Any]],
        progress_callback: Callable[[DeletionRecord], None] | None = None,
    ) -> SweepResult:
        work: list[tuple[int, Path, int, Signature | None, str, bool]] = []
        for group in groups:
            group_name: str = group["name"]
            is_review = bool(group.get("review", False))
            if is_review:
                # Behavioral group: items are bare Paths.
                for path in group.get("behavioral_paths", []):
                    size = path.stat().st_size if path.is_file() else 0
                    work.append((len(work), Path(path), size, None, group_name, True))
            else:
                signature: Signature = group["signature"]
                for node in group["nodes"]:
                    work.append(
                        (len(work), node.path, node.size, signature, group_name, False)
                    )

        records_by_idx: dict[int, DeletionRecord] = {}
        if not work:
            return SweepResult()

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            future_to_idx = {
                pool.submit(self._process_one, path, size, sig, gn, rv): i
                for i, path, size, sig, gn, rv in work
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                record = future.result()
                records_by_idx[idx] = record
                self._journal(record)
                if progress_callback is not None:
                    progress_callback(record)

        return SweepResult(records=[records_by_idx[i] for i in range(len(work))])
```

Then update `_process_one`'s signature and behavior — it currently takes
`(node: DirectoryNode, signature: Signature, group_name: str)`. New signature
takes a path, size, optional signature, group name, and review flag:

```python
    def _process_one(
        self,
        path: Path,
        node_size: int,
        signature: Signature | None,
        group_name: str,
        review: bool,
    ) -> DeletionRecord:
        record = DeletionRecord(path=path, signature_name=group_name, review=review)

        if self._is_unsafe_root(path):
            record.skip_reason = SkipReason.UNSAFE_ROOT
            record.action = Action.SKIPPED
            return record

        if path.is_symlink():
            record.skip_reason = SkipReason.SYMLINK
            record.action = Action.SKIPPED
            return record

        if not path.exists():
            record.skip_reason = SkipReason.MISSING
            record.action = Action.SKIPPED
            return record

        # Sentinel re-verification only applies to structural groups.
        if signature is not None and not self._reverify_sentinel(path, signature):
            record.skip_reason = SkipReason.SENTINEL_MISSING
            record.action = Action.SKIPPED
            return record

        if self.dry_run:
            record.deleted = True
            record.freed_bytes = node_size
            record.action = Action.DRY_RUN
            return record

        try:
            if self.trash:
                send2trash(path)
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as e:
            record.error = f"{type(e).__name__}: {e}"
            record.action = Action.ERRORED
            return record

        record.deleted = True
        record.freed_bytes = node_size
        record.action = Action.TRASHED if self.trash else Action.DELETED
        return record
```

- [ ] **Step 5: Add the `review` field to the journal**

In `Sweeper._journal`, locate the `entry = {...}` dict and add the review key:

```python
        entry = {
            "timestamp": self._now().isoformat(),
            "path": str(record.path),
            "signature": record.signature_name,
            "size_bytes": record.freed_bytes,
            "action": record.action.value,
            "detail": detail,
            "review": record.review,
        }
```

- [ ] **Step 6: Run sweeper tests to verify**

Run: `uv run pytest tests/test_sweeper.py -v`
Expected: 18 pre-existing + 2 new = 20 passed. Existing tests should be unaffected because the new field has a default of False and the old `group["nodes"]` shape still works.

Run: `make all`
Expected: full suite green.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/sweeper.py tests/test_sweeper.py
git commit -m "feat(sweeper): review:bool on DeletionRecord + journal field

DeletionRecord gains review:bool (default False), serialised into the
JSONL journal entry. Sweeper.sweep dispatches both structural groups
(nodes + signature) and review groups (behavioral_paths + no sig); the
sentinel re-verification step is skipped for review items since they
have no signature."
```

---

## Task 9: Prompt — typed-`yes` gate when REVIEW items selected

**Files:**
- Modify: `src/fsgc/ui/prompt.py`
- Create: `tests/test_review_flow.py`

- [ ] **Step 1: Write failing test for the prompt gate**

Create `tests/test_review_flow.py`:

```python
from unittest.mock import MagicMock, patch

from fsgc.ui.prompt import prompt_confirm_review


def test_prompt_confirm_review_returns_true_when_user_types_yes() -> None:
    with patch("fsgc.ui.prompt.inquirer") as mock_inq:
        mock_inq.text.return_value.execute.return_value = "yes"
        assert prompt_confirm_review(num_items=3) is True


def test_prompt_confirm_review_returns_false_for_anything_else() -> None:
    for typed in ["", "no", "yeah", "y", "YES", "delete it"]:
        with patch("fsgc.ui.prompt.inquirer") as mock_inq:
            mock_inq.text.return_value.execute.return_value = typed
            assert prompt_confirm_review(num_items=3) is False, (
                f"input {typed!r} should reject"
            )
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_review_flow.py -v`
Expected: FAIL — `cannot import name 'prompt_confirm_review'`

- [ ] **Step 3: Add `prompt_confirm_review` to `src/fsgc/ui/prompt.py`**

Append:

```python
def prompt_confirm_review(num_items: int) -> bool:
    """
    Gate the sweep when REVIEW items are selected. The user must type
    'yes' verbatim (lowercase, no whitespace) to proceed. Anything else
    aborts the REVIEW portion of the sweep.
    """
    msg = (
        f"You have {num_items} item(s) in REVIEW marked for collection.\n"
        f"These are user data, not regenerable garbage.\n"
        f"Type 'yes' to confirm:"
    )
    response = inquirer.text(message=msg).execute()  # type: ignore
    return cast(str, response) == "yes"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_review_flow.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/ui/prompt.py tests/test_review_flow.py
git commit -m "feat(ui): prompt_confirm_review typed-yes gate for REVIEW sweeps

InquirerPy text prompt; only the literal string 'yes' proceeds — case-
sensitive, no whitespace tolerance, no synonyms. Anything else (empty,
'y', 'YES', 'no') aborts the REVIEW portion. Structural sweeps don't
go through this gate."
```

---

## Task 10: CLI wiring — render REVIEW section + thread through prompt

**Files:**
- Modify: `src/fsgc/__main__.py`
- Modify: `tests/test_review_flow.py`

- [ ] **Step 1: Write failing test for the CLI integration**

Append to `tests/test_review_flow.py`:

```python
from pathlib import Path


def test_render_proposal_includes_review_header_when_review_groups_present(
    capsys,
) -> None:
    """
    The proposal output gains a REVIEW header when at least one review group
    has matches, and omits it otherwise.
    """
    from fsgc.__main__ import _render_proposal

    structural_groups = [
        {"name": "Python Bytecode", "size": 1024, "avg_score": 0.7, "nodes": [], "signature": None},
    ]
    review_groups = [
        {
            "name": "Stale Code Project",
            "size": 4 * 1024 ** 3,
            "review": True,
            "matches": [],
            "auto_check": False,
        },
    ]
    _render_proposal(structural_groups, review_groups)
    out = capsys.readouterr().out
    assert "Garbage" in out
    assert "Review" in out
    assert "Stale Code Project" in out


def test_render_proposal_omits_review_header_when_empty(capsys) -> None:
    from fsgc.__main__ import _render_proposal
    _render_proposal(
        structural_groups=[{"name": "X", "size": 1, "avg_score": 0.5, "nodes": [], "signature": None}],
        review_groups=[],
    )
    out = capsys.readouterr().out
    assert "Review" not in out
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_review_flow.py -v`
Expected: FAIL — `_render_proposal` doesn't exist.

- [ ] **Step 3: Wire `BehavioralRuleManager` into `_do_scan` and add `_render_proposal`**

In `src/fsgc/__main__.py`:

Add imports near the existing fsgc imports:

```python
from fsgc.aggregator import group_behavioral_matches
from fsgc.behavior import BehavioralRuleManager
from fsgc.ui.prompt import prompt_confirm_review
```

Add the proposal renderer (somewhere near `sweep` or `_do_scan`):

```python
def _render_proposal(
    structural_groups: list[dict[str, Any]],
    review_groups: list[dict[str, Any]],
) -> None:
    """Render the two-section proposal. Called before the interactive selection."""
    console.print()
    console.print("[bold green]🗑  Garbage (auto-suggested for cleanup)[/]")
    for g in structural_groups:
        console.print(
            f"   {g['name']:<30} {format_size(g['size']):>10}   "
            f"(score {g['avg_score']:.2f})"
        )
    if review_groups:
        console.print()
        console.print("[bold yellow]🔍 Review (suggested — never auto-checked, see and decide)[/]")
        for g in review_groups:
            console.print(
                f"   {g['name']:<30} {format_size(g['size']):>10}   "
                f"({len(g.get('matches', []))} item(s))"
            )
```

In `_do_scan`, construct the manager next to `SignatureManager`:

```python
    sig_manager = SignatureManager()
    behavioral_manager = BehavioralRuleManager()
    engine = HeuristicEngine(age_threshold_days=age_threshold)
    engine.get_matching_signature(DirectoryNode(path=path), sig_manager.signatures)
    trail_store = TrailStore() if use_cache else None

    scanner = Scanner(
        path,
        engine=engine,
        signatures=sig_manager.signatures,
        max_concurrency=workers,
        trail_store=trail_store,
        budget_seconds=budget_seconds,
        behavioral_manager=behavioral_manager,
    )
```

After scoring + grouping, before the prompt:

```python
    # Phase 4.5: Aggregate behavioral matches into REVIEW groups
    review_groups = group_behavioral_matches(scanner.behavioral_matches)

    # Phase 5: Render the two-section proposal and prompt for selection
    if not groups and not review_groups:
        console.print("\n[green]Nothing surfaced for review or collection.[/]")
        return

    _render_proposal(groups, review_groups)
    selected_groups = prompt_for_deletion(groups + review_groups)
```

After `prompt_confirm_action(...)` returns, before invoking `sweep`, gate REVIEW items:

```python
    if action == "run":
        review_selected = [g for g in selected_groups if g.get("review")]
        if review_selected:
            if not prompt_confirm_review(
                num_items=sum(len(g.get("matches", [])) for g in review_selected)
            ):
                console.print("[yellow]REVIEW items not confirmed — excluding from sweep.[/]")
                selected_groups = [g for g in selected_groups if not g.get("review")]
        sweep(
            selected_groups,
            dry_run=False,
            trash=trash,
            journal_path=journal_path,
            max_concurrency=workers,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_review_flow.py -v`
Expected: 4 passed.

Run: `make all`
Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add src/fsgc/__main__.py tests/test_review_flow.py
git commit -m "feat(cli): render REVIEW section + thread typed-yes gate

_do_scan now constructs BehavioralRuleManager, passes it to Scanner,
aggregates behavioral_matches via group_behavioral_matches, and renders
a two-section proposal. When the user selects any REVIEW group and
picks 'run' as the action, prompt_confirm_review fires; on rejection
the REVIEW items are dropped and the structural sweep proceeds."
```

---

## Task 11: Docs — `docs/behaviors.md` + `know-how/adding-behaviors.md`

**Files:**
- Create: `docs/behaviors.md`
- Create: `know-how/adding-behaviors.md`

- [ ] **Step 1: Create the user-facing doc**

Create `docs/behaviors.md`:

```markdown
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
```

- [ ] **Step 2: Create the repo-internal know-how doc**

Create `know-how/adding-behaviors.md`:

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add docs/behaviors.md know-how/adding-behaviors.md
git commit -m "docs: behaviors.md + adding-behaviors.md know-how

User-facing doc mirrors docs/signatures.md (schema, examples, confirmation
flow, cache caveat). Internal know-how doc covers adding rules and adding
new signals."
```

---

## Task 12: Acceptance run + CHANGELOG + TASKS archive

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`

- [ ] **Step 1: Run a real-world acceptance scan**

Run on your machine:

```bash
cd /home/apiad/Workspace/repos/fsgc
rm -f ~/.cache/fsgc/trails.db
uv run python -c "
import asyncio, time
from pathlib import Path
from fsgc.config import SignatureManager
from fsgc.behavior import BehavioralRuleManager
from fsgc.engine import HeuristicEngine
from fsgc.scanner import Scanner, DirectoryNode
from fsgc.aggregator import group_by_signature, group_behavioral_matches
from fsgc.trail import TrailStore

async def main():
    target = Path.home()
    sigs = SignatureManager()
    beh = BehavioralRuleManager()
    engine = HeuristicEngine(age_threshold_days=90)
    engine.get_matching_signature(DirectoryNode(path=target), sigs.signatures)
    store = TrailStore()
    scanner = Scanner(
        target, engine=engine, signatures=sigs.signatures, max_concurrency=8,
        trail_store=store, budget_seconds=30.0, behavioral_manager=beh,
    )
    t0 = time.time()
    async for _ in scanner.scan():
        pass
    scanner.tree.calculate_metadata()
    scores = engine.apply_scoring(scanner.tree, sigs.signatures)
    g = group_by_signature(scores)
    rg = group_behavioral_matches(scanner.behavioral_matches)
    print(f'elapsed {time.time()-t0:.1f}s  structural={len(g)} groups  review={len(rg)} groups')
    for grp in rg:
        print(f\"  REVIEW {grp['name']}: {grp['size']/2**30:.2f} GB ({len(grp['matches'])} items)\")
    store.close()
asyncio.run(main())
"
```

Record the numbers from the output. Expected acceptance criteria:

1. At least one of the four behavioral categories surfaces with ≥1 GB total OR ≥1 match.
2. No exceptions thrown.
3. Wall-clock comparable to a pre-feature scan (budget=30 s, so should be ≤32 s).

If acceptance fails (no behavioral matches found on your machine despite known stale repos / old downloads), inspect the rule thresholds in `behaviors.yaml` and the actual file ages on disk. If a real bug — go back to the relevant task, add a failing test, fix.

- [ ] **Step 2: Update CHANGELOG**

Open `CHANGELOG.md` and add a section under `## [Unreleased]` (or create it if absent):

```markdown
### Behavioral abandonment heuristics (NEW)
- **`behaviors.yaml` catalog** ships four v1 rules that catch what signatures can't: Stale Code Project (180-day `.git/HEAD` mtime), Old Download (90-day file mtime under `**/Downloads/*`), Forgotten Archive (90-day file mtime, archive/installer extensions anywhere), Old Large ML Weights (180-day file mtime, weight extensions, ≥500 MB).
- **REVIEW section in the proposal.** Behavioral matches appear under a clearly-labelled `🔍 Review` header below the structural `🗑  Garbage` groups. Never auto-checked, distinct color, and a typed-`yes` gate fires before any REVIEW item is swept.
- **JSONL journal gains `review: true`.** Sweep entries flagged so `jq 'select(.review)' ~/.local/share/fsgc/sweep-log.jsonl` returns exactly the behavioral deletions.
- **Trail cache integration.** `stale_dir` matches (e.g. Stale Code Project) persist alongside the trail and restore on cache hit — once a stale repo is flagged it stays flagged across subsequent warm scans. `stale_file` matches inside cached subtrees are a documented limitation; `fsgc scan --no-cache` is the escape hatch.
- **Detection cost.** One extra `os.stat` per candidate directory for the git-head signal; zero extra syscalls on file rules (reuses the `stat` already done by `_get_entries`).

### Verification (Behavioral heuristics)
- New `tests/test_behavior.py` (9 tests) covers rule loading + validation + shipped catalog.
- New `tests/test_scanner_behavioral.py` (7 tests) covers stale_dir + stale_file detection + cache roundtrip.
- New `tests/test_review_flow.py` (4 tests) covers prompt gating + proposal rendering + journal flag.
- Real-world acceptance on `~/` (cold cache, 30 s budget): `<RECORD ACTUAL NUMBERS FROM STEP 1 HERE>`.
```

Replace `<RECORD ACTUAL NUMBERS FROM STEP 1 HERE>` with the actual measured output.

- [ ] **Step 3: Archive in TASKS.md**

Open `TASKS.md` and add this line under the Done / Archive section:

```markdown
- [x] **Behavioral abandonment heuristics.** Parallel `behaviors.yaml` catalog (Stale Code Project / Old Download / Forgotten Archive / Old Large ML Weights). REVIEW section in proposal, typed-`yes` confirmation gate, `review: true` JSONL journal flag, trail roundtrip for stale_dir matches. Spec: `plans/abandonment-heuristics-design.md`. (2026-06-08)
```

- [ ] **Step 4: Commit**

```bash
cd /home/apiad/Workspace/repos/fsgc
git add CHANGELOG.md TASKS.md
git commit -m "docs(changelog): behavioral abandonment heuristics

CHANGELOG entry covers the four-rule v1 catalog, REVIEW section UX,
typed-yes gate, JSONL review flag, trail roundtrip, and real-world
acceptance numbers. TASKS.md archives the audit item this closes."
```

- [ ] **Step 5: Push**

```bash
cd /home/apiad/Workspace/repos/fsgc
git push origin main
```

---

## Self-review

This section is a check the implementer can run before pushing.

1. **Spec coverage** — every section of `plans/abandonment-heuristics-design.md` should map to a task here:
   - Component 1 (Schema): Tasks 1, 2, 3 ✓
   - Component 2 (Detection): Tasks 4, 5 ✓
   - Cache interaction (TrailRecord roundtrip): Task 6 ✓
   - Component 3 (UX: REVIEW section + typed-yes + journal flag): Tasks 8, 9, 10 ✓
   - Aggregator helper: Task 7 ✓
   - Tests: distributed across each task ✓
   - Docs: Task 11 ✓
   - CHANGELOG / TASKS / acceptance: Task 12 ✓

2. **Placeholder scan** — search the document for `TBD`, `TODO`, `fill in`, `similar to`, or vague directives. None present (the spec's lone "TBD by implementer" was scoped under *Open implementation choices* and the plan decides them: `review: bool = False` default, journal always emits the field, scanner walks `path_to_node`).

3. **Type consistency**:
   - `BehavioralRule` field names (`name`, `kind`, `signal`, `min_age_days`, `path_scope`, `extensions`, `min_size_bytes`) — consistent across tasks 1, 2, 3, 4, 5, 7, 8, 11.
   - `BehavioralMatch` fields (`path`, `rule_name`, `size_bytes`, `age_days`) — consistent across tasks 1, 4, 5, 6, 7.
   - `DeletionRecord.review: bool = False` — Task 8 defines; Task 8 + 9 use; Task 10 propagates.
   - `group["review"]` discriminator — Task 7 sets; Task 8 + 10 read.
   - `group["behavioral_paths"]` — Task 8 introduces; Task 10 produces (via the aggregator output shape from Task 7… wait).

   **Inconsistency caught**: Task 7's aggregator output has `matches: list[BehavioralMatch]` but Task 8's sweeper reads `group["behavioral_paths"]`. The proposal/prompt flow needs to convert one to the other. **Fix inline**: In Task 10's `_do_scan` block right before `sweep(...)`, materialise `behavioral_paths` from `matches`:

   ```python
       # Sweeper consumes a flat list of paths; build it from BehavioralMatch.
       for g in selected_groups:
           if g.get("review"):
               g["behavioral_paths"] = [m.path for m in g.get("matches", [])]
   ```

   **Updated Task 10 — Step 3 — `_do_scan` block** (replace the gate code in that step with):

   ```python
       if action == "run":
           # Sweeper consumes a flat list of paths for review groups.
           for g in selected_groups:
               if g.get("review"):
                   g["behavioral_paths"] = [m.path for m in g.get("matches", [])]
           review_selected = [g for g in selected_groups if g.get("review")]
           if review_selected:
               if not prompt_confirm_review(
                   num_items=sum(len(g.get("behavioral_paths", [])) for g in review_selected)
               ):
                   console.print("[yellow]REVIEW items not confirmed — excluding from sweep.[/]")
                   selected_groups = [g for g in selected_groups if not g.get("review")]
           sweep(
               selected_groups,
               dry_run=False,
               trash=trash,
               journal_path=journal_path,
               max_concurrency=workers,
           )
   ```

   Apply this fix during Task 10 implementation.

That's the plan.
