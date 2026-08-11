from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field

from sysadmin_lab.application.ports import CheckObservation, LabSession
from sysadmin_lab.application.verification import ScenarioVerifier, VerificationPhase
from sysadmin_lab.domain.models import CheckSpec, ScenarioManifest
from tests.unit.test_models import minimal_manifest


@dataclass
class FakeDriver:
    phase: str = "broken"
    calls: list[str] = field(default_factory=list)

    def provision(self, manifest: ScenarioManifest) -> LabSession:
        self.calls.append("provision")
        self.phase = "broken"
        return LabSession(session_id="session-1", scenario_id=manifest.scenario_id)

    def run_check(self, session: LabSession, check: CheckSpec) -> CheckObservation:
        self.calls.append(f"check:{self.phase}:{check.check_id}")
        return CheckObservation(
            check_id=check.check_id,
            passed=self.phase in {"solved", "rebooted"},
            message=self.phase,
        )

    def apply_reference_solution(self, session: LabSession, manifest: ScenarioManifest) -> None:
        self.calls.append("solve")
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
    ]
    assert driver.calls == [
        "provision",
        "check:broken:service-active",
        "solve",
        "check:solved:service-active",
        "reboot:node1",
        "check:rebooted:service-active",
        "reset",
        "check:broken:service-active",
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
        def apply_reference_solution(self, session: LabSession, manifest: ScenarioManifest) -> None:
            raise RuntimeError("solution failed")

    manifest = ScenarioManifest.model_validate(minimal_manifest())
    driver = FailingDriver()

    with suppress(RuntimeError):
        ScenarioVerifier(driver).verify(manifest)

    assert driver.calls[-1] == "destroy:session-1"


def test_report_rejects_a_reference_solution_that_does_not_work() -> None:
    class IneffectiveSolutionDriver(FakeDriver):
        def apply_reference_solution(self, session: LabSession, manifest: ScenarioManifest) -> None:
            self.calls.append("ineffective-solution")

    manifest = ScenarioManifest.model_validate(minimal_manifest())
    report = ScenarioVerifier(IneffectiveSolutionDriver()).verify(manifest)
    assert not report.passed
