from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sysadmin_lab.adapters.ssh_guest import (
    BoundedSubprocessRunner,
    ProcessResult,
    SshGuestExecutor,
)
from sysadmin_lab.application.guest_execution import GuestCommandTimeout, GuestEndpoint


@dataclass
class FakeRunner:
    result: ProcessResult = field(
        default_factory=lambda: ProcessResult(0, "ok\n", "", False, False)
    )
    calls: list[tuple[list[str], float, int]] = field(default_factory=list)

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> ProcessResult:
        self.calls.append((list(arguments), timeout_seconds, max_output_bytes))
        return self.result


def test_ssh_executor_quotes_remote_arguments_and_bounds_output(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("private", encoding="utf-8")
    runner = FakeRunner()
    executor = SshGuestExecutor(runner, ssh_binary=Path("/custom/ssh"), max_output_bytes=99)
    endpoint = GuestEndpoint("192.0.2.10", "labadmin", key)

    result = executor.run(
        endpoint,
        ("printf", "%s", "value with spaces; $(unsafe)"),
        timeout_seconds=7,
    )

    command, timeout, limit = runner.calls[0]
    assert command[0] == "/custom/ssh"
    assert command[-2] == "labadmin@192.0.2.10"
    assert command[-1] == "printf %s 'value with spaces; $(unsafe)'"
    assert (timeout, limit) == (7, 99)
    assert result.succeeded
    assert key.stat().st_mode & 0o777 == 0o600


def test_ssh_executor_rejects_missing_key_or_empty_command(tmp_path: Path) -> None:
    endpoint = GuestEndpoint("192.0.2.10", "labadmin", tmp_path / "missing")
    executor = SshGuestExecutor(FakeRunner())
    with pytest.raises(ValueError, match="must not be empty"):
        executor.run(endpoint, (), timeout_seconds=1)
    with pytest.raises(FileNotFoundError, match="does not exist"):
        executor.run(endpoint, ("true",), timeout_seconds=1)


def test_bounded_runner_truncates_each_stream() -> None:
    result = BoundedSubprocessRunner().run(
        [
            sys.executable,
            "-c",
            "import sys; print('abcdefgh'); print('12345678', file=sys.stderr)",
        ],
        timeout_seconds=2,
        max_output_bytes=5,
    )
    assert result.stdout == "abcde"
    assert result.stderr == "12345"
    assert result.stdout_truncated and result.stderr_truncated


def test_bounded_runner_enforces_timeout_and_valid_limits() -> None:
    runner = BoundedSubprocessRunner()
    with pytest.raises(GuestCommandTimeout, match="exceeded"):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            timeout_seconds=0.01,
            max_output_bytes=10,
        )
    with pytest.raises(ValueError, match="timeout"):
        runner.run([sys.executable], timeout_seconds=0, max_output_bytes=10)
    with pytest.raises(ValueError, match="output limit"):
        runner.run([sys.executable], timeout_seconds=1, max_output_bytes=0)
