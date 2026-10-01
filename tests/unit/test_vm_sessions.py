from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.application.guest_execution import (
    GuestCommandResult,
    GuestCommandTimeout,
    GuestEndpoint,
    GuestReadinessError,
)
from sysadmin_lab.application.session_artifacts import GuestAccess, SessionPaths
from sysadmin_lab.application.sessions import SessionConflictError, SessionCoordinator
from sysadmin_lab.application.vm_sessions import (
    RELEASE_MANAGEMENT_LEASE,
    MachineUnreachableError,
    VmHostRequest,
    VmProvisioningError,
    VmSessionService,
)
from sysadmin_lab.domain.models import DiskSpec, NicSpec
from sysadmin_lab.domain.resources import ResourceDriftError, ResourceIdentity, ResourceKind
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import ScenarioInterface


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
    hostnames: list[str] = field(default_factory=list)
    interfaces: dict[str, tuple[ScenarioInterface, ...]] = field(default_factory=dict)

    def create(
        self,
        *,
        session_id: UUID,
        role: str,
        hostname: str,
        base_image: Path,
        disk_gib: int = 12,
        data_disks: tuple[DiskSpec, ...] = (),
        interfaces: tuple[ScenarioInterface, ...] = (),
    ) -> tuple[SessionPaths, GuestAccess]:
        self.hostnames.append(hostname)
        self.interfaces[role] = interfaces
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
    defined: list[str] = field(default_factory=list)
    fail_on: str | None = None
    connected: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    vanished: set[str] = field(default_factory=set)

    def define(self, xml: str, identity: ResourceIdentity, *, start: bool = False) -> object:
        assert start
        if self.fail_on and self.fail_on in identity.name:
            raise RuntimeError(f"libvirt refused {identity.name}")
        self.xml = xml
        self.defined.append(xml)
        self.registered[identity.name] = identity
        return object()

    def owned(self, session_id: UUID, kind: ResourceKind) -> tuple[ResourceIdentity, ...]:
        return tuple(
            identity
            for identity in self.registered.values()
            if identity.session_id == session_id and identity.kind is kind
        )

    def is_registered(self, identity: ResourceIdentity) -> bool:
        return self.registered.get(identity.name) == identity

    def remove(self, identity: ResourceIdentity, *, missing_ok: bool = False) -> None:
        if identity.name in self.vanished:  # deleted in libvirt behind the lab's back
            if not missing_ok:
                raise ResourceDriftError(f"registered resource is absent: {identity.name}")
        else:
            self.removed.append(identity.name)
        del self.registered[identity.name]

    def connect_interfaces(self, identity: ResourceIdentity, macs: tuple[str, ...]) -> None:
        self.connected.append((identity.role, macs))


@dataclass
class FakeLeases:
    address: str = "192.0.2.10"
    released: list[str] = field(default_factory=list)

    def wait(self, domain_name: str) -> str:
        return self.address

    def wait_released(self, domain_name: str) -> bool:
        self.released.append(domain_name)
        return True

    def holds(self, domain_name: str, address: str) -> bool:
        return address == self.address


@dataclass
class FakeReadiness:
    endpoints: list[GuestEndpoint] = field(default_factory=list)
    restarted: list[GuestEndpoint] = field(default_factory=list)
    restart_failure: Exception | None = None

    def wait(self, endpoint: GuestEndpoint) -> None:
        self.endpoints.append(endpoint)

    def wait_for_restart(self, endpoint: GuestEndpoint) -> None:
        self.restarted.append(endpoint)
        if self.restart_failure is not None:
            raise self.restart_failure


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


def dependencies(
    tmp_path: Path,
    *,
    cloud_exit: int = 0,
    artifact_failure: bool = False,
    leases: FakeLeases | None = None,
) -> tuple:
    session_repository = MemorySessions()
    machines = MemoryMachines()
    artifacts = FakeArtifacts(tmp_path / "artifacts", fail=artifact_failure)
    resources = FakeResources()
    readiness = FakeReadiness()
    executor = FakeExecutor(GuestCommandResult(cloud_exit, "", "cloud error"))
    leases = leases or FakeLeases()
    service = VmSessionService(
        sessions=SessionCoordinator(session_repository),
        machines=machines,  # type: ignore[arg-type]
        resources=resources,  # type: ignore[arg-type]
        artifacts=artifacts,  # type: ignore[arg-type]
        leases=leases,  # type: ignore[arg-type]
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


def test_service_reboots_only_an_owned_ready_machine(tmp_path: Path) -> None:
    service, sessions, _machines, _artifacts, _resources, readiness, executor = dependencies(
        tmp_path
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(
        scenario_id="persistent-kernel-tuning", host_name="node2", base_image=base
    )

    service.reboot(provisioned.state.session_id, ("node2",))

    assert executor.calls[-1][1] == ("sudo", "--", "systemctl", "reboot")
    assert readiness.restarted == [
        GuestEndpoint("192.0.2.10", "labadmin", (tmp_path / "artifacts" / "key").resolve())
    ]
    assert sessions.states[provisioned.state.session_id].status is SessionStatus.READY
    assert sessions.states[provisioned.state.session_id].revision == 4


def test_unbootable_machine_after_reboot_leaves_the_session_usable(tmp_path: Path) -> None:
    service, sessions, _machines, _artifacts, _resources, readiness, _executor = dependencies(
        tmp_path
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="fstab-lab", host_name="node2", base_image=base)
    readiness.restart_failure = GuestReadinessError("guest SSH did not become ready")

    with pytest.raises(MachineUnreachableError, match="node2 did not come back") as raised:
        service.reboot(provisioned.state.session_id, ("node2",))

    assert raised.value.host_name == "node2"
    assert sessions.states[provisioned.state.session_id].status is SessionStatus.READY


def test_service_provisions_multiple_hosts_in_one_session(tmp_path: Path) -> None:
    service, _sessions, machines, artifacts, resources, readiness, executor = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    base.touch()

    provisioned = service.provision_many(
        scenario_id="peer-lab",
        requests=(
            VmHostRequest("node1", base, 1024, 1),
            VmHostRequest("node2", base, 1024, 1),
        ),
    )

    assert [machine.host_name for machine in provisioned.machines] == ["node1", "node2"]
    assert provisioned.machine == provisioned.machines[0]
    assert len(machines.list(provisioned.state.session_id)) == 2
    assert len(resources.registered) == 2
    assert len(readiness.endpoints) == 2
    assert len(executor.calls) == 2

    service.destroy(provisioned.state.session_id)
    assert artifacts.destroyed == [
        (provisioned.state.session_id, "node1"),
        (provisioned.state.session_id, "node2"),
    ]


def test_service_reboot_rejects_unknown_or_empty_targets(tmp_path: Path) -> None:
    service, _sessions, _machines, _artifacts, _resources, _readiness, _executor = dependencies(
        tmp_path
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="base-smoke", host_name="node2", base_image=base)

    with pytest.raises(VmProvisioningError, match="at least one"):
        service.reboot(provisioned.state.session_id, ())
    with pytest.raises(VmProvisioningError, match="unknown reboot hosts"):
        service.reboot(provisioned.state.session_id, ("node3",))


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


def test_service_builds_isolated_networks_and_named_interfaces(tmp_path: Path) -> None:
    service, _sessions, _machines, artifacts, resources, _readiness, _executor = dependencies(
        tmp_path
    )
    base = tmp_path / "base.qcow2"
    base.touch()

    provisioned = service.provision_many(
        scenario_id="branch-routing",
        requests=(
            VmHostRequest("router", base, interfaces=(NicSpec(network="lan", name="lan0"),)),
            VmHostRequest("client", base, interfaces=(NicSpec(network="lan", name="lan0"),)),
        ),
        networks=("lan",),
    )

    session_id = provisioned.state.session_id
    assert resources.defined[0].startswith("<network>")
    networks = resources.owned(session_id, ResourceKind.NETWORK)
    assert [identity.role for identity in networks] == ["net-lan"]
    router_nic, client_nic = artifacts.interfaces["router"][0], artifacts.interfaces["client"][0]
    assert router_nic.name == client_nic.name == "lan0"
    assert router_nic.network == client_nic.network == networks[0].name
    assert router_nic.mac != client_nic.mac
    assert f"address='{router_nic.mac}'" in resources.defined[1].replace('"', "'")
    assert artifacts.hostnames == ["router", "client"]

    service.destroy(session_id)
    assert resources.owned(session_id, ResourceKind.NETWORK) == ()
    assert resources.removed[-1] == networks[0].name


def test_failed_provisioning_removes_the_session_networks(tmp_path: Path) -> None:
    service, sessions, _machines, _artifacts, resources, _readiness, _executor = dependencies(
        tmp_path
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    resources.fail_on = "-client"

    with pytest.raises(RuntimeError, match="libvirt refused"):
        service.provision_many(
            scenario_id="branch-routing",
            requests=(
                VmHostRequest("router", base, interfaces=(NicSpec(network="lan", name="lan0"),)),
                VmHostRequest("client", base, interfaces=(NicSpec(network="lan", name="lan0"),)),
            ),
            networks=("lan",),
        )

    assert resources.registered == {}
    assert next(iter(sessions.states.values())).status is SessionStatus.FAILED


def test_service_rejects_nics_on_undeclared_networks(tmp_path: Path) -> None:
    service, sessions, *_rest = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    base.touch()
    request = VmHostRequest("router", base, interfaces=(NicSpec(network="wan", name="wan0"),))

    with pytest.raises(VmProvisioningError, match="undeclared scenario networks: wan"):
        service.provision_many(scenario_id="branch-routing", requests=(request,), networks=())
    assert sessions.states == {}


@pytest.mark.parametrize(
    ("hosts", "networks", "refusal"),
    [
        ((), (), "at least one VM host is required"),
        (("node1", "node1"), (), "VM host names must be unique"),
        (("node1",), ("lan", "lan"), "scenario network names must be unique"),
    ],
)
def test_a_malformed_request_is_refused_before_a_session_exists(
    hosts: tuple[str, ...], networks: tuple[str, ...], refusal: str, tmp_path: Path
) -> None:
    service, sessions, *_rest = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    requests = tuple(VmHostRequest(host, base) for host in hosts)

    with pytest.raises(VmProvisioningError, match=refusal):
        service.provision_many(scenario_id="peer-lab", requests=requests, networks=networks)
    assert sessions.states == {}


def test_destroy_cleans_up_a_machine_whose_domain_was_never_defined(tmp_path: Path) -> None:
    # The controller died between recording a machine and defining its domain.
    service, _sessions, machines, artifacts, resources, *_rest = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)
    del resources.registered[provisioned.machine.identity.name]

    destroyed = service.destroy(provisioned.state.session_id)

    assert destroyed.status is SessionStatus.DESTROYED
    assert resources.removed == []  # nothing in libvirt is touched that the lab does not own
    assert artifacts.destroyed == [(provisioned.state.session_id, "node1")]
    assert machines.list(provisioned.state.session_id) == ()


def test_a_destroy_that_fails_is_recorded_on_the_session(tmp_path: Path) -> None:
    service, sessions, machines, _artifacts, resources, *_rest = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)

    def refuse(identity: ResourceIdentity, *, missing_ok: bool = False) -> None:
        raise RuntimeError(f"libvirt refused to undefine {identity.name}")

    resources.remove = refuse  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="refused to undefine"):
        service.destroy(provisioned.state.session_id)

    state = sessions.states[provisioned.state.session_id]
    assert state.status is SessionStatus.FAILED
    assert state.error == f"libvirt refused to undefine {provisioned.machine.identity.name}"
    assert machines.list(provisioned.state.session_id)  # kept, so destroy can be retried


def test_only_a_ready_session_is_rebooted(tmp_path: Path) -> None:
    service, _sessions, *_rest, executor = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)
    service.destroy(provisioned.state.session_id)

    with pytest.raises(VmProvisioningError, match="only a ready scenario can be rebooted"):
        service.reboot(provisioned.state.session_id, ("node1",))
    assert ("sudo", "--", "systemctl", "reboot") not in [call[1] for call in executor.calls]


def test_a_reboot_touches_only_the_requested_hosts(tmp_path: Path) -> None:
    service, _sessions, _machines, _artifacts, _resources, readiness, executor = dependencies(
        tmp_path
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision_many(
        scenario_id="peer-lab",
        requests=(VmHostRequest("node1", base), VmHostRequest("node2", base)),
    )
    executor.calls.clear()

    service.reboot(provisioned.state.session_id, ("node2",))

    assert [call[1] for call in executor.calls] == [("sudo", "--", "systemctl", "reboot")]
    assert len(readiness.restarted) == 1


@pytest.mark.parametrize(
    ("break_it", "failure"),
    [
        ("sudo", "reboot command failed for node2: sudo: a password is required"),
        ("address", "machine has no address: node2"),
    ],
)
def test_a_reboot_that_cannot_be_sent_fails_the_session(
    break_it: str, failure: str, tmp_path: Path
) -> None:
    service, sessions, machines, _artifacts, _resources, _readiness, executor = dependencies(
        tmp_path
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="base-smoke", host_name="node2", base_image=base)
    if break_it == "sudo":
        executor.result = GuestCommandResult(1, "", "sudo: a password is required\n")
    else:
        machine = provisioned.machine
        machines.values[(machine.session_id, "node2")] = SessionMachine(
            machine.session_id,
            "node2",
            machine.identity,
            machine.username,
            machine.password,
            machine.private_key,
        )

    with pytest.raises(VmProvisioningError, match=failure):
        service.reboot(provisioned.state.session_id, ("node2",))
    state = sessions.states[provisioned.state.session_id]
    assert (state.status, state.error) == (SessionStatus.FAILED, failure)


@pytest.mark.parametrize("cloud_exit", [0, 1])
def test_scenario_nics_are_plugged_in_only_once_cloud_init_has_configured_them(
    cloud_exit: int, tmp_path: Path
) -> None:
    service, _sessions, _machines, artifacts, resources, _readiness, executor = dependencies(
        tmp_path, cloud_exit=cloud_exit
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    plugged_after: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    def plug(identity: ResourceIdentity, macs: tuple[str, ...]) -> None:
        plugged_after.append((identity.role, macs, executor.calls[-1][1]))

    resources.connect_interfaces = plug  # type: ignore[method-assign]
    requests = (
        VmHostRequest("router", base, interfaces=(NicSpec(network="lan", name="lan0"),)),
        VmHostRequest("mon", base),
        VmHostRequest(
            "client",
            base,
            interfaces=(NicSpec(network="lan", name="lan0"), NicSpec(network="wan", name="wan0")),
        ),
    )

    if cloud_exit:
        with pytest.raises(VmProvisioningError, match="cloud-init failed"):
            service.provision_many(
                scenario_id="branch-routing", requests=requests, networks=("lan", "wan")
            )
        assert plugged_after == []  # a guest cloud-init did not configure stays unplugged
        return
    service.provision_many(scenario_id="branch-routing", requests=requests, networks=("lan", "wan"))

    domains = [xml for xml in resources.defined if xml.startswith("<domain")]
    assert all(xml.count('<link state="down" />') == xml.count("<mac ") for xml in domains)
    waited = ("sudo", "cloud-init", "status", "--wait")
    macs = {role: tuple(nic.mac for nic in nics) for role, nics in artifacts.interfaces.items()}
    assert plugged_after == [  # mon has only its management NIC, which is never unplugged
        ("router", macs["router"], waited),
        ("client", macs["client"], waited),
    ]


def test_a_destroyed_guest_hands_back_its_management_lease_first(tmp_path: Path) -> None:
    removed_when_released: list[list[str]] = []

    class WatchingLeases(FakeLeases):
        def wait_released(self, domain_name: str) -> bool:
            removed_when_released.append(list(resources.removed))
            return True

    service, _sessions, _machines, _artifacts, resources, _readiness, executor = dependencies(
        tmp_path, leases=WatchingLeases()
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision_many(
        scenario_id="peer-lab",
        requests=(VmHostRequest("node1", base), VmHostRequest("node2", base)),
    )

    service.destroy(provisioned.state.session_id)

    releases = [call for call in executor.calls if call[1][:3] == ("sudo", "sh", "-c")]
    assert [call[1][3] for call in releases] == [RELEASE_MANAGEMENT_LEASE] * 2
    assert "ipv4.dhcp-send-release=1" in RELEASE_MANAGEMENT_LEASE
    names = [machine.identity.name for machine in provisioned.machines]
    assert removed_when_released == [[], names[:1]]  # each released before its domain goes
    assert resources.removed == names


@pytest.mark.parametrize(
    "unreachable",
    [GuestCommandResult(255, "", "Connection refused"), GuestCommandTimeout("ssh timed out")],
)
def test_a_guest_that_cannot_release_its_lease_is_destroyed_anyway(
    unreachable: GuestCommandResult | GuestCommandTimeout, tmp_path: Path
) -> None:
    leases = FakeLeases()
    service, _sessions, _machines, _artifacts, resources, _readiness, executor = dependencies(
        tmp_path, leases=leases
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)

    def broken(*_args: object, **_kwargs: object) -> GuestCommandResult:
        if isinstance(unreachable, Exception):
            raise unreachable
        return unreachable

    executor.run = broken  # type: ignore[method-assign]
    assert service.destroy(provisioned.state.session_id).status is SessionStatus.DESTROYED
    assert resources.removed == [provisioned.machine.identity.name]
    assert leases.released == []  # nothing was released, so nothing to wait for


def test_a_failed_provision_releases_the_leases_its_guests_took(tmp_path: Path) -> None:
    service, _sessions, _machines, _artifacts, _resources, _readiness, executor = dependencies(
        tmp_path, cloud_exit=1
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    released: list[str] = []
    original = executor.run

    def run(endpoint: GuestEndpoint, arguments: tuple[str, ...], **options: float) -> object:
        if arguments[:3] == ("sudo", "sh", "-c"):
            released.append(endpoint.host)
            return GuestCommandResult(0, "", "")
        return original(endpoint, arguments, **options)

    executor.run = run  # type: ignore[method-assign]
    with pytest.raises(VmProvisioningError, match="cloud-init failed"):
        service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)
    assert released == ["192.0.2.10"]


def test_a_session_whose_machines_vanished_from_libvirt_can_still_be_destroyed(
    tmp_path: Path,
) -> None:
    # Someone deleted the guests in virt-manager; only the lab's records remain.
    service, sessions, machines, artifacts, resources, *_rest = dependencies(tmp_path)
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision_many(
        scenario_id="peer-lab",
        requests=(VmHostRequest("node1", base), VmHostRequest("node2", base)),
    )
    resources.vanished = {machine.identity.name for machine in provisioned.machines}

    assert service.destroy(provisioned.state.session_id).status is SessionStatus.DESTROYED
    assert resources.registered == {} and resources.removed == []
    assert machines.list(provisioned.state.session_id) == ()
    assert len(artifacts.destroyed) == 2
    assert sessions.states[provisioned.state.session_id].status is SessionStatus.DESTROYED


def test_an_address_the_guest_no_longer_holds_is_never_released_from_it(tmp_path: Path) -> None:
    # Off for an hour, the guest lost its lease; another machine, perhaps one sharing this
    # runtime's key, may have it now, and must not have its network taken down.
    leases = FakeLeases()
    service, _sessions, _machines, _artifacts, resources, _readiness, executor = dependencies(
        tmp_path, leases=leases
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    provisioned = service.provision(scenario_id="base-smoke", host_name="node1", base_image=base)
    leases.address = "192.0.2.99"

    service.destroy(provisioned.state.session_id)

    assert all(call[1][:3] != ("sudo", "sh", "-c") for call in executor.calls)
    assert leases.released == []
    assert resources.removed == [provisioned.machine.identity.name]
