from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import ValidationError

from sysadmin_lab.adapters.guest_checks import (
    CommandCheckProvider,
    CommandParameters,
    FileCheckProvider,
    FileParameters,
    ServiceCheckProvider,
)
from sysadmin_lab.application.guest_execution import (
    GuestCommandResult,
    GuestEndpoint,
)
from sysadmin_lab.domain.models import CheckKind, CheckSpec


@dataclass
class Executor:
    results: list[GuestCommandResult]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self,
        endpoint: GuestEndpoint,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GuestCommandResult:
        self.calls.append(arguments)
        return self.results.pop(0)


def endpoint(tmp_path: Path) -> GuestEndpoint:
    return GuestEndpoint("192.0.2.10", "labadmin", (tmp_path / "key").resolve())


def spec(kind: CheckKind, parameters: dict[str, object]) -> CheckSpec:
    return CheckSpec(
        check_id="state-check",
        kind=kind,
        target="node1",
        description="state",
        parameters=parameters,
    )


def result(exit_code: int, stdout: str = "", stderr: str = "") -> GuestCommandResult:
    return GuestCommandResult(exit_code, stdout, stderr)


def test_command_provider_checks_exit_stdout_and_sudo(tmp_path: Path) -> None:
    executor = Executor([result(0, "expected\n")])
    observation = CommandCheckProvider(executor).evaluate(
        spec(
            CheckKind.COMMAND,
            {"arguments": ["printf", "expected"], "stdout_equals": "expected", "sudo": True},
        ),
        endpoint(tmp_path),
    )
    assert observation.passed
    assert executor.calls == [("sudo", "--", "printf", "expected")]

    truncated = GuestCommandResult(0, "long", "", stdout_truncated=True)
    observation = CommandCheckProvider(Executor([truncated])).evaluate(
        spec(CheckKind.COMMAND, {"arguments": ["true"]}), endpoint(tmp_path)
    )
    assert observation.error


def test_command_parameters_reject_ambiguous_stdout_expectations() -> None:
    with pytest.raises(ValidationError, match="only one"):
        CommandParameters.model_validate(
            {"arguments": ["true"], "stdout_equals": "a", "stdout_contains": "a"}
        )


def test_file_provider_matches_metadata_and_content(tmp_path: Path) -> None:
    executor = Executor([result(0, "regular file|root|root|640\n"), result(0)])
    observation = FileCheckProvider(executor).evaluate(
        spec(
            CheckKind.FILE,
            {
                "path": "/etc/example.conf",
                "kind": "file",
                "owner": "root",
                "group": "root",
                "mode": "0640",
                "contains": "enabled=true",
            },
        ),
        endpoint(tmp_path),
    )
    assert observation.passed
    assert executor.calls[1] == (
        "sudo",
        "--",
        "grep",
        "--fixed-strings",
        "--quiet",
        "--",
        "enabled=true",
        "/etc/example.conf",
    )


def test_file_provider_handles_absence_mismatch_and_inspection_error(tmp_path: Path) -> None:
    absent = FileCheckProvider(Executor([result(1)])).evaluate(
        spec(CheckKind.FILE, {"path": "/missing", "exists": False}), endpoint(tmp_path)
    )
    assert absent.passed

    mismatch = FileCheckProvider(Executor([result(0, "directory|user|users|755\n")])).evaluate(
        spec(CheckKind.FILE, {"path": "/file", "kind": "file", "owner": "root"}),
        endpoint(tmp_path),
    )
    assert not mismatch.passed and "type is directory" in mismatch.message

    inspection = FileCheckProvider(
        Executor([result(0, "regular file|root|root|644\n"), result(2)])
    ).evaluate(spec(CheckKind.FILE, {"path": "/file", "contains": "text"}), endpoint(tmp_path))
    assert inspection.error


def test_file_parameters_reject_expectations_for_absent_path() -> None:
    with pytest.raises(ValidationError, match="absent file"):
        FileParameters.model_validate({"path": "/missing", "exists": False, "owner": "root"})


def test_service_provider_checks_loaded_active_and_enabled(tmp_path: Path) -> None:
    executor = Executor([result(0, "loaded\n"), result(0), result(0)])
    observation = ServiceCheckProvider(executor).evaluate(
        spec(CheckKind.SERVICE, {"name": "sshd.service", "active": True, "enabled": True}),
        endpoint(tmp_path),
    )
    assert observation.passed

    inactive = ServiceCheckProvider(Executor([result(0, "loaded\n"), result(3)])).evaluate(
        spec(CheckKind.SERVICE, {"name": "sshd.service", "active": True}), endpoint(tmp_path)
    )
    assert not inactive.passed and "active state" in inactive.message

    missing = ServiceCheckProvider(Executor([result(0, "not-found\n")])).evaluate(
        spec(CheckKind.SERVICE, {"name": "missing.service"}), endpoint(tmp_path)
    )
    assert not missing.passed and "not loaded" in missing.message
