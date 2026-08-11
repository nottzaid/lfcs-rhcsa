from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.application.guest_execution import (
    GuestCommandResult,
    GuestEndpoint,
)
from sysadmin_lab.application.session_artifacts import GuestAccess, SessionPaths
from sysadmin_lab.application.sessions import SessionConflictError, SessionCoordinator
from sysadmin_lab.application.vm_sessions import SingleHostVmSessionService, VmProvisioningError
from sysadmin_lab.domain.resources import ResourceIdentity
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus


@dataclass
class MemorySessions:
    states: dict[UUID, SessionState] = field(default_factory=dict)

    def create(self, state: SessionState) -> None:
        if state.session_id in self.states:
            raise SessionConflictError("duplicate")
        self.states[state.session_id] = state

    def get(self, session_id: UUID) -> SessionState | None:
        return self.states.get(session_id)

    def save(self, previous_revision: int, state: SessionState) -> None:
        if self.states[state.session_id].revision != previous_revision:
            raise SessionConflictError("stale")
        self.states[state.session_id] = state


@dataclass
class MemoryMachines:
    values: dict[tuple[UUID, str], SessionMachine] = field(default_factory=dict)

    def add(self, machine: SessionMachine) -> None:
        self.values[(machine.session_id, machine.host_name)] = machine

    def get(self, session_id: UUID, host_name: str) -> SessionMachine | None:
        return self.values.get((session_id, host_name))

    def list(self, session_id: UUID) -> tuple[SessionMachine, ...]:
        return tuple(
            machine
            for (stored_session, _host), machine in self.values.items()
            if stored_session == session_id
        )

    def update_address(self, machine: SessionMachine, address: str) -> SessionMachine:
        updated = SessionMachine(
            machine.session_id,
            machine.host_name,
            machine.identity,
            machine.username,
            machine.password,
            machine.private_key,
            address,
        )
        self.values[(machine.session_id, machine.host_name)] = updated
        return updated

    def remove(self, machine: SessionMachine) -> None:
        del self.values[(machine.session_id, machine.host_name)]


@dataclass
class FakeArtifacts:
    root: Path
    fail: bool = False
    destroyed: list[tuple[UUID, str]] = field(default_factory=list)

    def create(
        self,
        *,
        session_id: UUID,
        role: str,
        hostname: str,
        base_image: Path,
        disk_gib: int = 12,
    ) -> tuple[SessionPaths, GuestAccess]:
        if self.fail:
            raise RuntimeError("artifact failure")
        directory = self.root / str(session_id) / role
        directory.mkdir(parents=True)
        key = (self.root / "key").resolve()
        key.touch()
        paths = SessionPaths(
            directory,
            directory / "root.qcow2",
            directory / "seed.iso",
            directory / "seed-source",
            key,
            key.with_suffix(".pub"),
        )
        return paths, GuestAccess("labadmin", "secret", key, key.with_suffix(".pub"))

    def destroy(self, session_id: UUID, role: str) -> None:
        self.destroyed.append((session_id, role))


@dataclass
class FakeResources:
    registered: dict[str, ResourceIdentity] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    xml: str = ""

    def define(self, xml: str, identity: ResourceIdentity, *, start: bool = False) -> object:
        assert start
        self.xml = xml
        self.registered[identity.name] = identity
        return object()

    def is_registered(self, identity: ResourceIdentity) -> bool:
        return self.registered.get(identity.name) == identity

    def remove(self, identity: ResourceIdentity) -> None:
        del self.registered[identity.name]
        self.removed.append(identity.name)


@dataclass
class FakeLeases:
    address: str = "192.0.2.10"

    def wait(self, domain_name: str) -> str:
        return self.address


@dataclass
class FakeReadiness:
    endpoints: list[GuestEndpoint] = field(default_factory=list)

    def wait(self, endpoint: GuestEndpoint) -> None:
        self.endpoints.append(endpoint)


@dataclass
class FakeExecutor:
    result: GuestCommandResult
    calls: list[tuple[GuestEndpoint, tuple[str, ...], float]] = field(default_factory=list)

    def run(
        self,
        endpoint: GuestEndpoint,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GuestCommandResult:
        self.calls.append((endpoint, arguments, timeout_seconds))
        return self.result


def dependencies(tmp_path: Path, *, cloud_exit: int = 0, artifact_failure: bool = False) -> tuple:
    session_repository = MemorySessions()
    machines = MemoryMachines()
    artifacts = FakeArtifacts(tmp_path / "artifacts", fail=artifact_failure)
    resources = FakeResources()
    readiness = FakeReadiness()
    executor = FakeExecutor(GuestCommandResult(cloud_exit, "", "cloud error"))
    service = SingleHostVmSessionService(
        sessions=SessionCoordinator(session_repository),
        machines=machines,  # type: ignore[arg-type]
        resources=resources,  # type: ignore[arg-type]
        artifacts=artifacts,  # type: ignore[arg-type]
        leases=FakeLeases(),  # type: ignore[arg-type]
        guest_readiness=readiness,  # type: ignore[arg-type]
        guest_executor=executor,
    )
    return service, session_repository, machines, artifacts, resources, readiness, executor


def test_service_provisions_persists_and_exactly_destroys_machine(tmp_path: Path) -> None:
    service, sessions, machines, artifacts, resources, readiness, executor = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    base.touch()

    provisioned = service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)

    assert provisioned.state.status is SessionStatus.READY
    assert provisioned.machine.address == "192.0.2.10"
    assert machines.get(provisioned.state.session_id, "node1") == provisioned.machine
    assert provisioned.machine.identity.name in resources.xml
    assert readiness.endpoints[0].host == "192.0.2.10"
    assert executor.calls[0][1] == ("sudo", "cloud-init", "status", "--wait")

    destroyed = service.destroy(provisioned.state.session_id)
    assert destroyed.status is SessionStatus.DESTROYED
    assert machines.list(provisioned.state.session_id) == ()
    assert resources.removed == [provisioned.machine.identity.name]
    assert artifacts.destroyed == [(provisioned.state.session_id, "node1")]
    assert sessions.states[provisioned.state.session_id] == destroyed


def test_service_cleans_resources_and_records_failed_cloud_init(tmp_path: Path) -> None:
    service, sessions, machines, artifacts, resources, _readiness, _executor = dependencies(
        tmp_path, cloud_exit=2
    )
    base = tmp_path / "base.qcow2"
    base.touch()

    with pytest.raises(VmProvisioningError, match="cloud-init failed"):
        service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)

    state = next(iter(sessions.states.values()))
    assert state.status is SessionStatus.FAILED
    assert state.error == "cloud-init failed: cloud error"
    assert machines.list(state.session_id) == ()
    assert resources.registered == {}
    assert artifacts.destroyed == [(state.session_id, "node1")]


def test_service_records_early_artifact_failure(tmp_path: Path) -> None:
    service, sessions, machines, artifacts, resources, _readiness, _executor = dependencies(
        tmp_path, artifact_failure=True
    )
    base = tmp_path / "base.qcow2"
    base.touch()

    with pytest.raises(RuntimeError, match="artifact failure"):
        service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)

    state = next(iter(sessions.states.values()))
    assert state.status is SessionStatus.FAILED
    assert state.error == "artifact failure"
    assert machines.list(state.session_id) == ()
    assert resources.registered == {}
    assert artifacts.destroyed == [(state.session_id, "node1")]
