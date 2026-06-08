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
