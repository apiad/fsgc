from pathlib import Path

import pytest
import yaml

from fsgc.behavior import (
    BehavioralKind,
    BehavioralMatch,
    BehavioralRule,
    BehavioralRuleManager,
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


def test_rule_manager_loads_minimal_yaml(tmp_path: Path) -> None:
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


def test_rule_manager_separates_dir_and_file_rules(tmp_path: Path) -> None:
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


def test_rule_manager_rejects_extensions_on_stale_dir(tmp_path: Path) -> None:
    config = tmp_path / "behaviors.yaml"
    config.write_text(yaml.safe_dump({
        "rules": [{
            "name": "X",
            "kind": "stale_dir",
            "signal": "git_head_mtime",
            "min_age_days": 30,
            "extensions": [".zip"],
        }]
    }))
    with pytest.raises(ValueError, match="extensions"):
        BehavioralRuleManager(config_path=config)


def test_rule_manager_rejects_wrong_signal_for_kind(tmp_path: Path) -> None:
    config = tmp_path / "behaviors.yaml"
    config.write_text(yaml.safe_dump({
        "rules": [{
            "name": "X",
            "kind": "stale_file",
            "signal": "git_head_mtime",
            "min_age_days": 30,
        }]
    }))
    with pytest.raises(ValueError, match="signal"):
        BehavioralRuleManager(config_path=config)


def test_rule_manager_empty_when_config_missing(tmp_path: Path) -> None:
    mgr = BehavioralRuleManager(config_path=tmp_path / "nope.yaml")
    assert mgr.rules == []


def test_shipped_behaviors_yaml_loads() -> None:
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
