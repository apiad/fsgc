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
