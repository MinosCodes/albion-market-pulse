from __future__ import annotations

import io
import sys
from albion_flips.cli import main


def test_cli_offline_once() -> None:
    """Verify that CLI in offline once mode executes cleanly with return code 0."""
    result = main(["--once", "--offline", "tests/fixtures"])
    assert result == 0


def test_cli_sort_margin() -> None:
    """Verify sorting by margin works without error."""
    result = main(["--once", "--offline", "tests/fixtures", "--sort", "margin"])
    assert result == 0


def test_cli_invalid_config_exit() -> None:
    """Verify CLI returns non-zero error code on invalid config."""
    result = main(["--config", "nonexistent_file.json"])
    assert result != 0
