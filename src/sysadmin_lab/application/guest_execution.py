from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class GuestEndpoint:
    host: str
    username: str
    private_key: Path
    port: int = 22

    def __post_init__(self) -> None:
        if not self.host or any(character.isspace() for character in self.host):
            raise ValueError("guest host must be non-empty and contain no whitespace")
        if not USERNAME_PATTERN.fullmatch(self.username):
            raise ValueError(f"invalid guest username: {self.username}")
        if not self.private_key.is_absolute():
            raise ValueError("guest private key path must be absolute")
        if not 1 <= self.port <= 65535:
            raise ValueError("guest SSH port is outside the valid range")


@dataclass(frozen=True, slots=True)
class GuestCommandResult:
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


class GuestCommandTimeout(TimeoutError):
    pass


class GuestExecutor(Protocol):
    def run(
        self,
        endpoint: GuestEndpoint,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GuestCommandResult: ...


class GuestReadinessError(TimeoutError):
    pass


class GuestReadiness:
    def __init__(
        self, executor: GuestExecutor, *, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._executor = executor
        self._sleep = sleep

    def wait(
        self,
        endpoint: GuestEndpoint,
        *,
        attempts: int = 60,
        interval_seconds: float = 2.0,
        command_timeout_seconds: float = 3.0,
    ) -> None:
        if attempts < 1:
            raise ValueError("readiness attempts must be positive")
        last_detail = "no connection attempt was made"
        for attempt in range(attempts):
            try:
                result = self._executor.run(
                    endpoint, ("true",), timeout_seconds=command_timeout_seconds
                )
                if result.succeeded:
                    return
                last_detail = result.stderr.strip() or f"SSH exited {result.exit_code}"
            except GuestCommandTimeout as exc:
                last_detail = str(exc)
            if attempt + 1 < attempts:
                self._sleep(interval_seconds)
        raise GuestReadinessError(
            f"guest SSH did not become ready after {attempts} attempts: {last_detail}"
        )
