import asyncio
import datetime
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.tree import Tree

from fsgc.aggregator import group_by_signature, summarize_tree
from fsgc.config import SignatureManager
from fsgc.engine import HeuristicEngine
from fsgc.scanner import DirectoryNode, Scanner
from fsgc.sweeper import Action, SkipReason, Sweeper
from fsgc.trail import GCTrail
from fsgc.ui.formatter import format_size, format_speed, render_summary_tree
from fsgc.ui.prompt import prompt_confirm_action, prompt_for_deletion

app = typer.Typer(name="fsgc", help="Heuristic-based filesystem scanner and garbage collector.")
console = Console()


DEFAULT_JOURNAL_PATH = Path.home() / ".local" / "share" / "fsgc" / "sweep-log.jsonl"


_SKIP_REASON_LABELS: dict[SkipReason, str] = {
    SkipReason.UNSAFE_ROOT: "protected system path",
    SkipReason.SYMLINK: "symlink (target preserved)",
    SkipReason.SENTINEL_MISSING: "sentinel disappeared since scan",
    SkipReason.MISSING: "no longer exists",
}

_ACTION_LABELS: dict[Action, tuple[str, str]] = {
    Action.DRY_RUN: ("DRY RUN:", "yellow"),
    Action.TRASHED: ("Trashed:", "green"),
    Action.DELETED: ("Deleted:", "green"),
}


def sweep(
    selected_groups: list[dict[str, Any]],
    dry_run: bool = True,
    trash: bool = True,
    journal_path: Path | None = None,
) -> None:
    """
    Perform the actual or simulated deletion of selected garbage nodes via Sweeper.
    """
    result = Sweeper(
        dry_run=dry_run,
        trash=trash,
        journal_path=journal_path,
    ).sweep(selected_groups)

    current_group: str | None = None
    for record in result.records:
        if record.signature_name != current_group:
            console.print(f"\n[bold]Collecting: {record.signature_name}[/]")
            current_group = record.signature_name
        if record.action in _ACTION_LABELS:
            prefix, color = _ACTION_LABELS[record.action]
            console.print(f"[{color}]{prefix}[/] {record.path} ({format_size(record.freed_bytes)})")
        elif record.skip_reason is not None:
            reason = _SKIP_REASON_LABELS[record.skip_reason]
            console.print(f"[blue]Skipped:[/] {record.path} ({reason})")
        elif record.error is not None:
            console.print(f"[red]Error deleting {record.path}: {record.error}[/]")

    if dry_run:
        verb = "Simulated"
    elif trash:
        verb = "Moved to trash"
    else:
        verb = "Permanently reclaimed"
    console.print(f"\n[bold green]{verb} {format_size(result.total_freed_bytes)}![/]")
    if result.skipped:
        console.print(f"[blue]Skipped {len(result.skipped)} item(s) for safety.[/]")
    if result.errors:
        console.print(f"[red]{len(result.errors)} deletion(s) failed.[/]")
    if journal_path is not None and (result.deleted or result.skipped or result.errors):
        console.print(f"[dim]Sweep journaled to {journal_path}[/]")


def _do_scan(
    path: Path,
    dry_run: bool,
    min_size: int,
    depth: int,
    min_percent: float,
    limit: int,
    age_threshold: int,
    workers: int,
    trash: bool,
    journal_path: Path | None,
) -> None:
    path = path.resolve()
    console.print(f"[bold blue]Scanning[/] {path}...")
    console.print("[dim blue]Press Ctrl+C to break scanning at any time...\n")

    # Phase 0: Initialize Engine and Signatures
    sig_manager = SignatureManager()
    engine = HeuristicEngine(age_threshold_days=age_threshold)

    # Phase 1: Scan and build tree (Live Updates)
    scanner = Scanner(
        path, engine=engine, signatures=sig_manager.signatures, max_concurrency=workers
    )

    async def run_scan() -> DirectoryNode | None:
        root_node = None
        last_update_time = 0.0
        update_interval = 0.1  # 100ms (10Hz refresh)
        start_time = time.time()
        # History of (timestamp, confirmed_size) for speed calculation
        history: deque[tuple[float, int]] = deque(maxlen=100)  # 10s at 10Hz

        try:
            with Live(console=console, refresh_per_second=10) as live:
                async for snapshot in scanner.scan():
                    root_node = snapshot
                    current_time = time.time()
                    history.append((current_time, root_node.confirmed_size))

                    if current_time - last_update_time >= update_interval:
                        # Calculate speed (avg over last 10s if possible, or since start)
                        speed = 0.0
                        if len(history) > 1:
                            dt = history[-1][0] - history[0][0]
                            ds = history[-1][1] - history[0][1]
                            if dt > 0:
                                speed = ds / dt

                        # Phase 2: Hierarchy Summary (Traditional Scan view)
                        summary = summarize_tree(
                            root_node,
                            max_depth=depth,
                            min_percent=min_percent,
                            max_children=limit,
                            min_size=min_size,
                            speed=speed,
                        )
                        tree = render_summary_tree(summary)
                        live.update(tree)
                        last_update_time = current_time
        except asyncio.CancelledError:
            if not root_node:
                # Minimum progress (basic initialization / 1st iteration) not achieved.
                raise KeyboardInterrupt from None
            console.print("\n[bold yellow]Scan interrupted. Proceeding to cleanup...[/]\n")

        if root_node:
            root_node.calculate_metadata()
            duration = time.time() - start_time
            avg_speed = root_node.confirmed_size / duration if duration > 0 else 0
            console.print(
                f"\n[bold green]Scanned {format_size(root_node.confirmed_size)} in {duration:.2f}s "
                f"(avg {format_speed(avg_speed)})[/]"
            )

        return root_node

    try:
        root_node = asyncio.run(run_scan())
    except KeyboardInterrupt:
        return
    if not root_node:
        return

    console.print(f"\nTotal size: [bold]{format_size(root_node.size)}[/].")

    # Phase 3: Mark (Scoring)
    with console.status("[bold yellow]Aggregating heuristic scores...[/]"):
        # We need a way to get all scored nodes from the tree
        node_scores = engine.apply_scoring(root_node, sig_manager.signatures)

    # Phase 4: Aggregate (Grouping)
    groups = group_by_signature(node_scores)

    # Phase 5: Prompt (Interactive Selection)
    if not groups:
        console.print("\n[green]No garbage matching your signatures was found.[/]")
        return

    console.print("\n[bold yellow]Garbage Collection Proposal:[/]")
    selected_groups = prompt_for_deletion(groups)

    if not selected_groups:
        console.print("[yellow]No items selected. Aborting.[/]")
        return

    # Phase 6: Sweep (Final Action)
    action = "dry" if dry_run else prompt_confirm_action(trash=trash)

    if action == "abort":
        console.print("[red]Aborted.[/]")
    elif action == "dry":
        sweep(selected_groups, dry_run=True, trash=trash, journal_path=journal_path)
    elif action == "run":
        sweep(selected_groups, dry_run=False, trash=trash, journal_path=journal_path)


@app.command()
def scan(
    path: Annotated[Path, typer.Argument(help="Root path to start scanning from.")] = Path("."),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be collected without deleting.")
    ] = False,
    min_size: Annotated[
        int, typer.Option("--min-size", help="Minimum size in bytes to report.")
    ] = 0,
    depth: Annotated[int, typer.Option("--depth", "-d", help="Maximum display depth.")] = 2,
    min_percent: Annotated[
        float, typer.Option("--min-percent", "-p", help="Minimum size percentage of parent.")
    ] = 0.01,
    limit: Annotated[
        int, typer.Option("--limit", "-l", help="Maximum number of children to list individually.")
    ] = 10,
    age_threshold: Annotated[
        int, typer.Option("--age", "-a", help="Age threshold in days for recency heuristic.")
    ] = 90,
    workers: Annotated[
        int, typer.Option("--workers", "-w", help="Number of concurrent workers.")
    ] = 8,
    trash: Annotated[
        bool,
        typer.Option(
            "--trash/--permanent",
            help="Move to system trash (default, recoverable) vs permanent rmtree.",
        ),
    ] = True,
    no_journal: Annotated[
        bool,
        typer.Option(
            "--no-journal",
            help=f"Disable the sweep audit log (default: append to {DEFAULT_JOURNAL_PATH}).",
        ),
    ] = False,
) -> None:
    """
    Scans a directory for garbage and proposes collection.
    """
    journal_path = None if no_journal else DEFAULT_JOURNAL_PATH
    _do_scan(
        path,
        dry_run,
        min_size,
        depth,
        min_percent,
        limit,
        age_threshold,
        workers,
        trash,
        journal_path,
    )


def get_inspect_label(path: Path, trail: GCTrail) -> Text:
    label = Text()
    label.append(path.name if path.name else str(path), style="bold blue")
    label.append(" - ", style="dim")
    label.append(format_size(trail.total_size), style="green")
    dt = datetime.datetime.fromtimestamp(trail.timestamp, datetime.UTC)
    ts_str = dt.strftime("%Y-%m-%d %H:%M")
    label.append(f" ({ts_str})", style="dim")
    return label


def build_inspect_tree(path: Path, max_depth: int, current_depth: int = 1) -> Tree | None:
    trail_path = path if (path.is_file() and path.suffix == ".gctrail") else path / ".gctrail"
    if not trail_path.exists():
        return None

    try:
        data = trail_path.read_bytes()
        trail = GCTrail.from_bytes(data)
    except Exception:
        return None

    node_path = path if path.is_dir() else path.parent
    tree = Tree(get_inspect_label(node_path, trail))

    if current_depth < max_depth:
        for sub in trail.top_subdirs:
            subdir_path = node_path / sub.name
            if subdir_path.is_dir():
                sub_tree = build_inspect_tree(subdir_path, max_depth, current_depth + 1)
                if sub_tree:
                    tree.add(sub_tree)
                else:
                    leaf = Text()
                    leaf.append(sub.name, style="blue")
                    leaf.append(" - ", style="dim")
                    leaf.append(format_size(sub.size), style="dim green")
                    tree.add(leaf)
            else:
                leaf = Text()
                leaf.append(sub.name, style="dim blue")
                leaf.append(" - ", style="dim")
                leaf.append(format_size(sub.size), style="dim green")
                tree.add(leaf)
    return tree


@app.command(name="inspect")
def inspect(
    path: Annotated[Path, typer.Argument(help="Path to the directory containing .gctrail.")] = Path(
        "."
    ),
    depth: Annotated[
        int, typer.Option("--depth", "-d", help="Recursion depth for trail inspection.")
    ] = 1,
) -> None:
    """
    Inspect the contents of .gctrail files.
    """
    tree = build_inspect_tree(path, depth)
    if tree:
        console.print(tree)
    else:
        console.print(f"[red]Error:[/] No .gctrail found at {path}")
        raise typer.Exit(1)


def run() -> None:
    """
    Entry point for the CLI.
    """
    app()


if __name__ == "__main__":
    run()
