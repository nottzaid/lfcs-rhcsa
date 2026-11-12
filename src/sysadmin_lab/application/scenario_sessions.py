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
from sysadmin_lab.application.vm_sessions import (
    MachineUnreachableError,
    ProvisionedSession,
    VmHostRequest,
    VmSessionService,
)
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


@dataclass(frozen=True, slots=True)
class LearnerCheckReport:
    """A learner's check: the live state, then the state after any required reboot."""

    live: CheckReport
    reboot_hosts: tuple[str, ...] = ()
    after_reboot: CheckReport | None = None
    unreachable_host: str | None = None

    @property
    def live_passed(self) -> bool:
        return self.live.required_passed and not self.live.has_errors

    @property
    def persistence_proven(self) -> bool:
        if not self.reboot_hosts:
            return True
        report = self.after_reboot
        return report is not None and report.required_passed and not report.has_errors

    @property
    def solved(self) -> bool:
        return self.live_passed and self.persistence_proven

    @property
    def final(self) -> CheckReport:
        return self.after_reboot or self.live

    @property
    def earned_weight(self) -> int:
        return 0 if self.unreachable_host else self.final.earned_weight


class ScenarioSessionService:
    """Learner-facing lifecycle for currently supported scenario topologies."""

    def __init__(
        self,
        *,
        sessions: SessionCoordinator,
        machines: SessionMachineRepository,
        vm_sessions: VmSessionService,
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
        hosts = self._supported_hosts(manifest, require_verified=require_verified)
        requests: list[VmHostRequest] = []
        for host in hosts:
            try:
                base_image = base_images[host.image]
            except KeyError as exc:
                raise ScenarioLaunchError(
                    f"no verified base image is available for {host.image}"
                ) from exc
            requests.append(
                VmHostRequest(
                    host_name=host.name,
                    base_image=base_image,
                    memory_mib=host.memory_mib,
                    vcpus=host.vcpus,
                    data_disks=host.disks,
                    interfaces=host.nics,
                )
            )
        provisioned = self._vm_sessions.provision_many(
            scenario_id=manifest.scenario_id,
            requests=tuple(requests),
            networks=tuple(network.name for network in manifest.topology.networks),
        )
        try:
            endpoints = self._endpoints(provisioned.state.session_id)
            self._actions.run(setup, endpoints)
            report = self._checks.run(provisioned.state.session_id, manifest)
            if report.has_errors:
                failed = next(r.observation for r in report.results if r.observation.error)
                raise ScenarioLaunchError(
                    "fresh scenario produced a checker error in "
                    f"{failed.check_id}: {failed.message}"
                )
            if report.required_passed:
                raise ScenarioLaunchError("fresh scenario already satisfies every required check")
        except Exception:
            self._vm_sessions.destroy(provisioned.state.session_id)
            raise
        return StartedScenario(provisioned, report)

    def check(
        self,
        session_id: UUID,
        manifest: ScenarioManifest,
        *,
        prove_persistence: bool = True,
    ) -> LearnerCheckReport:
        """Grade the live state and, once it passes, prove required persistence by reboot."""
        live = self._checks.run(session_id, manifest)
        report = LearnerCheckReport(live, manifest.reboot_hosts)
        if report.reboot_hosts and prove_persistence and report.live_passed:
            try:
                self._vm_sessions.reboot(session_id, report.reboot_hosts)
            except MachineUnreachableError as exc:
                report = LearnerCheckReport(live, report.reboot_hosts, None, exc.host_name)
            else:
                after = self._checks.run(session_id, manifest)
                report = LearnerCheckReport(live, report.reboot_hosts, after)
        self._progress.record(
            CheckAttempt.now(
                session_id=session_id,
                scenario_id=manifest.scenario_id,
                scenario_version=manifest.version,
                earned_weight=report.earned_weight,
                available_weight=report.final.available_weight,
                required_passed=report.solved,
                has_errors=report.final.has_errors,
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
    def _supported_hosts(
        manifest: ScenarioManifest, *, require_verified: bool = True
    ) -> tuple[HostSpec, ...]:
        if require_verified and manifest.status is not ScenarioStatus.VERIFIED:
            raise ScenarioLaunchError(f"scenario is not verified: {manifest.scenario_id}")
        return manifest.topology.hosts
