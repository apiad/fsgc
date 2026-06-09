import asyncio
import datetime
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.text import Text
from rich.tree import Tree

from fsgc.aggregator import group_behavioral_matches, group_by_signature, summarize_tree
from fsgc.behavior import BehavioralRuleManager
from fsgc.config import SignatureManager
from fsgc.engine import HeuristicEngine
from fsgc.scanner import DirectoryNode, Scanner
from fsgc.sweeper import DeletionRecord, SkipReason, Sweeper
from fsgc.trail import DEFAULT_DB_PATH, TrailStore
from fsgc.ui.formatter import format_size, format_speed, render_summary_tree
from fsgc.ui.prompt import prompt_confirm_action, prompt_confirm_review, prompt_for_deletion

app = typer.Typer(name="fsgc", help="Heuristic-based filesystem scanner and garbage collector.")
console = Console()


DEFAULT_JOURNAL_PATH = Path.home() / ".local" / "share" / "fsgc" / "sweep-log.jsonl"


_SKIP_REASON_LABELS: dict[SkipReason, str] = {
    SkipReason.UNSAFE_ROOT: "protected system path",
    SkipReason.SYMLINK: "symlink (target preserved)",
    SkipReason.SENTINEL_MISSING: "sentinel disappeared since scan",
    SkipReason.MISSING: "no longer exists",
}


def sweep(
    selected_groups: list[dict[str, Any]],
    dry_run: bool = True,
    trash: bool = True,
    journal_path: Path | None = None,
    max_concurrency: int = 1,
) -> None:
    """
    Perform the actual or simulated deletion of selected garbage nodes via Sweeper,
    streaming progress through a Rich live bar.
    """
    sweeper = Sweeper(
        dry_run=dry_run,
        trash=trash,
        journal_path=journal_path,
        max_concurrency=max_concurrency,
    )

    def _group_item_count(g: dict[str, Any]) -> int:
        if g.get("review"):
            return len(g.get("matches", []))
        return len(g.get("nodes", []))

    def _group_byte_total(g: dict[str, Any]) -> int:
        if g.get("review"):
            return int(g.get("size", 0))
        return sum(n.size for n in g.get("nodes", []))

    total_nodes = sum(_group_item_count(g) for g in selected_groups)
    total_bytes = sum(_group_byte_total(g) for g in selected_groups)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/]"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TransferSpeedColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )

    if dry_run:
        verb_present = "Simulating"
    elif trash:
        verb_present = "Trashing"
    else:
        verb_present = "Deleting"

    with progress:
        task_id = progress.add_task(verb_present, total=total_bytes or 1)

        def on_record(record: DeletionRecord) -> None:
            advance = record.freed_bytes if record.freed_bytes > 0 else 1
            progress.update(task_id, advance=advance)

        result = sweeper.sweep(selected_groups, progress_callback=on_record)

    # Post-sweep summary: report every non-success outcome so failures are visible
    # (the progress bar deliberately suppressed per-record chatter during the run).
    if result.errors:
        console.print("\n[bold red]Errors:[/]")
        for r in result.errors:
            console.print(f"  [red]✗[/] {r.path} — {r.error}")
    if result.skipped:
        console.print("\n[bold blue]Skipped:[/]")
        for r in result.skipped:
            reason = _SKIP_REASON_LABELS[r.skip_reason] if r.skip_reason else "unknown"
            console.print(f"  [blue]·[/] {r.path} ({reason})")

    if dry_run:
        verb_past = "Simulated"
    elif trash:
        verb_past = "Moved to trash"
    else:
        verb_past = "Permanently reclaimed"
    console.print(
        f"\n[bold green]{verb_past} {format_size(result.total_freed_bytes)}[/] "
        f"across {len(result.deleted)} of {total_nodes} item(s)."
    )
    if journal_path is not None and result.records:
        console.print(f"[dim]Sweep journaled to {journal_path}[/]")


def _render_proposal(
    structural_groups: list[dict[str, Any]],
    review_groups: list[dict[str, Any]],
) -> None:
    """Render the two-section proposal. Called before the interactive selection."""
    console.print()
    console.print("[bold green]🗑  Garbage (auto-suggested for cleanup)[/]")
    for g in structural_groups:
        console.print(
            f"   {g['name']:<30} {format_size(g['size']):>10}   (score {g['avg_score']:.2f})"
        )
    if review_groups:
        console.print()
        console.print("[bold yellow]🔍 Review (suggested — never auto-checked, see and decide)[/]")
        for g in review_groups:
            console.print(
                f"   {g['name']:<30} {format_size(g['size']):>10}   "
                f"({len(g.get('matches', []))} item(s))"
            )


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
    use_cache: bool,
    budget_seconds: float | None,
) -> None:
    path = path.resolve()
    console.print(f"[bold blue]Scanning[/] {path}...")
    console.print("[dim blue]Press Ctrl+C to break scanning at any time...\n")

    # Phase 0: Initialize Engine and Signatures
    sig_manager = SignatureManager()
    behavioral_manager = BehavioralRuleManager()
    engine = HeuristicEngine(age_threshold_days=age_threshold)
    # Prime engine.directory_priors before the scan starts so Scanner.select_node
    # can use it on the very first selection.
    engine.get_matching_signature(DirectoryNode(path=path), sig_manager.signatures)
    trail_store = TrailStore() if use_cache else None

    # Phase 1: Scan and build tree (Live Updates)
    scanner = Scanner(
        path,
        engine=engine,
        signatures=sig_manager.signatures,
        max_concurrency=workers,
        trail_store=trail_store,
        budget_seconds=budget_seconds,
        behavioral_manager=behavioral_manager,
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
                        # Calculate speed (avg over last 10s if possible, or since start).
                        # Clamp to >= 0: transient backprop deltas can briefly make
                        # confirmed_size decrease (e.g. when a cache-hit subtree gets
                        # re-rolled-up), and a negative speed display is just noise.
                        speed = 0.0
                        if len(history) > 1:
                            dt = history[-1][0] - history[0][0]
                            ds = history[-1][1] - history[0][1]
                            if dt > 0:
                                speed = max(0.0, ds / dt)

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
            cache_info = ""
            if trail_store is not None:
                total = scanner.cache_hits + scanner.cache_misses
                if total > 0:
                    pct = 100.0 * scanner.cache_hits / total
                    cache_info = f" · cache: {scanner.cache_hits}/{total} hits ({pct:.0f}%)"
            budget_info = ""
            if scanner.timed_out:
                incomplete = sum(
                    1 for n in scanner.path_to_node.values() if not n.is_fully_explored
                )
                budget_info = (
                    f" · [yellow]budget exhausted, {incomplete} dirs incomplete "
                    f"(use --full for thorough)[/yellow]"
                )
            console.print(
                f"\n[bold green]Scanned {format_size(root_node.confirmed_size)} in {duration:.2f}s "
                f"(avg {format_speed(avg_speed)}){cache_info}[/]{budget_info}"
            )

        return root_node

    try:
        root_node = asyncio.run(run_scan())
    except KeyboardInterrupt:
        if trail_store is not None:
            trail_store.close()
        return
    if not root_node:
        if trail_store is not None:
            trail_store.close()
        return

    console.print(f"\nTotal size: [bold]{format_size(root_node.size)}[/].")

    # Phase 3: Mark (Scoring)
    with console.status("[bold yellow]Aggregating heuristic scores...[/]"):
        # We need a way to get all scored nodes from the tree
        node_scores = engine.apply_scoring(root_node, sig_manager.signatures)

    # Phase 4: Aggregate (Grouping)
    groups = group_by_signature(node_scores)
    review_groups = group_behavioral_matches(scanner.behavioral_matches)

    # Phase 5: Prompt (Interactive Selection)
    if not groups and not review_groups:
        console.print("\n[green]Nothing surfaced for review or collection.[/]")
        return

    console.print("\n[bold yellow]Garbage Collection Proposal:[/]")
    _render_proposal(groups, review_groups)
    selected_groups = prompt_for_deletion(groups + review_groups)

    if not selected_groups:
        console.print("[yellow]No items selected. Aborting.[/]")
        return

    # Phase 6: Sweep (Final Action)
    action = "dry" if dry_run else prompt_confirm_action(trash=trash)

    if action == "abort":
        console.print("[red]Aborted.[/]")
    elif action == "dry":
        sweep(
            selected_groups,
            dry_run=True,
            trash=trash,
            journal_path=journal_path,
            max_concurrency=workers,
        )
    elif action == "run":
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

    if trail_store is not None:
        trail_store.close()


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
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help=(
                "Skip the scan-result cache for this run. Without this flag, fsgc "
                f"reads + writes ~/.cache/fsgc/trails.db ({DEFAULT_DB_PATH}) to "
                "skip walking unchanged subtrees on repeat scans."
            ),
        ),
    ] = False,
    budget: Annotated[
        float,
        typer.Option(
            "--budget",
            help=(
                "Wall-clock cap on the scan phase, in seconds. "
                "MCTS surfaces the highest-priority garbage first; the prompt "
                "+ sweep still run on whatever was found. 0 means no cap."
            ),
        ),
    ] = 30.0,
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            help="Disable the scan budget — walk every directory. Shorthand for --budget 0.",
        ),
    ] = False,
) -> None:
    """
    Scans a directory for garbage and proposes collection.
    """
    if full and budget != 30.0:
        raise typer.BadParameter(
            "--full and --budget are mutually exclusive (use one or the other)."
        )
    budget_seconds: float | None
    if full or budget <= 0:
        budget_seconds = None
    else:
        budget_seconds = budget

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
        use_cache=not no_cache,
        budget_seconds=budget_seconds,
    )


def _inspect_label(path: str, record: Any) -> Text:
    label = Text()
    label.append(Path(path).name or path, style="bold blue")
    label.append(" - ", style="dim")
    label.append(format_size(record.total_size), style="green")
    dt = datetime.datetime.fromtimestamp(record.scanned_at, datetime.UTC)
    label.append(f" ({dt.strftime('%Y-%m-%d %H:%M')})", style="dim")
    return label


@app.command(name="inspect")
def inspect(
    path: Annotated[
        Path | None,
        typer.Argument(help="Optional path prefix to filter cached entries."),
    ] = None,
    depth: Annotated[
        int, typer.Option("--depth", "-d", help="Top-child rows to display per entry.")
    ] = 5,
) -> None:
    """
    Inspect cached scan results from ~/.cache/fsgc/trails.db.
    """
    store = TrailStore()
    try:
        keys = sorted(store.keys())
        if path is not None:
            prefix = str(path.resolve())
            keys = [k for k in keys if k == prefix or k.startswith(prefix + "/")]
        if not keys:
            console.print(f"[yellow]No cached trails found at {store.db_path}[/]")
            raise typer.Exit(0)
        for key in keys:
            record = store.get(Path(key))
            if record is None:
                continue
            tree = Tree(_inspect_label(key, record))
            for child in record.top_children[:depth]:
                leaf = Text()
                leaf.append(child.name, style="blue")
                leaf.append(" - ", style="dim")
                leaf.append(format_size(child.size), style="green")
                leaf.append(f"  score={child.score:.2f}", style="dim yellow")
                tree.add(leaf)
            console.print(tree)
        console.print(f"\n[dim]{len(keys)} cached entries at {store.db_path}[/]")
    finally:
        store.close()


def run() -> None:
    """
    Entry point for the CLI.
    """
    app()


if __name__ == "__main__":
    run()
