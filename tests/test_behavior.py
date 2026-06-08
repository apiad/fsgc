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
