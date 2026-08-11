from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from sysadmin_lab.application.guest_execution import (
    GuestCommandResult,
    GuestCommandTimeout,
    GuestEndpoint,
    GuestReadiness,
    GuestReadinessError,
)


@dataclass
class FakeExecutor:
    results: list[GuestCommandResult | Exception]
    calls: int = 0

    def run(
        self,
        endpoint: GuestEndpoint,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GuestCommandResult:
        self.calls += 1
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def endpoint(tmp_path: Path) -> GuestEndpoint:
    return GuestEndpoint("192.0.2.10", "labadmin", tmp_path / "key")


def test_readiness_retries_failures_and_timeouts(tmp_path: Path) -> None:
    executor = FakeExecutor(
        [
            GuestCommandResult(255, "", "refused"),
            GuestCommandTimeout("timed out"),
            GuestCommandResult(0, "", ""),
        ]
    )
    sleeps: list[float] = []
    GuestReadiness(executor, sleep=sleeps.append).wait(endpoint(tmp_path), attempts=3)
    assert executor.calls == 3
    assert sleeps == [2.0, 2.0]


def test_readiness_reports_last_failure_without_sleeping_after_final_try(tmp_path: Path) -> None:
    executor = FakeExecutor([GuestCommandResult(255, "", "permission denied")])
    sleeps: list[float] = []
    with pytest.raises(GuestReadinessError, match="permission denied"):
        GuestReadiness(executor, sleep=sleeps.append).wait(endpoint(tmp_path), attempts=1)
    assert sleeps == []


def test_restart_waits_for_offline_then_online(tmp_path: Path) -> None:
    executor = FakeExecutor(
        [
            GuestCommandResult(0, "", ""),
            GuestCommandResult(255, "", "connection refused"),
            GuestCommandResult(255, "", "connection refused"),
            GuestCommandResult(0, "", ""),
        ]
    )
    sleeps: list[float] = []

    GuestReadiness(executor, sleep=sleeps.append).wait_for_restart(
        endpoint(tmp_path), offline_attempts=2, online_attempts=2
    )

    assert executor.calls == 4
    assert sleeps == [2.0, 2.0]


def test_restart_rejects_a_guest_that_never_goes_offline(tmp_path: Path) -> None:
    executor = FakeExecutor(
        [GuestCommandResult(0, "", ""), GuestCommandResult(0, "", "")]
    )

    with pytest.raises(GuestReadinessError, match="did not go offline"):
        GuestReadiness(executor, sleep=lambda _seconds: None).wait_for_restart(
            endpoint(tmp_path), offline_attempts=2
        )


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"host": "bad host"}, "no whitespace"),
        ({"username": "Bad"}, "invalid guest username"),
        ({"private_key": Path("relative")}, "must be absolute"),
        ({"port": 0}, "valid range"),
    ],
)
def test_guest_endpoint_validates_inputs(
    tmp_path: Path, changes: dict[str, object], match: str
) -> None:
    values: dict[str, object] = {
        "host": "192.0.2.10",
        "username": "labadmin",
        "private_key": tmp_path / "key",
        "port": 22,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=match):
        GuestEndpoint(**values)  # type: ignore[arg-type]
