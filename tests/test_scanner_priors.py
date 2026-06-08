"""
Tests for the signature-derived MCTS prior (engine.directory_priors) and the
new tier-1.5 select_node behavior that uses it.

The prior gives MCTS a strong "where to look first" hint on cold cache, before
the trail tier has any history to rely on. It's built from the signature
catalog's literal path components, so adding a signature automatically widens
the prior — no parallel hardcoded list.
"""

from pathlib import Path

from fsgc.config import Recovery, Signature
from fsgc.engine import HeuristicEngine
from fsgc.scanner import DirectoryNode, Scanner


def _prime(engine: HeuristicEngine, sigs: list[Signature]) -> None:
    """The priors map is populated lazily inside _get_matchers; force it."""
    engine.get_matching_signature(DirectoryNode(path=Path("__irrelevant__")), sigs)  # noqa: S108


# ── engine.directory_priors ─────────────────────────────────────────────────


def test_engine_builds_directory_priors_from_signatures() -> None:
    engine = HeuristicEngine()
    sigs = [
        Signature(name="A", pattern="**/__pycache__", recovery=Recovery.TRIVIAL),
        Signature(name="B", pattern="**/node_modules", recovery=Recovery.NETWORK),
        Signature(name="C", pattern="**/.cache/uv", recovery=Recovery.NETWORK),
    ]
    _prime(engine, sigs)

    # Every literal path component contributes to the map.
    assert engine.directory_priors["__pycache__"] == 1.0  # TRIVIAL
    assert engine.directory_priors["node_modules"] == 0.4  # NETWORK
    assert engine.directory_priors[".cache"] == 0.4  # via the **/.cache/uv pattern
    assert engine.directory_priors["uv"] == 0.4
    # No spurious keys for ** or for unrelated names
    assert "**" not in engine.directory_priors
    assert "Documents" not in engine.directory_priors


def test_engine_directory_priors_takes_max_cap_per_literal() -> None:
    """A literal appearing in both NETWORK and TRIVIAL sigs maps to TRIVIAL (1.0)."""
    engine = HeuristicEngine()
    sigs = [
        Signature(name="UV", pattern="**/.cache/uv", recovery=Recovery.NETWORK),
        Signature(name="Chrome", pattern="**/.cache/google-chrome", recovery=Recovery.TRIVIAL),
    ]
    _prime(engine, sigs)

    # `.cache` appears in both → max(NETWORK=0.4, TRIVIAL=1.0) = 1.0
    assert engine.directory_priors[".cache"] == 1.0


def test_engine_skips_glob_components_when_building_priors() -> None:
    """Glob segments (`*`, `?`, `[abc]`) and `**` must NOT enter the map."""
    engine = HeuristicEngine()
    sigs = [
        Signature(
            name="ChromeProfile",
            pattern="**/.config/google-chrome/*/Cache",
            recovery=Recovery.TRIVIAL,
        ),
    ]
    _prime(engine, sigs)

    assert engine.directory_priors[".config"] == 1.0
    assert engine.directory_priors["google-chrome"] == 1.0
    assert engine.directory_priors["Cache"] == 1.0
    assert "*" not in engine.directory_priors
    assert "**" not in engine.directory_priors


# ── scanner select_node tier 1.5 ────────────────────────────────────────────


def test_scanner_select_node_prefers_high_prior_child_over_larger_size() -> None:
    """
    With the prior active, a smaller but high-value child (.cache) beats a
    larger neutral child (Documents).
    """
    sigs = [Signature(name="Chrome", pattern="**/.cache/google-chrome", recovery=Recovery.TRIVIAL)]
    engine = HeuristicEngine()
    _prime(engine, sigs)

    scanner = Scanner(Path("/mock"), engine=engine, signatures=sigs)
    root = DirectoryNode(path=Path("/mock"))
    cache = DirectoryNode(path=Path("/mock/.cache"))
    docs = DirectoryNode(path=Path("/mock/Documents"))
    cache.estimated_size = 1
    docs.estimated_size = 1_000_000_000
    cache.visits = 1
    docs.visits = 1
    root.add_child(".cache", cache)
    root.add_child("Documents", docs)

    selected = scanner.select_node(root)
    assert selected is cache, "prior should win over raw size"


def test_scanner_select_node_falls_through_when_no_prior_matches() -> None:
    """When no child's name is in the prior map, behavior reverts to size/random fallback."""
    sigs = [Signature(name="Cache", pattern="**/.cache/uv", recovery=Recovery.NETWORK)]
    engine = HeuristicEngine()
    _prime(engine, sigs)

    scanner = Scanner(Path("/mock"), engine=engine, signatures=sigs)
    root = DirectoryNode(path=Path("/mock"))
    music = DirectoryNode(path=Path("/mock/Music"))
    pictures = DirectoryNode(path=Path("/mock/Pictures"))
    music.estimated_size = 100
    pictures.estimated_size = 500
    music.visits = 1
    pictures.visits = 1
    root.add_child("Music", music)
    root.add_child("Pictures", pictures)

    # Neither matches the prior; fallback picks largest estimated_size.
    selected = scanner.select_node(root)
    assert selected is pictures


# ── CLI mutex check ────────────────────────────────────────────────────────


def test_cli_rejects_full_and_budget_together() -> None:
    """`--full --budget 30` is a friendly error, not a silent override."""
    from typer.testing import CliRunner

    from fsgc.__main__ import app

    runner = CliRunner()
    result = runner.invoke(app, ["scan", ".", "--full", "--budget", "30"])
    assert result.exit_code != 0
    assert "mutually exclusive" in (result.output + str(result.exception)).lower()
