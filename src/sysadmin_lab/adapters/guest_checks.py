from __future__ import annotations

import re
from functools import cache
from importlib.resources import files
from typing import ClassVar, Literal, Self

from pydantic import Field, model_validator

from sysadmin_lab.application.guest_execution import (
    GuestCommandResult,
    GuestEndpoint,
    GuestExecutor,
)
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.domain.models import CheckKind, CheckSpec, StrictModel

UNIT_PATTERN = re.compile(r"^[A-Za-z0-9@_.:-]+$")
MAX_REASON_LENGTH = 240


@cache
def check_library() -> str:
    """Return the host-owned shell helpers prepended to every script check."""
    return files("sysadmin_lab.adapters").joinpath("check_library.sh").read_text(encoding="utf-8")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= MAX_REASON_LENGTH:
        return text
    return text[: MAX_REASON_LENGTH - 1].rstrip() + "…"


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return _clip(lines[-1]) if lines else ""


class CommandParameters(StrictModel):
    arguments: tuple[str, ...] | None = Field(default=None, min_length=1)
    script: str | None = Field(default=None, min_length=1)
    expected_exit_code: int = 0
    stdout_equals: str | None = None
    stdout_contains: str | None = None
    sudo: bool = False
    timeout_seconds: float = Field(default=10, gt=0, le=120)

    @model_validator(mode="after")
    def one_program_and_one_stdout_expectation(self) -> Self:
        if (self.arguments is None) == (self.script is None):
            raise ValueError("command checks require exactly one of arguments or script")
        if self.stdout_equals is not None and self.stdout_contains is not None:
            raise ValueError("command checks accept only one stdout expectation")
        return self

    def command(self) -> tuple[str, ...]:
        if self.script is not None:
            program: tuple[str, ...] = ("bash", "-c", f"{check_library()}\n{self.script}")
        else:
            assert self.arguments is not None
            program = self.arguments
        return ("sudo", "--", *program) if self.sudo else program


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
        result = self._executor.run(
            endpoint, parameters.command(), timeout_seconds=parameters.timeout_seconds
        )
        if result.stdout_truncated or result.stderr_truncated:
            return CheckObservation(check.check_id, False, "command output exceeded limit", True)
        if result.exit_code != parameters.expected_exit_code:
            return CheckObservation(check.check_id, False, self._exit_reason(result, parameters))
        if parameters.stdout_equals is not None:
            observed = result.stdout.rstrip("\n")
            if observed != parameters.stdout_equals:
                shown = _clip(observed.splitlines()[0]) if observed.strip() else "no output"
                return CheckObservation(check.check_id, False, f"found {shown!r}")
        elif parameters.stdout_contains is not None:
            if parameters.stdout_contains not in result.stdout:
                return CheckObservation(
                    check.check_id, False, "the output did not contain the expected text"
                )
        return CheckObservation(check.check_id, True, "requirement met")

    @staticmethod
    def _exit_reason(result: GuestCommandResult, parameters: CommandParameters) -> str:
        reason = _last_line(result.stderr)
        if not reason and parameters.script is not None:
            reason = _last_line(result.stdout)
        return reason or (
            f"exit status {result.exit_code}; expected {parameters.expected_exit_code}"
        )


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
                "path is absent" if not stat.succeeded else f"{parameters.path} still exists",
            )
        if not stat.succeeded:
            return CheckObservation(check.check_id, False, f"{parameters.path} does not exist")
        if stat.stdout_truncated:
            return CheckObservation(check.check_id, False, "stat output exceeded limit", True)
        fields = stat.stdout.strip().split("|")
        if len(fields) != 4:
            return CheckObservation(check.check_id, False, "unexpected stat output", True)
        actual_kind, actual_owner, actual_group, actual_mode = fields
        mismatches: list[str] = []
        if parameters.kind and actual_kind != self._kind_names[parameters.kind]:
            mismatches.append(
                f"{parameters.path} is a {actual_kind}, not a {self._kind_names[parameters.kind]}"
            )
        if parameters.owner and actual_owner != parameters.owner:
            mismatches.append(f"owner is {actual_owner}; expected {parameters.owner}")
        if parameters.group and actual_group != parameters.group:
            mismatches.append(f"group is {actual_group}; expected {parameters.group}")
        if parameters.mode and actual_mode.lstrip("0") != parameters.mode.lstrip("0"):
            mismatches.append(
                f"mode is {actual_mode.zfill(4)}; expected {parameters.mode.zfill(4)}"
            )
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
                mismatches.append(f"{parameters.path} lacks the required content")
            elif content.exit_code != 0:
                return CheckObservation(check.check_id, False, "content inspection failed", True)
        return CheckObservation(
            check.check_id,
            not mismatches,
            "requirement met" if not mismatches else "; ".join(mismatches),
        )


class ServiceCheckProvider:
    kind = CheckKind.SERVICE

    def __init__(self, executor: GuestExecutor) -> None:
        self._executor = executor

    def evaluate(self, check: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation:
        parameters = ServiceParameters.model_validate(check.parameters)
        name = parameters.name
        shown = self._executor.run(
            endpoint,
            ("systemctl", "show", "--property=LoadState,ActiveState", "--", name),
            timeout_seconds=parameters.timeout_seconds,
        )
        state = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
        if not shown.succeeded or state.get("LoadState") != "loaded":
            return CheckObservation(check.check_id, False, f"{name} is not installed or loadable")
        mismatches: list[str] = []
        active_state = state.get("ActiveState", "unknown")
        if (active_state == "active") != parameters.active:
            expected = "active" if parameters.active else "stopped"
            mismatches.append(f"{name} is {active_state}; expected {expected}")
        if parameters.enabled is not None:
            enabled = self._executor.run(
                endpoint,
                ("systemctl", "is-enabled", "--", name),
                timeout_seconds=parameters.timeout_seconds,
            )
            if enabled.succeeded != parameters.enabled:
                actual = enabled.stdout.strip() or "not enabled"
                expected = "enabled at boot" if parameters.enabled else "disabled"
                mismatches.append(f"{name} is {actual}; expected {expected}")
        return CheckObservation(
            check.check_id,
            not mismatches,
            "requirement met" if not mismatches else "; ".join(mismatches),
        )


def validate_guest_check_parameters(check: CheckSpec) -> None:
    models: dict[CheckKind, type[StrictModel]] = {
        CheckKind.COMMAND: CommandParameters,
        CheckKind.FILE: FileParameters,
        CheckKind.SERVICE: ServiceParameters,
    }
    models[check.kind].model_validate(check.parameters)
