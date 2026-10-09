"""Boundary regressions identified during integration review."""

from __future__ import annotations

import pytest

from data_curator.cli import main
from data_curator.pipeline import validate_recipe


@pytest.mark.parametrize(
    "recipe",
    [
        {"seed": 10**400},
        {"compose": {"weights": {"source": 10**400}}},
        {"create": {"timeout_seconds": 10**400}},
    ],
)
def test_huge_json_integer_is_validation_error_not_arithmetic_crash(recipe):
    with pytest.raises(ValueError, match="finite|between"):
        validate_recipe(recipe)


def test_cli_unknown_run_is_json_error_without_traceback(tmp_path, capsys):
    result = main(
        ["--data-dir", str(tmp_path / "state"), "export", "a" * 32, "--output", str(tmp_path / "out")]
    )
    assert result == 1
    assert '"error"' in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
