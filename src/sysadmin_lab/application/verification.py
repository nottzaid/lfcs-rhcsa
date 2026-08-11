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
    ALTERNATE_SOLVED = "alternate-solved"
    ALTERNATE_REBOOTED = "alternate-rebooted"


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: VerificationPhase
    observations: tuple[CheckObservation, ...]
    passed: bool
    solution: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    scenario_id: str
    phases: tuple[PhaseResult, ...]

    @property
    def passed(self) -> bool:
        if any(
            observation.error
            for phase in self.phases
            for observation in phase.observations
        ):
            return False
        present = {phase.phase for phase in self.phases}
        if not {
            VerificationPhase.INITIAL,
            VerificationPhase.SOLVED,
            VerificationPhase.RESET,
        }.issubset(present):
            return False
        expected = {
            VerificationPhase.INITIAL: False,
            VerificationPhase.SOLVED: True,
            VerificationPhase.RESET: False,
            VerificationPhase.REBOOTED: True,
            VerificationPhase.ALTERNATE_SOLVED: True,
            VerificationPhase.ALTERNATE_REBOOTED: True,
        }
        return all(phase.passed is expected[phase.phase] for phase in self.phases)


class ScenarioVerifier:
    """Replays the permanent acceptance lifecycle for one scenario."""

    def __init__(self, driver: ScenarioDriver) -> None:
        self._driver = driver

    def verify(self, manifest: ScenarioManifest) -> VerificationReport:
        session = self._driver.provision(manifest)
        phases: list[PhaseResult] = []
        try:
            phases.append(self._evaluate(VerificationPhase.INITIAL, session, manifest))

            self._driver.apply_solution(session, manifest, manifest.reference_solution)
            phases.append(
                self._evaluate(
                    VerificationPhase.SOLVED,
                    session,
                    manifest,
                    solution=manifest.reference_solution,
                )
            )

            if manifest.persistence.reboot:
                reboot_hosts = manifest.persistence.hosts or tuple(
                    host.name for host in manifest.topology.hosts
                )
                self._driver.reboot(session, reboot_hosts)
                phases.append(
                    self._evaluate(
                        VerificationPhase.REBOOTED,
                        session,
                        manifest,
                        solution=manifest.reference_solution,
                    )
                )

            session = self._driver.reset(session, manifest)
            phases.append(self._evaluate(VerificationPhase.RESET, session, manifest))

            for index, solution in enumerate(manifest.alternate_solutions):
                self._driver.apply_solution(session, manifest, solution)
                phases.append(
                    self._evaluate(
                        VerificationPhase.ALTERNATE_SOLVED,
                        session,
                        manifest,
                        solution=solution,
                    )
                )
                if manifest.persistence.reboot:
                    self._driver.reboot(session, reboot_hosts)
                    phases.append(
                        self._evaluate(
                            VerificationPhase.ALTERNATE_REBOOTED,
                            session,
                            manifest,
                            solution=solution,
                        )
                    )
                if index < len(manifest.alternate_solutions) - 1:
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
        *,
        solution: str | None = None,
    ) -> PhaseResult:
        observations = self._driver.run_checks(session, manifest)
        required_ids = {check.check_id for check in manifest.checks if check.required}
        passed = not any(observation.error for observation in observations) and all(
            observation.passed
            for observation in observations
            if observation.check_id in required_ids
        )
        observed_ids = {observation.check_id for observation in observations}
        if observed_ids != {check.check_id for check in manifest.checks}:
            passed = False
        return PhaseResult(
            phase=phase,
            observations=observations,
            passed=passed,
            solution=solution,
        )
