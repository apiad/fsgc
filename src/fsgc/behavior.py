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
from typing import Any

import yaml


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
    def _parse(entry: dict[str, Any]) -> BehavioralRule:
        kind = BehavioralKind(entry["kind"])
        signal = BehavioralSignal(entry["signal"])
        extensions = list(entry.get("extensions", []))
        min_size_bytes = int(entry.get("min_size_bytes", 0))

        # Signal-kind compatibility: each signal is valid for exactly one kind.
        if kind is BehavioralKind.STALE_DIR and signal is not BehavioralSignal.GIT_HEAD_MTIME:
            raise ValueError(
                f"rule {entry['name']!r}: signal {signal.value!r} is not valid for kind=stale_dir"
            )
        if kind is BehavioralKind.STALE_FILE and signal is not BehavioralSignal.FILE_MTIME:
            raise ValueError(
                f"rule {entry['name']!r}: signal {signal.value!r} is not valid for kind=stale_file"
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
