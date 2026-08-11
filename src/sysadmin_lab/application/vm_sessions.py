from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sysadmin_lab.application.guest_execution import (
    GuestEndpoint,
    GuestExecutor,
    GuestReadiness,
)
from sysadmin_lab.application.machines import (
    DomainLeaseReadiness,
    SessionMachineRepository,
)
from sysadmin_lab.application.resources import ResourceManager
from sysadmin_lab.application.session_artifacts import SessionArtifactBuilder
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.domain.resources import ResourceIdentity
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import DomainSpec, domain_identity, render_domain_xml


class VmProvisioningError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProvisionedSession:
    state: SessionState
    machine: SessionMachine


class SingleHostVmSessionService:
    """Production vertical slice for one default-network KVM guest."""

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
    ) -> ProvisionedSession:
        state = self._sessions.declare(scenario_id)
        state = self._sessions.transition(state.session_id, SessionStatus.PROVISIONING)
        identity = domain_identity(scenario_id, state.session_id, host_name)
        machine: SessionMachine | None = None
        try:
            paths, access = self._artifacts.create(
                session_id=state.session_id,
                role=host_name,
                hostname=identity.name,
                base_image=base_image,
            )
            machine = SessionMachine(
                session_id=state.session_id,
                host_name=host_name,
                identity=identity,
                username=access.username,
                password=access.password,
                private_key=access.private_key,
            )
            self._machines.add(machine)
            xml = render_domain_xml(
                DomainSpec(
                    identity=identity,
                    disk=paths.overlay,
                    seed_iso=paths.seed_iso,
                    memory_mib=memory_mib,
                    vcpus=vcpus,
                )
            )
            self._resources.define(xml, identity, start=True)
            address = self._leases.wait(identity.name)
            machine = self._machines.update_address(machine, address)
            endpoint = GuestEndpoint(address, access.username, access.private_key)
            self._guest_readiness.wait(endpoint)
            cloud_init = self._guest_executor.run(
                endpoint,
                ("sudo", "cloud-init", "status", "--wait"),
                timeout_seconds=240,
            )
            if not cloud_init.succeeded:
                detail = cloud_init.stderr.strip() or cloud_init.stdout.strip()
                raise VmProvisioningError(f"cloud-init failed: {detail}")
            state = self._sessions.transition(state.session_id, SessionStatus.READY)
            return ProvisionedSession(state, machine)
        except Exception as exc:
            self._cleanup_failed_provision(state, identity, machine)
            self._sessions.transition(state.session_id, SessionStatus.FAILED, error=str(exc))
            raise

    def destroy(self, session_id: UUID) -> SessionState:
        self._sessions.transition(session_id, SessionStatus.DESTROYING)
        try:
            for machine in self._machines.list(session_id):
                if self._resources.is_registered(machine.identity):
                    self._resources.remove(machine.identity)
                self._artifacts.destroy(session_id, machine.host_name)
                self._machines.remove(machine)
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
                self._guest_readiness.wait_for_restart(endpoint)
        except Exception as exc:
            self._sessions.transition(session_id, SessionStatus.FAILED, error=str(exc))
            raise
        self._sessions.transition(session_id, SessionStatus.READY)

    def _cleanup_failed_provision(
        self,
        state: SessionState,
        identity: ResourceIdentity,
        machine: SessionMachine | None,
    ) -> None:
        typed_identity = machine.identity if machine is not None else identity
        if self._resources.is_registered(typed_identity):
            self._resources.remove(typed_identity)
        self._artifacts.destroy(state.session_id, typed_identity.role)
        if machine is not None and self._machines.get(state.session_id, machine.host_name):
            self._machines.remove(machine)
