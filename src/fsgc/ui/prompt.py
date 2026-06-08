from typing import Any, cast

from InquirerPy import inquirer

from fsgc.ui.formatter import format_size


def prompt_for_deletion(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Present an interactive checkbox list for selecting garbage groups to delete.
    """
    if not groups:
        print("[yellow]No garbage identified for collection.[/]")
        return []

    choices = []
    for group in groups:
        if group.get("review"):
            label = (
                f"[REVIEW] {group['name']} - {format_size(group['size'])} "
                f"({len(group.get('matches', []))} item(s))"
            )
        else:
            label = (
                f"{group['name']} - {format_size(group['size'])} "
                f"(Avg Score: {group['avg_score']:.2f})"
            )
        choices.append({"name": label, "value": group, "enabled": group["auto_check"]})

    selected = inquirer.checkbox(  # type: ignore
        message="Select garbage groups to collect:",
        choices=choices,
        instruction="(Space to toggle, Enter to confirm)",
        transformer=lambda result: f"{len(result)} groups selected",
    ).execute()

    return cast(list[dict[str, Any]], selected)


def prompt_confirm_action(trash: bool = True) -> str:
    """
    Confirm the final action: Run Collection, Dry Run, or Abort.
    """
    run_label = "Run Collection (Move to Trash)" if trash else "Run Collection (PERMANENT Deletion)"
    result = inquirer.select(  # type: ignore
        message="Choose action:",
        choices=[
            {"name": run_label, "value": "run"},
            {"name": "Dry Run (Show what would be collected)", "value": "dry"},
            {"name": "Abort", "value": "abort"},
        ],
        default="dry",
    ).execute()

    return cast(str, result)


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
