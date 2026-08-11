from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sysadmin_lab.application.actions import ActionRunner
from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.application.machines import SessionMachineRepository
from sysadmin_lab.application.ports import CheckObservation, LabSession
from sysadmin_lab.application.scenario_sessions import ScenarioSessionService
from sysadmin_lab.application.session_checks import SessionCheckService
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.application.vm_sessions import SingleHostVmSessionService
from sysadmin_lab.catalog import load_action_manifest
from sysadmin_lab.domain.models import ScenarioManifest
from sysadmin_lab.domain.sessions import SessionStatus


class VmScenarioDriver:
    """Connect the generic acceptance verifier to disposable production VMs."""

    def __init__(
        self,
        *,
        scenario_directory: Path,
        base_images: dict[str, Path],
        sessions: SessionCoordinator,
        machines: SessionMachineRepository,
        vm_sessions: SingleHostVmSessionService,
        checks: SessionCheckService,
        scenarios: ScenarioSessionService,
        actions: ActionRunner,
    ) -> None:
        self._scenario_directory = scenario_directory.resolve()
        self._base_images = base_images
        self._sessions = sessions
        self._machines = machines
        self._vm_sessions = vm_sessions
        self._checks = checks
        self._scenarios = scenarios
        self._actions = actions

    def provision(self, manifest: ScenarioManifest) -> LabSession:
        setup = load_action_manifest(self._scenario_directory / manifest.setup)
        started = self._scenarios.start(manifest, setup, self._base_images)
        return LabSession(str(started.provisioned.state.session_id), manifest.scenario_id)

    def run_checks(
        self, session: LabSession, manifest: ScenarioManifest
    ) -> tuple[CheckObservation, ...]:
        report = self._checks.run(UUID(session.session_id), manifest)
        return tuple(result.observation for result in report.results)

    def apply_solution(
        self, session: LabSession, manifest: ScenarioManifest, solution: str
    ) -> None:
        action_manifest = load_action_manifest(self._scenario_directory / solution)
        self._actions.run(action_manifest, self._endpoints(UUID(session.session_id)))

    def reboot(self, session: LabSession, hosts: tuple[str, ...]) -> None:
        self._vm_sessions.reboot(UUID(session.session_id), hosts)

    def reset(self, session: LabSession, manifest: ScenarioManifest) -> LabSession:
        setup = load_action_manifest(self._scenario_directory / manifest.setup)
        started = self._scenarios.reset(
            UUID(session.session_id), manifest, setup, self._base_images
        )
        return LabSession(str(started.provisioned.state.session_id), manifest.scenario_id)

    def destroy(self, session: LabSession) -> None:
        session_id = UUID(session.session_id)
        state = self._sessions.get(session_id)
        if state.status in {SessionStatus.READY, SessionStatus.FAILED}:
            self._vm_sessions.destroy(session_id)

    def _endpoints(self, session_id: UUID) -> dict[str, GuestEndpoint]:
        endpoints: dict[str, GuestEndpoint] = {}
        for machine in self._machines.list(session_id):
            if machine.address is None:
                raise RuntimeError(f"machine has no reachable address: {machine.host_name}")
            endpoints[machine.host_name] = GuestEndpoint(
                machine.address,
                machine.username,
                machine.private_key,
            )
        return endpoints
