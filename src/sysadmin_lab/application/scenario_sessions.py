from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sysadmin_lab.application.actions import ActionRunner
from sysadmin_lab.application.checking import CheckReport
from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.application.machines import SessionMachineRepository
from sysadmin_lab.application.progress import ProgressService
from sysadmin_lab.application.session_checks import SessionCheckService
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.application.vm_sessions import ProvisionedSession, SingleHostVmSessionService
from sysadmin_lab.domain.actions import ActionManifest
from sysadmin_lab.domain.models import HostSpec, ScenarioManifest, ScenarioStatus
from sysadmin_lab.domain.progress import CheckAttempt
from sysadmin_lab.domain.sessions import SessionState


class ScenarioLaunchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StartedScenario:
    provisioned: ProvisionedSession
    initial_report: CheckReport


class ScenarioSessionService:
    """Learner-facing lifecycle for currently supported scenario topologies."""

    def __init__(
        self,
        *,
        sessions: SessionCoordinator,
        machines: SessionMachineRepository,
        vm_sessions: SingleHostVmSessionService,
        checks: SessionCheckService,
        actions: ActionRunner,
        progress: ProgressService,
    ) -> None:
        self._sessions = sessions
        self._machines = machines
        self._vm_sessions = vm_sessions
        self._checks = checks
        self._actions = actions
        self._progress = progress

    def start(
        self,
        manifest: ScenarioManifest,
        setup: ActionManifest,
        base_images: dict[str, Path],
        *,
        require_verified: bool = True,
    ) -> StartedScenario:
        host = self._supported_host(manifest, require_verified=require_verified)
        try:
            base_image = base_images[host.image]
        except KeyError as exc:
            raise ScenarioLaunchError(
                f"no verified base image is available for {host.image}"
            ) from exc
        provisioned = self._vm_sessions.provision(
            scenario_id=manifest.scenario_id,
            host_name=host.name,
            base_image=base_image,
            memory_mib=host.memory_mib,
            vcpus=host.vcpus,
        )
        try:
            endpoints = self._endpoints(provisioned.state.session_id)
            self._actions.run(setup, endpoints)
            report = self._checks.run(provisioned.state.session_id, manifest)
            if report.has_errors:
                raise ScenarioLaunchError("fresh scenario produced a checker error")
            if report.required_passed:
                raise ScenarioLaunchError("fresh scenario already satisfies every required check")
        except Exception:
            self._vm_sessions.destroy(provisioned.state.session_id)
            raise
        return StartedScenario(provisioned, report)

    def check(self, session_id: UUID, manifest: ScenarioManifest) -> CheckReport:
        report = self._checks.run(session_id, manifest)
        self._progress.record(
            CheckAttempt.now(
                session_id=session_id,
                scenario_id=manifest.scenario_id,
                scenario_version=manifest.version,
                earned_weight=report.earned_weight,
                available_weight=report.available_weight,
                required_passed=report.required_passed,
                has_errors=report.has_errors,
            )
        )
        return report

    def reset(
        self,
        session_id: UUID,
        manifest: ScenarioManifest,
        setup: ActionManifest,
        base_images: dict[str, Path],
        *,
        require_verified: bool = True,
    ) -> StartedScenario:
        state = self._sessions.get(session_id)
        if state.scenario_id != manifest.scenario_id:
            raise ScenarioLaunchError(
                f"session runs {state.scenario_id}, not {manifest.scenario_id}"
            )
        self._vm_sessions.destroy(session_id)
        return self.start(
            manifest,
            setup,
            base_images,
            require_verified=require_verified,
        )

    def destroy(self, session_id: UUID) -> SessionState:
        return self._vm_sessions.destroy(session_id)

    def _endpoints(self, session_id: UUID) -> dict[str, GuestEndpoint]:
        endpoints: dict[str, GuestEndpoint] = {}
        for machine in self._machines.list(session_id):
            if machine.address is None:
                raise ScenarioLaunchError(f"machine has no reachable address: {machine.host_name}")
            endpoints[machine.host_name] = GuestEndpoint(
                machine.address,
                machine.username,
                machine.private_key,
            )
        return endpoints

    @staticmethod
    def _supported_host(
        manifest: ScenarioManifest, *, require_verified: bool = True
    ) -> HostSpec:
        if require_verified and manifest.status is not ScenarioStatus.VERIFIED:
            raise ScenarioLaunchError(f"scenario is not verified: {manifest.scenario_id}")
        if len(manifest.topology.hosts) != 1:
            raise ScenarioLaunchError("this release supports one-host scenarios only")
        if manifest.topology.networks:
            raise ScenarioLaunchError("custom scenario networks are not supported yet")
        host = manifest.topology.hosts[0]
        if host.nics or host.disks or host.nested_virtualization:
            raise ScenarioLaunchError("this scenario topology needs an unsupported VM feature")
        return host
