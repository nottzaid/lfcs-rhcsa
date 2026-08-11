from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from sysadmin_lab.application.guest_execution import (
    GuestCommandResult,
    GuestCommandTimeout,
    GuestEndpoint,
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


class ProcessRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> ProcessResult: ...


class BoundedSubprocessRunner:
    """Runs without a local shell and returns bounded stdout and stderr."""

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> ProcessResult:
        if timeout_seconds <= 0:
            raise ValueError("process timeout must be positive")
        if max_output_bytes < 1:
            raise ValueError("process output limit must be positive")
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                close_fds=True,
            )
            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise GuestCommandTimeout(
                    f"process exceeded {timeout_seconds:g} second timeout"
                ) from exc
            stdout_text, stdout_truncated = self._read_bounded(stdout, max_output_bytes)
            stderr_text, stderr_truncated = self._read_bounded(stderr, max_output_bytes)
        return ProcessResult(
            exit_code,
            stdout_text,
            stderr_text,
            stdout_truncated,
            stderr_truncated,
        )

    @staticmethod
    def _read_bounded(stream: BinaryIO, limit: int) -> tuple[str, bool]:
        size = stream.tell()
        stream.seek(0)
        payload = stream.read(limit)
        return payload.decode("utf-8", errors="replace"), size > limit


class SshGuestExecutor:
    def __init__(
        self,
        runner: ProcessRunner,
        *,
        ssh_binary: Path = Path("/usr/bin/ssh"),
        max_output_bytes: int = 64 * 1024,
    ) -> None:
        self._runner = runner
        self._ssh_binary = ssh_binary
        self._max_output_bytes = max_output_bytes

    def run(
        self,
        endpoint: GuestEndpoint,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GuestCommandResult:
        if not arguments:
            raise ValueError("guest command must not be empty")
        if not endpoint.private_key.is_file():
            raise FileNotFoundError(f"guest private key does not exist: {endpoint.private_key}")
        os.chmod(endpoint.private_key, 0o600)
        command = [
            str(self._ssh_binary),
            "-T",
            "-i",
            str(endpoint.private_key),
            "-p",
            str(endpoint.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            f"{endpoint.username}@{endpoint.host}",
            shlex.join(arguments),
        ]
        result = self._runner.run(
            command,
            timeout_seconds=timeout_seconds,
            max_output_bytes=self._max_output_bytes,
        )
        return GuestCommandResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
        )
