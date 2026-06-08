from unittest.mock import patch

from fsgc.ui.prompt import prompt_confirm_review


def test_prompt_confirm_review_returns_true_when_user_types_yes() -> None:
    with patch("fsgc.ui.prompt.inquirer") as mock_inq:
        mock_inq.text.return_value.execute.return_value = "yes"
        assert prompt_confirm_review(num_items=3) is True


def test_prompt_confirm_review_returns_false_for_anything_else() -> None:
    for typed in ["", "no", "yeah", "y", "YES", "delete it"]:
        with patch("fsgc.ui.prompt.inquirer") as mock_inq:
            mock_inq.text.return_value.execute.return_value = typed
            assert prompt_confirm_review(num_items=3) is False, f"input {typed!r} should reject"


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
            "size": 4 * 1024**3,
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
        structural_groups=[
            {"name": "X", "size": 1, "avg_score": 0.5, "nodes": [], "signature": None}
        ],
        review_groups=[],
    )
    out = capsys.readouterr().out
    assert "Review" not in out
