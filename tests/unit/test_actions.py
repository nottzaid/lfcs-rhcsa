from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sysadmin_lab.application.actions import ActionExecutionError, ActionRunner
from sysadmin_lab.application.guest_execution import GuestCommandResult, GuestEndpoint
from sysadmin_lab.domain.actions import ActionManifest


@dataclass
class Executor:
    results: list[GuestCommandResult]
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self,
        endpoint: GuestEndpoint,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GuestCommandResult:
        self.commands.append(arguments)
        return self.results.pop(0)


def manifest() -> ActionManifest:
    return ActionManifest.model_validate(
        {
            "actions": [
                {"action_id": "create-group", "target": "node1", "arguments": ["true"]},
                {
                    "action_id": "repair-user",
                    "target": "node1",
                    "arguments": ["false"],
                    "expected_exit_codes": [0, 1],
                },
            ]
        }
    )


def endpoint(tmp_path: Path) -> GuestEndpoint:
    return GuestEndpoint("192.0.2.10", "labadmin", (tmp_path / "key").resolve())


def test_action_runner_executes_in_order_with_declared_exit_codes(tmp_path: Path) -> None:
    executor = Executor([GuestCommandResult(0, "", ""), GuestCommandResult(1, "", "")])
    results = ActionRunner(executor).run(manifest(), {"node1": endpoint(tmp_path)})
    assert [item.action_id for item in results] == ["create-group", "repair-user"]
    assert executor.commands == [("true",), ("false",)]


def test_action_runner_reports_missing_target_failure_and_truncation(tmp_path: Path) -> None:
    with pytest.raises(ActionExecutionError, match="no endpoint"):
        ActionRunner(Executor([])).run(manifest(), {})

    failure = Executor([GuestCommandResult(2, "", "denied")])
    with pytest.raises(ActionExecutionError, match="exited 2: denied"):
        ActionRunner(failure).run(manifest(), {"node1": endpoint(tmp_path)})

    truncated = Executor([GuestCommandResult(0, "", "", stdout_truncated=True)])
    with pytest.raises(ActionExecutionError, match="output limit"):
        ActionRunner(truncated).run(manifest(), {"node1": endpoint(tmp_path)})
