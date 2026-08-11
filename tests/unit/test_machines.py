from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.adapters.sqlite_machines import SqliteSessionMachineRepository
from sysadmin_lab.application.machines import (
    DomainLeaseReadiness,
    DomainLeaseTimeout,
    SessionMachineConflictError,
)
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.virtual_machines import domain_identity

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


def machine(tmp_path: Path) -> SessionMachine:
    return SessionMachine(
        session_id=SESSION_ID,
        host_name="node1",
        identity=domain_identity("base-smoke", SESSION_ID, "node1"),
        username="labadmin",
        password="secret",
        private_key=(tmp_path / "key").resolve(),
    )


def test_machine_repository_round_trip_update_and_exact_remove(tmp_path: Path) -> None:
    expected = machine(tmp_path)
    database = tmp_path / "state.db"
    with SqliteSessionMachineRepository(database) as repository:
        repository.add(expected)
        assert repository.get(SESSION_ID, "node1") == expected
        assert repository.list(SESSION_ID) == (expected,)
        updated = repository.update_address(expected, "192.0.2.10")
        assert repository.get(SESSION_ID, "node1") == updated
        repository.remove(updated)
        assert repository.list(SESSION_ID) == ()
    assert database.stat().st_mode & 0o777 == 0o600


def test_machine_repository_rejects_duplicates_and_missing_mutations(tmp_path: Path) -> None:
    expected = machine(tmp_path)
    with SqliteSessionMachineRepository(tmp_path / "state.db") as repository:
        repository.add(expected)
        with pytest.raises(SessionMachineConflictError, match="already exists"):
            repository.add(expected)
        repository.remove(expected)
        with pytest.raises(SessionMachineConflictError, match="changed or is missing"):
            repository.update_address(expected, "192.0.2.10")
        with pytest.raises(SessionMachineConflictError, match="cannot remove"):
            repository.remove(expected)


def test_lease_readiness_returns_first_address_after_retry() -> None:
    class Source:
        calls = 0

        def domain_ipv4_addresses(self, name: str) -> tuple[str, ...]:
            self.calls += 1
            return () if self.calls == 1 else ("192.0.2.10", "192.0.2.11")

    source = Source()
    sleeps: list[float] = []
    address = DomainLeaseReadiness(source, sleep=sleeps.append).wait("lal-domain", attempts=2)
    assert address == "192.0.2.10"
    assert sleeps == [2.0]


def test_lease_readiness_times_out_and_validates_attempts() -> None:
    class Source:
        def domain_ipv4_addresses(self, name: str) -> tuple[str, ...]:
            return ()

    readiness = DomainLeaseReadiness(Source(), sleep=lambda _seconds: None)
    with pytest.raises(DomainLeaseTimeout, match="no IPv4 DHCP lease"):
        readiness.wait("lal-domain", attempts=1)
    with pytest.raises(ValueError, match="positive"):
        readiness.wait("lal-domain", attempts=0)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"host_name": "Bad"}, "invalid host_name"),
        ({"session_id": UUID(int=2)}, "session identifiers differ"),
        ({"username": ""}, "credentials"),
        ({"private_key": Path("relative")}, "must be absolute"),
        ({"address": "bad address"}, "address is invalid"),
    ],
)
def test_session_machine_validates_invariants(
    tmp_path: Path, changes: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        replace(machine(tmp_path), **changes)
