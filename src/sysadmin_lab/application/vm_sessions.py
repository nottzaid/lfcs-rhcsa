from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sysadmin_lab.application.guest_execution import (
    GuestCommandTimeout,
    GuestEndpoint,
    GuestExecutor,
    GuestReadiness,
    GuestReadinessError,
)
from sysadmin_lab.application.machines import (
    DomainLeaseReadiness,
    SessionMachineRepository,
)
from sysadmin_lab.application.resources import ResourceManager
from sysadmin_lab.application.session_artifacts import SessionArtifactBuilder
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.domain.models import DiskSpec, NicSpec
from sysadmin_lab.domain.resources import ResourceIdentity, ResourceKind
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import (
    DomainSpec,
    ScenarioInterface,
    domain_identity,
    interface_mac,
    network_identity,
    render_domain_xml,
    render_network_xml,
)

# NetworkManager sends a DHCP RELEASE when it takes a connection down, if configured to;
# the setting lives under /run, so it disappears with the machine. Taking the NIC down
# ends the SSH session that runs this, so it happens in the background.
RELEASE_MANAGEMENT_LEASE = """set -eu
mkdir -p /run/NetworkManager/conf.d
printf '[connection]\\nipv4.dhcp-send-release=1\\n' > /run/NetworkManager/conf.d/90-lal-release.conf
nmcli general reload conf
nohup sh -c 'sleep 0.5; nmcli device down enp1s0' >/dev/null 2>&1 &
"""


class VmProvisioningError(RuntimeError):
    pass


class MachineUnreachableError(RuntimeError):
    """A rebooted machine did not accept SSH again within the readiness window."""

    def __init__(self, host_name: str, detail: str) -> None:
        super().__init__(f"{host_name} did not come back after rebooting: {detail}")
        self.host_name = host_name


@dataclass(frozen=True, slots=True)
class ProvisionedSession:
    state: SessionState
    machines: tuple[SessionMachine, ...]

    @property
    def machine(self) -> SessionMachine:
        return self.machines[0]


@dataclass(frozen=True, slots=True)
class VmHostRequest:
    host_name: str
    base_image: Path
    memory_mib: int = 2048
    vcpus: int = 2
    data_disks: tuple[DiskSpec, ...] = ()
    interfaces: tuple[NicSpec, ...] = ()


class VmSessionService:
    """Production lifecycle for a session's KVM guests and isolated scenario networks.

    Every guest keeps its management NIC on libvirt's default network, which carries SSH
    for the learner and the checker. Scenario networks are separate isolated segments.
    """

    def __init__(
        self,
        *,
        sessions: SessionCoordinator,
        machines: SessionMachineRepository,
        resources: ResourceManager,
        artifacts: SessionArtifactBuilder,
        leases: DomainLeaseReadiness,
        guest_readiness: GuestReadiness,
        guest_executor: GuestExecutor,
    ) -> None:
        self._sessions = sessions
        self._machines = machines
        self._resources = resources
        self._artifacts = artifacts
        self._leases = leases
        self._guest_readiness = guest_readiness
        self._guest_executor = guest_executor

    def provision(
        self,
        *,
        scenario_id: str,
        host_name: str,
        base_image: Path,
        memory_mib: int = 2048,
        vcpus: int = 2,
        data_disks: tuple[DiskSpec, ...] = (),
    ) -> ProvisionedSession:
        request = VmHostRequest(host_name, base_image, memory_mib, vcpus, data_disks)
        return self.provision_many(scenario_id=scenario_id, requests=(request,))

    def provision_many(
        self,
        *,
        scenario_id: str,
        requests: tuple[VmHostRequest, ...],
        networks: tuple[str, ...] = (),
    ) -> ProvisionedSession:
        if not requests:
            raise VmProvisioningError("at least one VM host is required")
        if len({request.host_name for request in requests}) != len(requests):
            raise VmProvisioningError("VM host names must be unique")
        if len(set(networks)) != len(networks):
            raise VmProvisioningError("scenario network names must be unique")
        unknown = sorted(
            {nic.network for request in requests for nic in request.interfaces} - set(networks)
        )
        if unknown:
            raise VmProvisioningError(f"undeclared scenario networks: {', '.join(unknown)}")
        state = self._sessions.declare(scenario_id)
        state = self._sessions.transition(state.session_id, SessionStatus.PROVISIONING)
        created: list[SessionMachine] = []
        attempted_roles: list[str] = []
        unplugged: dict[str, tuple[str, ...]] = {}
        try:
            for network in networks:
                network_resource = network_identity(scenario_id, state.session_id, network)
                self._resources.define(
                    render_network_xml(network_resource), network_resource, start=True
                )
            for request in requests:
                attempted_roles.append(request.host_name)
                identity = domain_identity(scenario_id, state.session_id, request.host_name)
                interfaces = tuple(
                    ScenarioInterface(
                        name=nic.name,
                        network=network_identity(scenario_id, state.session_id, nic.network).name,
                        mac=interface_mac(state.session_id, request.host_name, nic.name),
                    )
                    for nic in request.interfaces
                )
                unplugged[request.host_name] = tuple(interface.mac for interface in interfaces)
                paths, access = self._artifacts.create(
                    session_id=state.session_id,
                    role=request.host_name,
                    hostname=request.host_name,
                    base_image=request.base_image,
                    data_disks=request.data_disks,
                    interfaces=interfaces,
                )
                machine = SessionMachine(
                    session_id=state.session_id,
                    host_name=request.host_name,
                    identity=identity,
                    username=access.username,
                    password=access.password,
                    private_key=access.private_key,
                )
                self._machines.add(machine)
                created.append(machine)
                xml = render_domain_xml(
                    DomainSpec(
                        identity=identity,
                        disk=paths.overlay,
                        seed_iso=paths.seed_iso,
                        memory_mib=request.memory_mib,
                        vcpus=request.vcpus,
                        data_disks=paths.data_disks,
                        interfaces=interfaces,
                    )
                )
                self._resources.define(xml, identity, start=True)

            ready: list[SessionMachine] = []
            for machine in created:
                address = self._leases.wait(machine.identity.name)
                machine = self._machines.update_address(machine, address)
                endpoint = GuestEndpoint(address, machine.username, machine.private_key)
                self._guest_readiness.wait(endpoint)
                cloud_init = self._guest_executor.run(
                    endpoint,
                    ("sudo", "cloud-init", "status", "--wait"),
                    timeout_seconds=240,
                )
                if not cloud_init.succeeded:
                    detail = cloud_init.stderr.strip() or cloud_init.stdout.strip()
                    raise VmProvisioningError(f"cloud-init failed: {detail}")
                if unplugged[machine.host_name]:
                    # cloud-init has named the scenario NICs and set NetworkManager to leave
                    # them alone, so they can carry traffic now.
                    self._resources.connect_interfaces(
                        machine.identity, unplugged[machine.host_name]
                    )
                ready.append(machine)
            state = self._sessions.transition(state.session_id, SessionStatus.READY)
            return ProvisionedSession(state, tuple(ready))
        except Exception as exc:
            self._cleanup_failed_provision(state, tuple(attempted_roles))
            self._sessions.transition(state.session_id, SessionStatus.FAILED, error=str(exc))
            raise

    def destroy(self, session_id: UUID) -> SessionState:
        self._sessions.transition(session_id, SessionStatus.DESTROYING)
        try:
            for machine in self._machines.list(session_id):
                if self._resources.is_registered(machine.identity):
                    self._release_address(machine)
                    self._resources.remove(machine.identity, missing_ok=True)
                self._artifacts.destroy(session_id, machine.host_name)
                self._machines.remove(machine)
            self._remove_networks(session_id)
            return self._sessions.transition(session_id, SessionStatus.DESTROYED)
        except Exception as exc:
            self._sessions.transition(session_id, SessionStatus.FAILED, error=str(exc))
            raise

    def reboot(self, session_id: UUID, host_names: tuple[str, ...]) -> None:
        state = self._sessions.get(session_id)
        if state.status is not SessionStatus.READY:
            raise VmProvisioningError("only a ready scenario can be rebooted")
        requested = set(host_names)
        if not requested:
            raise VmProvisioningError("at least one reboot host is required")
        machines = self._machines.list(session_id)
        available = {machine.host_name for machine in machines}
        unknown = sorted(requested - available)
        if unknown:
            raise VmProvisioningError(f"unknown reboot hosts: {', '.join(unknown)}")
        self._sessions.transition(session_id, SessionStatus.REBOOTING)
        try:
            for machine in machines:
                if machine.host_name not in requested:
                    continue
                if machine.address is None:
                    raise VmProvisioningError(f"machine has no address: {machine.host_name}")
                endpoint = GuestEndpoint(
                    machine.address,
                    machine.username,
                    machine.private_key,
                )
                result = self._guest_executor.run(
                    endpoint,
                    ("sudo", "--", "systemctl", "reboot"),
                    timeout_seconds=10,
                )
                if result.exit_code not in {0, 255}:
                    detail = result.stderr.strip() or result.stdout.strip()
                    raise VmProvisioningError(
                        f"reboot command failed for {machine.host_name}: {detail}"
                    )
                try:
                    self._guest_readiness.wait_for_restart(endpoint)
                except GuestReadinessError as exc:
                    raise MachineUnreachableError(machine.host_name, str(exc)) from exc
        except MachineUnreachableError:
            # The domain still exists; a boot-blocking change inside the guest is usually
            # the learner's to find on the console, so the session stays usable.
            self._sessions.transition(session_id, SessionStatus.READY)
            raise
        except Exception as exc:
            self._sessions.transition(session_id, SessionStatus.FAILED, error=str(exc))
            raise
        self._sessions.transition(session_id, SessionStatus.READY)

    def _cleanup_failed_provision(
        self, state: SessionState, attempted_roles: tuple[str, ...]
    ) -> None:
        cleaned: set[str] = set()
        for machine in self._machines.list(state.session_id):
            if self._resources.is_registered(machine.identity):
                self._release_address(machine)
                self._resources.remove(machine.identity, missing_ok=True)
            self._artifacts.destroy(state.session_id, machine.host_name)
            cleaned.add(machine.host_name)
            self._machines.remove(machine)
        for role in attempted_roles:
            if role not in cleaned:
                self._artifacts.destroy(state.session_id, role)
        self._remove_networks(state.session_id)

    def _release_address(self, machine: SessionMachine) -> None:
        """Have the guest hand back its management lease before it is destroyed.

        libvirt's default network holds a lease for an hour after its machine is gone,
        and every launch and reset makes machines with new MAC addresses, so an hour of
        resets would exhaust its 253 addresses. A guest that cannot be reached keeps its
        lease until it expires.
        """
        if machine.address is None or not self._leases.holds(
            machine.identity.name, machine.address
        ):
            # A machine that is off or gone may have lost its address to another guest,
            # possibly one sharing this runtime's key, which must not be taken offline.
            return
        endpoint = GuestEndpoint(machine.address, machine.username, machine.private_key)
        try:
            result = self._guest_executor.run(
                endpoint, ("sudo", "sh", "-c", RELEASE_MANAGEMENT_LEASE), timeout_seconds=10
            )
        except GuestCommandTimeout:
            return
        if result.succeeded:
            self._leases.wait_released(machine.identity.name)

    def owned_networks(self, session_id: UUID) -> tuple[ResourceIdentity, ...]:
        return self._resources.owned(session_id, ResourceKind.NETWORK)

    def _remove_networks(self, session_id: UUID) -> None:
        # Domains are gone by now, so no guest still holds a port on these bridges.
        for identity in self.owned_networks(session_id):
            self._resources.remove(identity, missing_ok=True)
