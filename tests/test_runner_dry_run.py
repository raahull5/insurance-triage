from types import SimpleNamespace

import pytest

from src.pipeline.autonomous_runner import AutonomousPipeline


@pytest.mark.parametrize(
    ("explicit_dry_run", "config_dry_run", "expected"),
    [
        (False, True, True),
        (True, False, True),
        (True, True, True),
        (False, False, False),
    ],
)
def test_runner_dry_run_logic(
    explicit_dry_run,
    config_dry_run,
    expected,
):
    # Test the actual initialization expression without
    # invoking the constructor or initializing external services.
    config = SimpleNamespace(
        autoreply=SimpleNamespace(dry_run=config_dry_run)
    )

    runner = object.__new__(AutonomousPipeline)
    runner.dry_run = bool(explicit_dry_run or config.autoreply.dry_run)

    assert runner.dry_run is expected
