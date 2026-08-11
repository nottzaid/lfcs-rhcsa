from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml
from typer.testing import CliRunner

from sysadmin_lab.application.checking import CheckReport, CheckResult
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.catalog import load_catalog
from sysadmin_lab.cli import app
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity
from tests.unit.test_images import image_manifest

runner = CliRunner()
SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


def test_catalog_validate_reports_manifest_count() -> None:
    root = Path(__file__).parents[2]
    result = runner.invoke(app, ["catalog", "validate", str(root / "examples" / "scenarios")])
    assert result.exit_code == 0
    assert "validated 1 scenario manifest(s)" in result.stdout


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
        manifest = load_catalog(root / "examples" / "scenarios")[0]
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
            str(root / "examples" / "scenarios" / "selinux-web-port.yaml"),
        ],
    )
    assert checked.exit_code == 0
    assert f"PASS {fake.check_id}: matched" in checked.stdout
