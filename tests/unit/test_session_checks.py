from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.application.checking import CheckReport, CheckResult
from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.application.session_checks import (
    SessionCheckService,
    SessionScenarioMismatchError,
)
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.domain.models import ScenarioManifest
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity
from tests.unit.test_models import minimal_manifest

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


@dataclass
class Sessions:
    state: SessionState

    def create(self, state: SessionState) -> None:
        self.state = state

    def get(self, session_id: UUID) -> SessionState | None:
        return self.state if session_id == self.state.session_id else None

    def save(self, previous_revision: int, state: SessionState) -> None:
        assert self.state.revision == previous_revision
        self.state = state


@dataclass
class Machines:
    values: tuple[SessionMachine, ...]

    def list(self, session_id: UUID) -> tuple[SessionMachine, ...]:
        return self.values


@dataclass
class Engine:
    report: CheckReport
    endpoints: dict[str, GuestEndpoint] = field(default_factory=dict)
    error: Exception | None = None

    def run(self, checks: tuple, endpoints: dict[str, GuestEndpoint]) -> CheckReport:
        self.endpoints = endpoints
        if self.error:
            raise self.error
        return self.report


def ready_state(scenario_id: str = "valid-scenario") -> SessionState:
    return (
        SessionState.declared(scenario_id, SESSION_ID)
        .transition(SessionStatus.PROVISIONING)
        .transition(SessionStatus.READY)
    )


def test_session_check_transitions_and_builds_endpoints(tmp_path: Path) -> None:
    manifest = ScenarioManifest.model_validate(minimal_manifest())
    state_repository = Sessions(ready_state())
    machine = SessionMachine(
        SESSION_ID,
        "node1",
        domain_identity("valid-scenario", SESSION_ID, "node1"),
        "labadmin",
        "secret",
        (tmp_path / "key").resolve(),
        "192.0.2.10",
    )
    observation = CheckObservation("service-active", True, "matched")
    report = CheckReport((CheckResult(manifest.checks[0], observation),))
    engine = Engine(report)
    service = SessionCheckService(
        SessionCoordinator(state_repository),
        Machines((machine,)),
        engine,  # type: ignore[arg-type]
    )

    assert service.run(SESSION_ID, manifest) == report
    assert state_repository.state.status is SessionStatus.READY
    assert state_repository.state.revision == 4
    assert engine.endpoints["node1"].host == "192.0.2.10"


def test_session_check_rejects_wrong_scenario_before_transition() -> None:
    manifest = ScenarioManifest.model_validate(minimal_manifest())
    repository = Sessions(ready_state("other-scenario"))
    service = SessionCheckService(
        SessionCoordinator(repository),
        Machines(()),
        Engine(CheckReport(())),  # type: ignore[arg-type]
    )
    with pytest.raises(SessionScenarioMismatchError, match="not valid-scenario"):
        service.run(SESSION_ID, manifest)
    assert repository.state.status is SessionStatus.READY


def test_session_check_records_engine_failure() -> None:
    manifest = ScenarioManifest.model_validate(minimal_manifest())
    repository = Sessions(ready_state())
    engine = Engine(CheckReport(()), error=RuntimeError("engine failure"))
    service = SessionCheckService(
        SessionCoordinator(repository),
        Machines(()),
        engine,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="engine failure"):
        service.run(SESSION_ID, manifest)
    assert repository.state.status is SessionStatus.FAILED
    assert repository.state.error == "engine failure"
