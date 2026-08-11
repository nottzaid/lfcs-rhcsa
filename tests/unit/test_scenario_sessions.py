from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.application.actions import ActionExecutionError
from sysadmin_lab.application.checking import CheckReport, CheckResult
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.application.scenario_sessions import (
    ScenarioLaunchError,
    ScenarioSessionService,
)
from sysadmin_lab.application.session_artifacts import GuestAccess
from sysadmin_lab.application.vm_sessions import ProvisionedSession
from sysadmin_lab.domain.actions import ActionManifest
from sysadmin_lab.domain.models import ScenarioManifest, ScenarioStatus, TopologySpec
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity
from tests.unit.test_models import minimal_manifest

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


def manifest(*, status: ScenarioStatus = ScenarioStatus.VERIFIED) -> ScenarioManifest:
    raw = minimal_manifest()
    raw["status"] = status.value
    raw["topology"] = {"hosts": [{"name": "node1", "image": "rocky-base", "memory_mib": 1024}]}
    return ScenarioManifest.model_validate(raw)


def setup() -> ActionManifest:
    return ActionManifest.model_validate(
        {"actions": [{"action_id": "break-state", "target": "node1", "arguments": ["true"]}]}
    )


def report(spec: ScenarioManifest, *, passed: bool, error: bool = False) -> CheckReport:
    return CheckReport(
        (
            CheckResult(
                spec.checks[0],
                CheckObservation(spec.checks[0].check_id, passed, "state", error),
            ),
        )
    )


def ready_state() -> SessionState:
    return (
        SessionState.declared("valid-scenario", SESSION_ID)
        .transition(SessionStatus.PROVISIONING)
        .transition(SessionStatus.READY)
    )


def machine(tmp_path: Path, *, address: str | None = "192.0.2.10") -> SessionMachine:
    access = GuestAccess("labadmin", "secret", (tmp_path / "key").resolve(), tmp_path / "key.pub")
    return SessionMachine(
        SESSION_ID,
        "node1",
        domain_identity("valid-scenario", SESSION_ID, "node1"),
        access.username,
        access.password,
        access.private_key,
        address,
    )


@dataclass
class Sessions:
    state: SessionState = field(default_factory=ready_state)

    def get(self, session_id: UUID) -> SessionState:
        return self.state


@dataclass
class Machines:
    value: SessionMachine

    def list(self, session_id: UUID) -> tuple[SessionMachine, ...]:
        return (self.value,)


@dataclass
class VmSessions:
    provisioned: ProvisionedSession
    destroyed: list[UUID] = field(default_factory=list)

    def provision(self, **_kwargs: object) -> ProvisionedSession:
        return self.provisioned

    def destroy(self, session_id: UUID) -> SessionState:
        self.destroyed.append(session_id)
        return self.provisioned.state.transition(SessionStatus.DESTROYING).transition(
            SessionStatus.DESTROYED
        )


@dataclass
class Checks:
    reports: list[CheckReport]

    def run(self, session_id: UUID, spec: ScenarioManifest) -> CheckReport:
        return self.reports.pop(0)


@dataclass
class Actions:
    failure: Exception | None = None
    calls: int = 0

    def run(self, action_manifest: ActionManifest, endpoints: dict) -> tuple:
        self.calls += 1
        if self.failure:
            raise self.failure
        return ()


def service(
    tmp_path: Path,
    reports: list[CheckReport],
    *,
    address: str | None = "192.0.2.10",
    action_failure: Exception | None = None,
) -> tuple[ScenarioSessionService, VmSessions, Actions]:
    state = ready_state()
    host = machine(tmp_path, address=address)
    vm = VmSessions(ProvisionedSession(state, host))
    actions = Actions(action_failure)
    result = ScenarioSessionService(
        sessions=Sessions(state),  # type: ignore[arg-type]
        machines=Machines(host),  # type: ignore[arg-type]
        vm_sessions=vm,  # type: ignore[arg-type]
        checks=Checks(reports),  # type: ignore[arg-type]
        actions=actions,  # type: ignore[arg-type]
    )
    return result, vm, actions


def test_start_applies_setup_and_requires_initial_failure(tmp_path: Path) -> None:
    spec = manifest()
    scenario, vm, actions = service(tmp_path, [report(spec, passed=False)])
    base = tmp_path / "base.qcow2"
    started = scenario.start(spec, setup(), {"rocky-base": base})
    assert started.provisioned.state.status is SessionStatus.READY
    assert not started.initial_report.required_passed
    assert actions.calls == 1
    assert vm.destroyed == []


@pytest.mark.parametrize("checker_error,already_passed", [(True, False), (False, True)])
def test_start_cleans_scenario_that_is_invalid_at_initial_check(
    tmp_path: Path, checker_error: bool, already_passed: bool
) -> None:
    spec = manifest()
    scenario, vm, _actions = service(
        tmp_path, [report(spec, passed=already_passed, error=checker_error)]
    )
    with pytest.raises(ScenarioLaunchError):
        scenario.start(spec, setup(), {"rocky-base": tmp_path / "base.qcow2"})
    assert vm.destroyed == [SESSION_ID]


def test_start_cleans_setup_failure_and_missing_address(tmp_path: Path) -> None:
    spec = manifest()
    scenario, vm, _actions = service(
        tmp_path,
        [report(spec, passed=False)],
        action_failure=ActionExecutionError("setup failed"),
    )
    with pytest.raises(ActionExecutionError, match="setup failed"):
        scenario.start(spec, setup(), {"rocky-base": tmp_path / "base.qcow2"})
    assert vm.destroyed == [SESSION_ID]

    scenario, vm, _actions = service(tmp_path, [report(spec, passed=False)], address=None)
    with pytest.raises(ScenarioLaunchError, match="no reachable address"):
        scenario.start(spec, setup(), {"rocky-base": tmp_path / "base.qcow2"})
    assert vm.destroyed == [SESSION_ID]


def test_start_rejects_draft_unsupported_topology_and_missing_image(tmp_path: Path) -> None:
    verified = manifest()
    scenario, _vm, _actions = service(tmp_path, [report(verified, passed=False)])
    with pytest.raises(ScenarioLaunchError, match="not verified"):
        scenario.start(manifest(status=ScenarioStatus.DRAFT), setup(), {})
    with pytest.raises(ScenarioLaunchError, match="no verified base image"):
        scenario.start(verified, setup(), {})

    two_hosts = verified.model_copy(
        update={
            "topology": TopologySpec(
                hosts=(
                    verified.topology.hosts[0],
                    verified.topology.hosts[0].model_copy(update={"name": "node2"}),
                )
            )
        }
    )
    with pytest.raises(ScenarioLaunchError, match="one-host"):
        scenario.start(two_hosts, setup(), {})


def test_check_reset_and_destroy_delegate_with_scenario_identity(tmp_path: Path) -> None:
    spec = manifest()
    expected = report(spec, passed=False)
    scenario, vm, _actions = service(tmp_path, [expected, expected])
    assert scenario.check(SESSION_ID, spec) == expected
    reset = scenario.reset(SESSION_ID, spec, setup(), {"rocky-base": tmp_path / "base.qcow2"})
    assert reset.initial_report == expected
    assert vm.destroyed == [SESSION_ID]
    scenario.destroy(SESSION_ID)
    assert vm.destroyed == [SESSION_ID, SESSION_ID]

    wrong = spec.model_copy(update={"scenario_id": "wrong-scenario"})
    with pytest.raises(ScenarioLaunchError, match="session runs"):
        scenario.reset(SESSION_ID, wrong, setup(), {})
