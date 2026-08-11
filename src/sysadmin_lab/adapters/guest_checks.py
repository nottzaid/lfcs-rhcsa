from __future__ import annotations

import re
from typing import ClassVar, Literal, Self

from pydantic import Field, model_validator

from sysadmin_lab.application.guest_execution import GuestEndpoint, GuestExecutor
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.domain.models import CheckKind, CheckSpec, StrictModel

UNIT_PATTERN = re.compile(r"^[A-Za-z0-9@_.:-]+$")


class CommandParameters(StrictModel):
    arguments: tuple[str, ...] = Field(min_length=1)
    expected_exit_code: int = 0
    stdout_equals: str | None = None
    stdout_contains: str | None = None
    sudo: bool = False
    timeout_seconds: float = Field(default=10, gt=0, le=60)

    @model_validator(mode="after")
    def only_one_stdout_expectation(self) -> Self:
        if self.stdout_equals is not None and self.stdout_contains is not None:
            raise ValueError("command checks accept only one stdout expectation")
        return self


class FileParameters(StrictModel):
    path: str = Field(pattern=r"^/[^\x00]*$")
    exists: bool = True
    kind: Literal["file", "directory", "symlink"] | None = None
    owner: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    group: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    mode: str | None = Field(default=None, pattern=r"^0?[0-7]{3,4}$")
    contains: str | None = None
    sudo: bool = True
    timeout_seconds: float = Field(default=10, gt=0, le=60)

    @model_validator(mode="after")
    def absent_files_have_no_state_expectations(self) -> Self:
        if not self.exists and any(
            value is not None
            for value in (self.kind, self.owner, self.group, self.mode, self.contains)
        ):
            raise ValueError("absent file checks cannot define state expectations")
        return self


class ServiceParameters(StrictModel):
    name: str = Field(pattern=UNIT_PATTERN.pattern)
    active: bool = True
    enabled: bool | None = None
    timeout_seconds: float = Field(default=10, gt=0, le=60)


class CommandCheckProvider:
    kind = CheckKind.COMMAND

    def __init__(self, executor: GuestExecutor) -> None:
        self._executor = executor

    def evaluate(self, check: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation:
        parameters = CommandParameters.model_validate(check.parameters)
        arguments = parameters.arguments
        if parameters.sudo:
            arguments = ("sudo", "--", *arguments)
        result = self._executor.run(endpoint, arguments, timeout_seconds=parameters.timeout_seconds)
        if result.stdout_truncated or result.stderr_truncated:
            return CheckObservation(check.check_id, False, "command output exceeded limit", True)
        passed = result.exit_code == parameters.expected_exit_code
        reason = f"exit code {result.exit_code}, expected {parameters.expected_exit_code}"
        if passed and parameters.stdout_equals is not None:
            passed = result.stdout.rstrip("\n") == parameters.stdout_equals
            reason = "stdout matched" if passed else "stdout did not match exactly"
        elif passed and parameters.stdout_contains is not None:
            passed = parameters.stdout_contains in result.stdout
            reason = "stdout contained expected text" if passed else "stdout lacked expected text"
        elif passed:
            reason = "command reached the expected exit code"
        return CheckObservation(check.check_id, passed, reason)


class FileCheckProvider:
    kind = CheckKind.FILE
    _kind_names: ClassVar[dict[str, str]] = {
        "file": "regular file",
        "directory": "directory",
        "symlink": "symbolic link",
    }

    def __init__(self, executor: GuestExecutor) -> None:
        self._executor = executor

    def evaluate(self, check: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation:
        parameters = FileParameters.model_validate(check.parameters)
        prefix = ("sudo", "--") if parameters.sudo else ()
        stat = self._executor.run(
            endpoint,
            (*prefix, "stat", "--format=%F|%U|%G|%a", "--", parameters.path),
            timeout_seconds=parameters.timeout_seconds,
        )
        if not parameters.exists:
            return CheckObservation(
                check.check_id,
                not stat.succeeded,
                "path is absent" if not stat.succeeded else "path unexpectedly exists",
            )
        if not stat.succeeded:
            return CheckObservation(check.check_id, False, "required path does not exist")
        if stat.stdout_truncated:
            return CheckObservation(check.check_id, False, "stat output exceeded limit", True)
        fields = stat.stdout.strip().split("|")
        if len(fields) != 4:
            return CheckObservation(check.check_id, False, "unexpected stat output", True)
        actual_kind, actual_owner, actual_group, actual_mode = fields
        mismatches: list[str] = []
        if parameters.kind and actual_kind != self._kind_names[parameters.kind]:
            mismatches.append(f"type is {actual_kind}")
        if parameters.owner and actual_owner != parameters.owner:
            mismatches.append(f"owner is {actual_owner}")
        if parameters.group and actual_group != parameters.group:
            mismatches.append(f"group is {actual_group}")
        if parameters.mode and actual_mode != parameters.mode.lstrip("0"):
            mismatches.append(f"mode is {actual_mode}")
        if parameters.contains is not None:
            content = self._executor.run(
                endpoint,
                (
                    *prefix,
                    "grep",
                    "--fixed-strings",
                    "--quiet",
                    "--",
                    parameters.contains,
                    parameters.path,
                ),
                timeout_seconds=parameters.timeout_seconds,
            )
            if content.exit_code == 1:
                mismatches.append("required content is absent")
            elif content.exit_code != 0:
                return CheckObservation(check.check_id, False, "content inspection failed", True)
        return CheckObservation(
            check.check_id,
            not mismatches,
            "path state matched" if not mismatches else "; ".join(mismatches),
        )


class ServiceCheckProvider:
    kind = CheckKind.SERVICE

    def __init__(self, executor: GuestExecutor) -> None:
        self._executor = executor

    def evaluate(self, check: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation:
        parameters = ServiceParameters.model_validate(check.parameters)
        loaded = self._executor.run(
            endpoint,
            ("systemctl", "show", "--property=LoadState", "--value", "--", parameters.name),
            timeout_seconds=parameters.timeout_seconds,
        )
        if not loaded.succeeded or loaded.stdout.strip() != "loaded":
            return CheckObservation(check.check_id, False, "service unit is not loaded")
        active = self._executor.run(
            endpoint,
            ("systemctl", "is-active", "--quiet", "--", parameters.name),
            timeout_seconds=parameters.timeout_seconds,
        )
        actual_active = active.succeeded
        mismatches: list[str] = []
        if actual_active != parameters.active:
            mismatches.append("service active state differs")
        if parameters.enabled is not None:
            enabled = self._executor.run(
                endpoint,
                ("systemctl", "is-enabled", "--quiet", "--", parameters.name),
                timeout_seconds=parameters.timeout_seconds,
            )
            if enabled.succeeded != parameters.enabled:
                mismatches.append("service enabled state differs")
        return CheckObservation(
            check.check_id,
            not mismatches,
            "service state matched" if not mismatches else "; ".join(mismatches),
        )


def validate_guest_check_parameters(check: CheckSpec) -> None:
    models: dict[CheckKind, type[StrictModel]] = {
        CheckKind.COMMAND: CommandParameters,
        CheckKind.FILE: FileParameters,
        CheckKind.SERVICE: ServiceParameters,
    }
    model = models.get(check.kind)
    if model is not None:
        model.model_validate(check.parameters)
