from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field

from sysadmin_lab.application.ports import CheckObservation, LabSession
from sysadmin_lab.application.verification import ScenarioVerifier, VerificationPhase
from sysadmin_lab.domain.models import ScenarioManifest
from tests.unit.test_models import minimal_manifest


@dataclass
class FakeDriver:
    phase: str = "broken"
    calls: list[str] = field(default_factory=list)

    def provision(self, manifest: ScenarioManifest) -> LabSession:
        self.calls.append("provision")
        self.phase = "broken"
        return LabSession(session_id="session-1", scenario_id=manifest.scenario_id)

    def run_checks(
        self, session: LabSession, manifest: ScenarioManifest
    ) -> tuple[CheckObservation, ...]:
        observations = tuple(
            CheckObservation(
                check_id=check.check_id,
                passed=self.phase in {"solved", "rebooted"},
                message=self.phase,
            )
            for check in manifest.checks
        )
        self.calls.extend(
            f"check:{self.phase}:{observation.check_id}" for observation in observations
        )
        return observations

    def apply_solution(
        self, session: LabSession, manifest: ScenarioManifest, solution: str
    ) -> None:
        self.calls.append(f"solve:{solution}")
        self.phase = "solved"

    def reboot(self, session: LabSession, hosts: tuple[str, ...]) -> None:
        self.calls.append(f"reboot:{','.join(hosts)}")
        self.phase = "rebooted"

    def reset(self, session: LabSession, manifest: ScenarioManifest) -> LabSession:
        self.calls.append("reset")
        self.phase = "broken"
        return LabSession(session_id="session-2", scenario_id=manifest.scenario_id)

    def destroy(self, session: LabSession) -> None:
        self.calls.append(f"destroy:{session.session_id}")


def test_verifier_replays_complete_persistent_lifecycle() -> None:
    raw = minimal_manifest()
    raw["persistence"] = {"reboot": True, "hosts": ["node1"]}
    manifest = ScenarioManifest.model_validate(raw)
    driver = FakeDriver()

    report = ScenarioVerifier(driver).verify(manifest)

    assert report.passed
    assert [phase.phase for phase in report.phases] == [
        VerificationPhase.INITIAL,
        VerificationPhase.SOLVED,
        VerificationPhase.REBOOTED,
        VerificationPhase.RESET,
        VerificationPhase.ALTERNATE_SOLVED,
        VerificationPhase.ALTERNATE_REBOOTED,
    ]
    assert driver.calls == [
        "provision",
        "check:broken:service-active",
        "solve:solutions/valid.yaml",
        "check:solved:service-active",
        "reboot:node1",
        "check:rebooted:service-active",
        "reset",
        "check:broken:service-active",
        "solve:solutions/alternate.yaml",
        "check:solved:service-active",
        "reboot:node1",
        "check:rebooted:service-active",
        "destroy:session-2",
    ]


def test_report_rejects_scenario_that_is_already_solved() -> None:
    class InitiallySolvedDriver(FakeDriver):
        def provision(self, manifest: ScenarioManifest) -> LabSession:
            session = super().provision(manifest)
            self.phase = "solved"
            return session

    manifest = ScenarioManifest.model_validate(minimal_manifest())
    report = ScenarioVerifier(InitiallySolvedDriver()).verify(manifest)
    assert not report.passed


def test_driver_is_destroyed_when_solution_application_fails() -> None:
    class FailingDriver(FakeDriver):
        def apply_solution(
            self, session: LabSession, manifest: ScenarioManifest, solution: str
        ) -> None:
            raise RuntimeError("solution failed")

    manifest = ScenarioManifest.model_validate(minimal_manifest())
    driver = FailingDriver()

    with suppress(RuntimeError):
        ScenarioVerifier(driver).verify(manifest)

    assert driver.calls[-1] == "destroy:session-1"


def test_report_rejects_a_reference_solution_that_does_not_work() -> None:
    class IneffectiveSolutionDriver(FakeDriver):
        def apply_solution(
            self, session: LabSession, manifest: ScenarioManifest, solution: str
        ) -> None:
            self.calls.append("ineffective-solution")

    manifest = ScenarioManifest.model_validate(minimal_manifest())
    report = ScenarioVerifier(IneffectiveSolutionDriver()).verify(manifest)
    assert not report.passed


def test_report_rejects_checker_error_in_expected_broken_phase() -> None:
    class ErroringInitialDriver(FakeDriver):
        def run_checks(
            self, session: LabSession, manifest: ScenarioManifest
        ) -> tuple[CheckObservation, ...]:
            observations = super().run_checks(session, manifest)
            if self.phase == "broken":
                return tuple(
                    CheckObservation(item.check_id, False, "checker crashed", error=True)
                    for item in observations
                )
            return observations

    manifest = ScenarioManifest.model_validate(minimal_manifest())
    report = ScenarioVerifier(ErroringInitialDriver()).verify(manifest)

    assert not report.passed


class ReplayDriver(FakeDriver):
    """Solutions named runtime-only survive the live check but not a reboot."""

    def apply_solution(
        self, session: LabSession, manifest: ScenarioManifest, solution: str
    ) -> None:
        self.calls.append(f"solve:{solution}")
        self.phase = {"runtime-only": "solved-until-reboot", "wrong": "broken"}.get(
            solution.split("/")[-1].removesuffix(".yaml"), "solved"
        )

    def run_checks(
        self, session: LabSession, manifest: ScenarioManifest
    ) -> tuple[CheckObservation, ...]:
        passed = self.phase in {"solved", "rebooted", "solved-until-reboot"}
        return tuple(
            CheckObservation(check.check_id, passed, self.phase) for check in manifest.checks
        )

    def reboot(self, session: LabSession, hosts: tuple[str, ...]) -> None:
        self.calls.append(f"reboot:{','.join(hosts)}")
        self.phase = "broken" if self.phase == "solved-until-reboot" else "rebooted"


def persistent(rejected: list[str]) -> ScenarioManifest:
    raw = minimal_manifest()
    raw["persistence"] = {"reboot": True, "hosts": ["node1"]}
    raw["alternate_solutions"] = []
    raw["rejected_solutions"] = rejected
    return ScenarioManifest.model_validate(raw)


def test_rejected_solutions_must_not_be_solved_even_if_the_live_state_passes() -> None:
    manifest = persistent(["actions/runtime-only.yaml", "actions/wrong.yaml"])

    report = ScenarioVerifier(ReplayDriver()).verify(manifest)

    assert report.passed
    assert [phase.phase for phase in report.phases[:3]] == [
        VerificationPhase.INITIAL,
        VerificationPhase.SOLVED,
        VerificationPhase.REBOOTED,
    ]
    assert [(phase.phase, phase.passed, phase.expected) for phase in report.phases[3:]] == [
        (VerificationPhase.RESET, False, False),
        (VerificationPhase.REJECTED, True, None),
        (VerificationPhase.REJECTED_REBOOTED, False, False),
        (VerificationPhase.RESET, False, False),
        (VerificationPhase.REJECTED, False, False),
    ]


def test_a_rejected_solution_that_the_grader_accepts_fails_verification() -> None:
    class AcceptEverything(ReplayDriver):
        def apply_solution(
            self, session: LabSession, manifest: ScenarioManifest, solution: str
        ) -> None:
            self.phase = "solved"

    report = ScenarioVerifier(AcceptEverything()).verify(persistent(["actions/wrong.yaml"]))

    assert not report.passed
    assert [phase.phase for phase in report.phases if not phase.accepted] == [
        VerificationPhase.REJECTED_REBOOTED
    ]
