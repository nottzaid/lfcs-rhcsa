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
    REJECTED = "rejected"
    REJECTED_REBOOTED = "rejected-rebooted"


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: VerificationPhase
    observations: tuple[CheckObservation, ...]
    passed: bool
    solution: str | None = None
    # None means either outcome is acceptable: a rejected solution may pass the live
    # state as long as the reboot that follows exposes it.
    expected: bool | None = None

    @property
    def errored(self) -> bool:
        return any(observation.error for observation in self.observations)

    @property
    def accepted(self) -> bool:
        return not self.errored and (self.expected is None or self.passed is self.expected)


@dataclass(frozen=True, slots=True)
class VerificationReport:
    scenario_id: str
    phases: tuple[PhaseResult, ...]

    @property
    def passed(self) -> bool:
        present = {phase.phase for phase in self.phases}
        required = {VerificationPhase.INITIAL, VerificationPhase.SOLVED, VerificationPhase.RESET}
        return required <= present and all(phase.accepted for phase in self.phases)


class ScenarioVerifier:
    """Replays a scenario's acceptance contract, judging solutions as a learner's check would.

    A solution counts as solved when the live state passes every required check and, when
    the scenario requires persistence, still does after rebooting. The reference and
    alternate solutions must be solved; rejected solutions must not be.
    """

    def __init__(self, driver: ScenarioDriver) -> None:
        self._driver = driver

    def verify(self, manifest: ScenarioManifest) -> VerificationReport:
        session = self._driver.provision(manifest)
        phases: list[PhaseResult] = []
        try:
            phases.append(self._evaluate(VerificationPhase.INITIAL, session, manifest, False))
            phases.extend(self._solve(session, manifest, manifest.reference_solution, accept=True))
            trials = [(solution, True) for solution in manifest.alternate_solutions]
            trials += [(solution, False) for solution in manifest.rejected_solutions]
            session = self._driver.reset(session, manifest)
            phases.append(self._evaluate(VerificationPhase.RESET, session, manifest, False))
            for index, (solution, accept) in enumerate(trials):
                if index:
                    session = self._driver.reset(session, manifest)
                    phases.append(self._evaluate(VerificationPhase.RESET, session, manifest, False))
                phases.extend(self._solve(session, manifest, solution, accept=accept))
        finally:
            self._driver.destroy(session)

        return VerificationReport(scenario_id=manifest.scenario_id, phases=tuple(phases))

    def _solve(
        self, session: LabSession, manifest: ScenarioManifest, solution: str, *, accept: bool
    ) -> list[PhaseResult]:
        if solution == manifest.reference_solution:
            live_phase, reboot_phase = VerificationPhase.SOLVED, VerificationPhase.REBOOTED
        elif accept:
            live_phase = VerificationPhase.ALTERNATE_SOLVED
            reboot_phase = VerificationPhase.ALTERNATE_REBOOTED
        else:
            live_phase = VerificationPhase.REJECTED
            reboot_phase = VerificationPhase.REJECTED_REBOOTED

        self._driver.apply_solution(session, manifest, solution)
        live = self._evaluate(live_phase, session, manifest, accept, solution)
        if not manifest.reboot_hosts or not live.passed:
            return [live]
        if not accept:
            live = PhaseResult(live.phase, live.observations, live.passed, solution, None)
        self._driver.reboot(session, manifest.reboot_hosts)
        return [live, self._evaluate(reboot_phase, session, manifest, accept, solution)]

    def _evaluate(
        self,
        phase: VerificationPhase,
        session: LabSession,
        manifest: ScenarioManifest,
        expected: bool,
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
            expected=expected,
        )
