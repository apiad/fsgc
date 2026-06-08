from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class Recovery(Enum):
    """
    How costly it is to restore a directory after fsgc deletes it.

    Drives the score ceiling — a trivially-rebuilt cache can score 1.0;
    a network-dependent dep tree caps at 0.4 even when ancient. Sort
    order in the UI follows: trivial first, network last.
    """

    TRIVIAL = "trivial"  # auto-regenerates on next use, offline
    LOCAL = "local"  # rebuilt from local sources, offline
    NETWORK = "network"  # requires internet to refetch


RECOVERY_CAP: dict[Recovery, float] = {
    Recovery.TRIVIAL: 1.0,
    Recovery.LOCAL: 0.7,
    Recovery.NETWORK: 0.4,
}


@dataclass
class Signature:
    """
    Represents a garbage pattern signature.
    """

    name: str
    pattern: str
    recovery: Recovery
    min_age_days: int = 0
    sentinels: list[str] = field(default_factory=list)

    @property
    def recovery_cap(self) -> float:
        """Maximum score this signature can reach, regardless of recency."""
        return RECOVERY_CAP[self.recovery]


class SignatureManager:
    """
    Manages loading and matching of garbage signatures.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.signatures: list[Signature] = []
        self.default_path = Path(__file__).parent / "signatures.yaml"
        self.user_path = Path.home() / ".config" / "fsgc" / "signatures.yaml"
        self.config_path = config_path or (
            self.user_path if self.user_path.exists() else self.default_path
        )
        self.load()

    def load(self) -> None:
        """
        Load signatures from the YAML configuration file.
        """
        if not self.config_path.exists():
            return

        with open(self.config_path) as f:
            data = yaml.safe_load(f)

        if not data or "signatures" not in data:
            return

        for s in data["signatures"]:
            self.signatures.append(
                Signature(
                    name=s["name"],
                    pattern=s["pattern"],
                    recovery=Recovery(s["recovery"]),
                    min_age_days=int(s.get("min_age_days", 0)),
                    sentinels=s.get("sentinels", []),
                )
            )
