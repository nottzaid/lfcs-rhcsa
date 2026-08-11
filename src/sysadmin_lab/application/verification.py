from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sysadmin_lab.application.ports import CheckObservation, LabSession, ScenarioDriver
from sysadmin_lab.domain.models import ScenarioManifest


class VerificationPhase(StrEnum):
    INITIAL = "initial"
    SOLVED = "solved"
    REBOOTED = "rebooted"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: VerificationPhase
    observations: tuple[CheckObservation, ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class VerificationReport:
    scenario_id: str
    phases: tuple[PhaseResult, ...]

    @property
    def passed(self) -> bool:
        expected = {
            VerificationPhase.INITIAL: False,
            VerificationPhase.SOLVED: True,
            VerificationPhase.RESET: False,
        }
        for phase in self.phases:
            if phase.phase in expected and phase.passed is not expected[phase.phase]:
                return False
            if phase.phase is VerificationPhase.REBOOTED and not phase.passed:
                return False
        return True


class ScenarioVerifier:
    """Replays the permanent acceptance lifecycle for one scenario."""

    def __init__(self, driver: ScenarioDriver) -> None:
        self._driver = driver

    def verify(self, manifest: ScenarioManifest) -> VerificationReport:
        session = self._driver.provision(manifest)
        phases: list[PhaseResult] = []
        try:
            phases.append(self._evaluate(VerificationPhase.INITIAL, session, manifest))

            self._driver.apply_reference_solution(session, manifest)
            phases.append(self._evaluate(VerificationPhase.SOLVED, session, manifest))

            if manifest.persistence.reboot:
                reboot_hosts = manifest.persistence.hosts or tuple(
                    host.name for host in manifest.topology.hosts
                )
                self._driver.reboot(session, reboot_hosts)
                phases.append(self._evaluate(VerificationPhase.REBOOTED, session, manifest))

            session = self._driver.reset(session, manifest)
            phases.append(self._evaluate(VerificationPhase.RESET, session, manifest))
        finally:
            self._driver.destroy(session)

        return VerificationReport(scenario_id=manifest.scenario_id, phases=tuple(phases))

    def _evaluate(
        self,
        phase: VerificationPhase,
        session: LabSession,
        manifest: ScenarioManifest,
    ) -> PhaseResult:
        observations = tuple(self._driver.run_check(session, check) for check in manifest.checks)
        required_ids = {check.check_id for check in manifest.checks if check.required}
        passed = all(
            observation.passed
            for observation in observations
            if observation.check_id in required_ids
        )
        return PhaseResult(phase=phase, observations=observations, passed=passed)
