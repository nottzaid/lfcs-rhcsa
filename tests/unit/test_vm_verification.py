from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from sysadmin_lab.application.checking import CheckReport, CheckResult
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.application.vm_verification import VmScenarioDriver
from sysadmin_lab.domain.models import ScenarioManifest
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity
from tests.unit.test_models import minimal_manifest

SESSION_ID = UUID("20000000-0000-0000-0000-000000000001")
RESET_ID = UUID("20000000-0000-0000-0000-000000000002")


def _write_action(path: Path, action_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "actions:\n"
        f"  - action_id: {action_id}\n"
        "    target: node1\n"
        '    arguments: ["true"]\n',
        encoding="utf-8",
    )


def test_vm_driver_connects_every_acceptance_operation(tmp_path: Path) -> None:
    raw = minimal_manifest()
    raw["topology"] = {"hosts": [{"name": "node1", "image": "rocky-base"}]}
    manifest = ScenarioManifest.model_validate(raw)
    scenario_directory = tmp_path / "scenarios"
    _write_action(scenario_directory / manifest.setup, "setup")
    _write_action(scenario_directory / manifest.reference_solution, "solve")

    ready = SessionState(SESSION_ID, manifest.scenario_id, SessionStatus.READY, 0, 2)
    reset_ready = SessionState(RESET_ID, manifest.scenario_id, SessionStatus.READY, 0, 2)
    machine = SessionMachine(
        SESSION_ID,
        "node1",
        domain_identity(manifest.scenario_id, SESSION_ID, "node1"),
        "labadmin",
        "password",
        tmp_path / "id_ed25519",
        "192.0.2.20",
    )
    observation = CheckObservation(manifest.checks[0].check_id, True, "matched")
    report = CheckReport((CheckResult(manifest.checks[0], observation),))
    scenarios = Mock()
    scenarios.start.return_value = SimpleNamespace(
        provisioned=SimpleNamespace(state=ready, machine=machine)
    )
    scenarios.reset.return_value = SimpleNamespace(
        provisioned=SimpleNamespace(state=reset_ready, machine=machine)
    )
    sessions = Mock()
    sessions.get.return_value = ready
    machines = Mock()
    machines.list.return_value = (machine,)
    vm_sessions = Mock()
    checks = Mock()
    checks.run.return_value = report
    actions = Mock()
    base_images = {"rocky-base": tmp_path / "base.qcow2"}
    driver = VmScenarioDriver(
        scenario_directory=scenario_directory,
        base_images=base_images,
        sessions=sessions,
        machines=machines,
        vm_sessions=vm_sessions,
        checks=checks,
        scenarios=scenarios,
        actions=actions,
    )

    session = driver.provision(manifest)
    assert session.session_id == str(SESSION_ID)
    assert driver.run_checks(session, manifest) == (observation,)
    driver.apply_solution(session, manifest, manifest.reference_solution)
    endpoints = actions.run.call_args.args[1]
    assert endpoints["node1"].host == "192.0.2.20"
    driver.reboot(session, ("node1",))
    reset = driver.reset(session, manifest)
    assert reset.session_id == str(RESET_ID)
    driver.destroy(session)

    scenarios.start.assert_called_once_with(
        manifest,
        scenarios.start.call_args.args[1],
        base_images,
        require_verified=False,
    )
    scenarios.reset.assert_called_once_with(
        SESSION_ID,
        manifest,
        scenarios.reset.call_args.args[2],
        base_images,
        require_verified=False,
    )
    vm_sessions.reboot.assert_called_once_with(SESSION_ID, ("node1",))
    vm_sessions.destroy.assert_called_once_with(SESSION_ID)


def test_vm_driver_does_not_redestroy_terminal_session(tmp_path: Path) -> None:
    sessions = Mock()
    sessions.get.return_value = SessionState(
        SESSION_ID, "valid-scenario", SessionStatus.DESTROYED, 0, 4
    )
    vm_sessions = Mock()
    driver = VmScenarioDriver(
        scenario_directory=tmp_path,
        base_images={},
        sessions=sessions,
        machines=Mock(),
        vm_sessions=vm_sessions,
        checks=Mock(),
        scenarios=Mock(),
        actions=Mock(),
    )

    driver.destroy(SimpleNamespace(session_id=str(SESSION_ID), scenario_id="valid-scenario"))

    vm_sessions.destroy.assert_not_called()


def test_vm_driver_rejects_machine_without_address(tmp_path: Path) -> None:
    manifest = ScenarioManifest.model_validate(minimal_manifest())
    _write_action(tmp_path / manifest.reference_solution, "solve")
    machine = SessionMachine(
        SESSION_ID,
        "node1",
        domain_identity(manifest.scenario_id, SESSION_ID, "node1"),
        "labadmin",
        "password",
        tmp_path / "key",
    )
    machines = Mock()
    machines.list.return_value = (machine,)
    driver = VmScenarioDriver(
        scenario_directory=tmp_path,
        base_images={},
        sessions=Mock(),
        machines=machines,
        vm_sessions=Mock(),
        checks=Mock(),
        scenarios=Mock(),
        actions=Mock(),
    )

    with pytest.raises(RuntimeError, match="no reachable address"):
        driver.apply_solution(
            SimpleNamespace(session_id=str(SESSION_ID), scenario_id=manifest.scenario_id),
            manifest,
            manifest.reference_solution,
        )
