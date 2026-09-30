from __future__ import annotations

from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml
from typer.testing import CliRunner

from sysadmin_lab.application.checking import CheckReport, CheckResult
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.application.scenario_sessions import LearnerCheckReport, StartedScenario
from sysadmin_lab.application.verification import (
    PhaseResult,
    VerificationPhase,
    VerificationReport,
)
from sysadmin_lab.catalog import load_catalog
from sysadmin_lab.cli import app
from sysadmin_lab.domain.actions import ActionManifest
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity
from tests.unit.test_images import image_manifest

runner = CliRunner()
SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


def test_catalog_validate_reports_manifest_count() -> None:
    scenarios = Path(__file__).parents[2] / "scenarios"
    result = runner.invoke(app, ["catalog", "validate", str(scenarios)])
    assert result.exit_code == 0
    count = len(list(scenarios.glob("*.yaml")))
    assert f"validated {count} scenario manifest(s)" in result.stdout


def test_catalog_validate_reports_contract_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["catalog", "validate", str(tmp_path)])
    assert result.exit_code == 1
    assert "no scenario manifests found" in result.stderr


def test_image_source_verification_command(tmp_path: Path) -> None:
    payload = b"installation media"
    source = tmp_path / "source.iso"
    source.write_bytes(payload)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(image_manifest(payload).model_dump(mode="json")), encoding="utf-8"
    )
    result = runner.invoke(app, ["image", "verify-source", str(manifest), str(source)])
    assert result.exit_code == 0
    assert "verified rocky-10.2-test-v1" in result.stdout


def test_image_source_verification_command_reports_failure(tmp_path: Path) -> None:
    payload = b"installation media"
    source = tmp_path / "source.iso"
    source.write_bytes(b"tampered")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(image_manifest(payload).model_dump(mode="json")), encoding="utf-8"
    )
    result = runner.invoke(app, ["image", "verify-source", str(manifest), str(source)])
    assert result.exit_code == 1
    assert "mismatch" in result.stderr


class FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        identity = domain_identity("infrastructure-smoke", SESSION_ID, "node1")
        self.machine = SessionMachine(
            SESSION_ID,
            "node1",
            identity,
            "labadmin",
            "secret",
            (tmp_path / "key").resolve(),
            "192.0.2.10",
        )
        self.ready = SessionState(SESSION_ID, "infrastructure-smoke", SessionStatus.READY, 0, 2)
        self.vm_sessions = SimpleNamespace(
            provision=lambda **_kwargs: SimpleNamespace(state=self.ready, machine=self.machine),
            destroy=lambda _session_id: self.ready.transition(SessionStatus.DESTROYING).transition(
                SessionStatus.DESTROYED
            ),
        )
        self.sessions = SimpleNamespace(get=lambda _session_id: self.ready)
        self.machines = SimpleNamespace(list=lambda _session_id: (self.machine,))
        root = Path(__file__).parents[2]
        manifest = load_catalog(root / "scenarios")[0]
        self.manifest = manifest
        self.check_id = manifest.checks[0].check_id
        self.checks = SimpleNamespace(
            run=lambda _session_id, _manifest: CheckReport(
                (
                    CheckResult(
                        manifest.checks[0],
                        CheckObservation(manifest.checks[0].check_id, True, "matched"),
                    ),
                )
            )
        )
        self.report = self.checks.run(SESSION_ID, manifest)
        self.started = StartedScenario(
            SimpleNamespace(state=self.ready, machines=(self.machine,)),  # type: ignore[arg-type]
            self.report,
        )
        destroyed_state = self.ready.transition(SessionStatus.DESTROYING).transition(
            SessionStatus.DESTROYED
        )
        self.scenarios = SimpleNamespace(
            start=lambda _manifest, _setup, _images: self.started,
            check=lambda _session_id, _manifest, **_options: LearnerCheckReport(self.report),
            reset=lambda _session_id, _manifest, _setup, _images: self.started,
            destroy=lambda _session_id: destroyed_state,
        )
        self.actions = SimpleNamespace()

    def __enter__(self) -> FakeRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


def test_session_commands_use_shared_runtime_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRuntime(tmp_path)
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: fake)
    base = tmp_path / "base.qcow2"
    base.touch()

    started = runner.invoke(app, ["session", "start", str(base)])
    assert started.exit_code == 0
    assert f"session: {SESSION_ID}" in started.stdout
    assert "domain: lal-infrastructure-smoke-10000000-node1" in started.stdout
    assert "console password: secret" in started.stdout

    status = runner.invoke(app, ["session", "status", str(SESSION_ID)])
    assert status.exit_code == 0
    assert "status: ready" in status.stdout
    assert "machine: node1" in status.stdout

    destroyed = runner.invoke(app, ["session", "destroy", str(SESSION_ID)])
    assert destroyed.exit_code == 0
    assert "destroyed" in destroyed.stdout

    root = Path(__file__).parents[2]
    checked = runner.invoke(
        app,
        [
            "session",
            "check",
            str(SESSION_ID),
            str(root / "scenarios" / f"{fake.manifest.scenario_id}.yaml"),
        ],
    )
    assert checked.exit_code == 0
    assert f"PASS {fake.check_id}: matched" in checked.stdout


def test_public_scenario_commands_use_learner_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRuntime(tmp_path)
    root = Path(__file__).parents[2]
    scenario = load_catalog(root / "scenarios")[0]
    setup = ActionManifest.model_validate(
        {"actions": [{"action_id": "setup", "target": "node1", "arguments": ["true"]}]}
    )
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: fake)
    monkeypatch.setattr("sysadmin_lab.cli.find_scenario", lambda _path, _id: scenario)
    monkeypatch.setattr(
        "sysadmin_lab.cli._scenario_launch_inputs",
        lambda *_args: (scenario, setup, {"rocky-10.2-base-v1": tmp_path / "base.qcow2"}),
    )

    started = runner.invoke(app, ["scenario", "start", scenario.scenario_id])
    assert started.exit_code == 0
    assert f"session: {SESSION_ID}" in started.stdout
    assert "node1:\n  domain: lal-" in started.stdout
    assert scenario.task.strip() in started.stdout

    checked = runner.invoke(app, ["scenario", "check", str(SESSION_ID)])
    assert checked.exit_code == 0
    assert "score: 1/1" in checked.stdout
    assert "solved" in checked.stdout

    status = runner.invoke(app, ["scenario", "status", str(SESSION_ID)])
    assert status.exit_code == 0
    assert "status: ready" in status.stdout

    reset = runner.invoke(app, ["scenario", "reset", str(SESSION_ID)])
    assert reset.exit_code == 0
    assert f"replaced session: {SESSION_ID}" in reset.stdout

    destroyed = runner.invoke(app, ["scenario", "destroy", str(SESSION_ID)])
    assert destroyed.exit_code == 0
    assert "destroyed" in destroyed.stdout


def test_scenario_verify_reports_acceptance_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRuntime(tmp_path)
    root = Path(__file__).parents[2]
    scenario = load_catalog(root / "scenarios")[0]
    setup = ActionManifest.model_validate(
        {"actions": [{"action_id": "setup", "target": "node1", "arguments": ["true"]}]}
    )
    report = VerificationReport(
        scenario.scenario_id,
        (
            PhaseResult(VerificationPhase.INITIAL, (), False),
            PhaseResult(
                VerificationPhase.SOLVED,
                (),
                True,
                solution=scenario.reference_solution,
            ),
            PhaseResult(VerificationPhase.RESET, (), False),
        ),
    )
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: fake)
    monkeypatch.setattr(
        "sysadmin_lab.cli._scenario_launch_inputs",
        lambda *_args: (scenario, setup, {"rocky-10.2-base-v1": tmp_path / "base.qcow2"}),
    )
    monkeypatch.setattr("sysadmin_lab.cli.VmScenarioDriver", lambda **_kwargs: object())
    monkeypatch.setattr(
        "sysadmin_lab.cli.ScenarioVerifier",
        lambda _driver: SimpleNamespace(verify=lambda _manifest: report),
    )

    result = runner.invoke(app, ["scenario", "verify", scenario.scenario_id])

    assert result.exit_code == 0
    assert "PASS initial: checks fail as designed" in result.stdout
    assert "PASS solved" in result.stdout
    assert f"verified acceptance contract: {scenario.scenario_id}" in result.stdout


def test_up_rejects_non_loopback_and_busy_port() -> None:
    non_loopback = runner.invoke(app, ["up", "--host", "0.0.0.0", "--no-browser"])
    assert non_loopback.exit_code == 2
    assert "only binds to a loopback" in non_loopback.stderr

    with socket(AF_INET, SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        busy = runner.invoke(app, ["up", "--port", str(port), "--no-browser"])
    assert busy.exit_code == 2
    assert f"port {port} is already in use" in busy.stderr


@pytest.mark.parametrize(
    ("after_reboot", "unreachable", "expected"),
    [
        (True, None, "after rebooting node2:"),
        (False, "node2", "node2 did not come back over SSH after rebooting"),
        (False, None, "persistence not proven: rerun without --skip-reboot"),
    ],
)
def test_scenario_check_reports_the_persistence_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_reboot: bool,
    unreachable: str | None,
    expected: str,
) -> None:
    fake = FakeRuntime(tmp_path)
    learner = LearnerCheckReport(
        fake.report,
        ("node2",),
        fake.report if after_reboot else None,
        unreachable,
    )
    fake.scenarios.check = lambda _session_id, _manifest, **_options: learner
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: fake)
    monkeypatch.setattr("sysadmin_lab.cli.find_scenario", lambda _path, _id: fake.manifest)

    checked = runner.invoke(app, ["scenario", "check", str(SESSION_ID)])

    assert expected in checked.stdout
    assert checked.exit_code == (0 if after_reboot else 1)
