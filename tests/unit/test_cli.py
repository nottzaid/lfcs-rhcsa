from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml
from typer.testing import CliRunner, Result

from sysadmin_lab.adapters.sqlite_machines import SqliteSessionMachineRepository
from sysadmin_lab.adapters.sqlite_sessions import SqliteSessionRepository
from sysadmin_lab.application.checking import CheckReport, CheckResult
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.application.scenario_sessions import LearnerCheckReport, StartedScenario
from sysadmin_lab.application.verification import (
    PhaseResult,
    VerificationPhase,
    VerificationReport,
)
from sysadmin_lab.catalog import load_action_manifest, load_catalog
from sysadmin_lab.cli import app
from sysadmin_lab.domain.actions import ActionManifest
from sysadmin_lab.domain.images import ImageVerificationError
from sysadmin_lab.domain.models import PersistenceSpec, ScenarioManifest
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity
from tests.unit.test_image_building import kickstart_manifest
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


def test_session_check_fails_when_a_required_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRuntime(tmp_path)
    check = fake.manifest.checks[0]
    fake.checks.run = lambda _session_id, _manifest: CheckReport(
        (CheckResult(check, CheckObservation(check.check_id, False, "UID is 4301; expected 4201")),)
    )
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: fake)
    root = Path(__file__).parents[2]
    manifest = root / "scenarios" / f"{fake.manifest.scenario_id}.yaml"

    checked = runner.invoke(app, ["session", "check", str(SESSION_ID), str(manifest)])
    assert checked.exit_code == 1
    assert f"FAIL {check.check_id}: UID is 4301; expected 4201" in checked.stdout
    assert f"score: 0/{check.weight}" in checked.stdout


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


def test_scenario_start_launches_from_the_catalog_and_a_verified_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[2]
    payload = b"cloud image"
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(image_manifest(payload).model_dump(mode="json")), encoding="utf-8"
    )
    cache = tmp_path / "images"
    cache.mkdir()
    (cache / "source.iso").write_bytes(payload)
    fake = FakeRuntime(tmp_path)
    launched: list[tuple[ScenarioManifest, ActionManifest, dict[str, Path]]] = []

    def start(
        scenario: ScenarioManifest, setup: ActionManifest, images: dict[str, Path]
    ) -> StartedScenario:
        launched.append((scenario, setup, images))
        return fake.started

    fake.scenarios.start = start
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: fake)

    def launch(image_manifest_path: Path, image_cache: Path) -> Result:
        return runner.invoke(
            app,
            [
                "scenario",
                "start",
                "nfs-client-recovery",
                "--scenario-directory",
                str(root / "scenarios"),
                "--image-manifest-path",
                str(image_manifest_path),
                "--image-cache",
                str(image_cache),
            ],
        )

    assert launch(manifest_path, cache).exit_code == 0
    [(scenario, setup, images)] = launched
    assert scenario.scenario_id == "nfs-client-recovery"
    assert setup == load_action_manifest(root / "scenarios" / scenario.setup)
    assert images == {"rocky-10.2-test-v1": (cache / "source.iso").resolve()}

    # The lab's own image is built locally, and nothing is provisioned until it exists.
    unbuilt = launch(root / "images" / "rocky-10.2" / "iso-manifest.yaml", tmp_path / "empty")
    assert unbuilt.exit_code == 1
    assert "scenario start failed: built image is missing; run labctl image build" in (
        unbuilt.stderr
    )
    assert len(launched) == 1


@pytest.mark.parametrize("draft", [False, True])
def test_scenario_start_prints_the_brief_the_check_will_hold_the_learner_to(
    draft: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRuntime(tmp_path)
    root = Path(__file__).parents[2]
    scenario = next(
        manifest
        for manifest in load_catalog(root / "scenarios")
        if manifest.scenario_id == "nfs-client-recovery"
    )
    if draft:  # a draft may not state its requirements or prove persistence yet
        scenario = scenario.model_copy(
            update={"requirements": (), "persistence": PersistenceSpec()}
        )
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: fake)
    monkeypatch.setattr(
        "sysadmin_lab.cli._scenario_launch_inputs", lambda *_args: (scenario, None, {})
    )

    started = runner.invoke(app, ["scenario", "start", scenario.scenario_id])
    assert started.exit_code == 0
    brief = started.stdout.split(scenario.task.strip(), 1)[1]
    if draft:
        assert brief.strip() == ""
    else:
        assert "Done means:" in brief
        for requirement in scenario.requirements:
            assert f"  - {requirement}" in brief
        assert f"the check reboots {', '.join(scenario.reboot_hosts)} to prove" in brief


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
            PhaseResult(VerificationPhase.INITIAL, (), False, expected=False),
            PhaseResult(
                VerificationPhase.SOLVED,
                (),
                True,
                solution=scenario.reference_solution,
                expected=True,
            ),
            PhaseResult(VerificationPhase.RESET, (), False, expected=False),
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


def test_scenario_list_shows_what_the_state_store_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.db"
    left_behind = (
        SessionState.declared("nfs-client-recovery")
        .transition(SessionStatus.PROVISIONING)
        .transition(SessionStatus.READY)
    )
    finished = (
        SessionState.declared("local-account-repair")
        .transition(SessionStatus.PROVISIONING)
        .transition(SessionStatus.FAILED, error="boot timed out")
        .transition(SessionStatus.DESTROYING)
        .transition(SessionStatus.DESTROYED)
    )
    with (
        SqliteSessionRepository(state_path) as sessions,
        SqliteSessionMachineRepository(state_path) as machines,
    ):
        sessions.create(left_behind)
        sessions.create(finished)
        for host in ("node1", "node2"):
            identity = domain_identity("nfs-client-recovery", left_behind.session_id, host)
            machines.add(
                SessionMachine(
                    left_behind.session_id,
                    host,
                    identity,
                    "labadmin",
                    "secret",
                    (tmp_path / "key").resolve(),
                )
            )
        runtime = SimpleNamespace(sessions=sessions, machines=machines)
        monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: nullcontext(runtime))

        listed = runner.invoke(app, ["scenario", "list"])
        everything = runner.invoke(app, ["scenario", "list", "--all"])

    assert listed.exit_code == 0
    assert listed.stdout.split() == [
        str(left_behind.session_id),
        "ready",
        "nfs-client-recovery",
        "node1,node2",
    ]
    assert str(finished.session_id) in everything.stdout
    assert "destroyed" in everything.stdout


def test_scenario_list_says_when_there_is_nothing_to_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.db"
    with (
        SqliteSessionRepository(state_path) as sessions,
        SqliteSessionMachineRepository(state_path) as machines,
    ):
        runtime = SimpleNamespace(sessions=sessions, machines=machines)
        monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _path: nullcontext(runtime))
        listed = runner.invoke(app, ["scenario", "list", "--all"])
    assert listed.exit_code == 0
    assert listed.stdout == "no scenario sessions\n"


@pytest.mark.parametrize(
    ("command", "message", "code"),
    [
        (["session", "start", "{base}"], "session start failed", 1),
        (["session", "status", "{session}"], "session status failed", 1),
        (["session", "destroy", "{session}"], "session destroy failed", 1),
        (["session", "check", "{session}", "{manifest}"], "session check failed", 2),
        (["scenario", "list"], "scenario list failed", 1),
        (["scenario", "start", "local-account-repair"], "scenario start failed", 1),
        (["scenario", "check", "{session}"], "scenario check failed", 2),
        (["scenario", "status", "{session}"], "session status failed", 1),
        (["scenario", "reset", "{session}"], "scenario reset failed", 1),
        (["scenario", "destroy", "{session}"], "scenario destroy failed", 1),
        (["scenario", "verify", "local-account-repair"], "scenario verification failed", 2),
    ],
)
def test_every_command_explains_an_unreachable_lab(
    command: list[str],
    message: str,
    code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(_root: Path) -> object:
        raise ConnectionError("cannot connect to qemu:///system")

    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", unreachable)
    monkeypatch.setattr(
        "sysadmin_lab.cli._scenario_launch_inputs",
        lambda *_args: (None, None, {}),
    )
    base = tmp_path / "base.qcow2"
    base.touch()
    root = Path(__file__).parents[2]
    values = {
        "base": str(base),
        "session": str(SESSION_ID),
        "manifest": str(root / "scenarios" / "local-account-repair.yaml"),
    }
    result = runner.invoke(app, [part.format(**values) for part in command])
    assert result.exit_code == code
    assert f"{message}: cannot connect to qemu:///system" in result.stderr


def test_image_build_refuses_tampered_media_before_building_anything(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(kickstart_manifest(b"installer").model_dump(mode="json")),
        encoding="utf-8",
    )
    (tmp_path / "kickstart.ks").write_text("shutdown", encoding="utf-8")
    isos = tmp_path / "isos"
    isos.mkdir()
    (isos / "source.iso").write_bytes(b"tampered!")  # same length, different bytes

    refused = runner.invoke(
        app,
        [
            "image",
            "build",
            str(manifest),
            "--source-cache",
            str(isos),
            "--output-directory",
            str(tmp_path / "images"),
        ],
    )
    assert refused.exit_code == 1
    assert refused.stderr.startswith("image build failed: ")
    assert "mismatch" in refused.stderr
    assert not (tmp_path / "images").exists()


def test_image_fetch_reuses_a_verified_download_and_refuses_a_tampered_one(
    tmp_path: Path,
) -> None:
    payload = b"installation media"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(image_manifest(payload).model_dump(mode="json")), encoding="utf-8"
    )
    cache = tmp_path / "isos"
    cache.mkdir()
    (cache / "source.iso").write_bytes(payload)  # already downloaded: no network needed

    reused = runner.invoke(app, ["image", "fetch", str(manifest), str(cache)])
    assert reused.exit_code == 0
    assert reused.stdout.strip() == f"ready rocky-10.2-test-v1: {cache / 'source.iso'}"

    (cache / "source.iso").write_bytes(b"tampered")
    refused = runner.invoke(app, ["image", "fetch", str(manifest), str(cache)])
    assert refused.exit_code == 1
    assert "mismatch" in refused.stderr
    assert (cache / "source.iso").read_bytes() == b"tampered"  # never overwritten


def test_verify_prints_each_phase_and_the_checks_behind_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sysadmin_lab.application.verification import (
        PhaseResult,
        VerificationPhase,
        VerificationReport,
    )

    def seen(passed: bool, error: bool = False, message: str = "") -> tuple[CheckObservation]:
        return (CheckObservation("site-serving", passed, message, error),)

    phases = (
        PhaseResult(VerificationPhase.INITIAL, seen(False), False, expected=False),
        PhaseResult(VerificationPhase.SOLVED, seen(True), True, "solution.yaml", expected=True),
        PhaseResult(VerificationPhase.RESET, seen(True), True, expected=False),
        PhaseResult(
            VerificationPhase.REJECTED, seen(True), True, "rejected-chcon.yaml", expected=None
        ),
        PhaseResult(
            VerificationPhase.ALTERNATE_SOLVED,
            seen(False, message="nginx is not running"),
            False,
            "alternate.yaml",
            expected=True,
        ),
        PhaseResult(
            VerificationPhase.ALTERNATE_REBOOTED,
            seen(False, error=True, message="process exceeded 10 second timeout"),
            False,
            "alternate.yaml",
            expected=True,
        ),
    )

    class CannedVerifier:
        def __init__(self, _driver: object) -> None:
            pass

        def verify(self, manifest: object) -> VerificationReport:
            return VerificationReport("selinux-confined-web-service", phases)

    monkeypatch.setattr("sysadmin_lab.cli.ScenarioVerifier", CannedVerifier)
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _root: FakeRuntime(tmp_path))
    monkeypatch.setattr("sysadmin_lab.cli._scenario_launch_inputs", lambda *_args: (None, None, {}))

    result = runner.invoke(app, ["scenario", "verify", "selinux-confined-web-service"])
    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        "PASS initial: checks fail as designed",
        "PASS solved (solution.yaml): checks pass",
        "FAIL reset: checks should fail but pass",
        "PASS rejected (rejected-chcon.yaml): live state passes; the reboot must expose it",
        "FAIL alternate-solved (alternate.yaml): checks should pass but fail",
        "  CHECK site-serving: nginx is not running",
        "FAIL alternate-rebooted (alternate.yaml): a check errored",
        "  ERROR site-serving: process exceeded 10 second timeout",
    ]


@pytest.mark.parametrize(
    ("live_passes", "extra", "expected_lines"),
    [
        (True, {"unreachable_host": "node2"}, ["node2 did not come back over SSH"]),
        (True, {}, ["persistence not proven: rerun without --skip-reboot to reboot node2"]),
        (False, {}, ["once the live state passes, the check reboots node2 to prove persistence"]),
        (True, {"after_reboot": "failing"}, ["after rebooting node2:", "not solved yet"]),
    ],
)
def test_scenario_check_says_what_the_reboot_proved(
    live_passes: bool,
    extra: dict[str, object],
    expected_lines: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRuntime(tmp_path)
    check = fake.manifest.checks[0]

    def report(passed: bool) -> CheckReport:
        return CheckReport((CheckResult(check, CheckObservation(check.check_id, passed, "x")),))

    if extra.get("after_reboot") == "failing":
        extra = {"after_reboot": report(False)}
    learner = LearnerCheckReport(report(live_passes), ("node2",), **extra)  # type: ignore[arg-type]
    fake.scenarios.check = lambda *_args, **_kwargs: learner
    monkeypatch.setattr("sysadmin_lab.cli.open_vm_runtime", lambda _root: fake)
    monkeypatch.setattr("sysadmin_lab.cli.find_scenario", lambda *_args: fake.manifest)

    result = runner.invoke(app, ["scenario", "check", str(SESSION_ID)])
    assert result.exit_code == 1
    for line in expected_lines:
        assert any(output.startswith(line) for output in result.stdout.splitlines()), line


def test_up_refuses_to_listen_beyond_loopback_or_without_a_project(tmp_path: Path) -> None:
    exposed = runner.invoke(app, ["up", "--host", "0.0.0.0", "--no-browser"])
    assert exposed.exit_code == 2
    assert "only binds to a loopback address" in exposed.stderr

    empty = runner.invoke(app, ["up", "--no-browser", "--project-root", str(tmp_path)])
    assert empty.exit_code == 2
    assert "project root is missing required paths" in empty.stderr


def test_up_refuses_a_port_something_else_already_holds() -> None:
    root = Path(__file__).parents[2]
    with socket(AF_INET, SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        busy = runner.invoke(
            app, ["up", "--no-browser", "--port", str(port), "--project-root", str(root)]
        )
    assert busy.exit_code == 2
    assert f"loopback port {port} is already in use" in busy.stderr


def test_first_launch_builds_the_missing_image_then_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[2]
    project = tmp_path / "lab"
    project.mkdir()
    for shared in ("scenarios", "images", "curricula", "mock-exams"):
        (project / shared).symlink_to(root / shared)
    built = tmp_path / "built.qcow2"
    acquired: list[Path] = []
    served: list[tuple[str, int]] = []

    class StandInBuilder:  # the real build is a twenty-minute install; tests/live builds it
        def __init__(self, _runner: object) -> None:
            pass

        def build(self, _manifest: object, _path: Path, source: Path, _out: Path) -> object:
            acquired.append(source)
            return SimpleNamespace(artifact=built)

    monkeypatch.setattr("sysadmin_lab.cli.KickstartImageBuilder", StandInBuilder)
    monkeypatch.setattr(
        "sysadmin_lab.cli.ImageAcquirer.acquire",
        lambda _self, _manifest, cache: cache / "rocky.iso",
    )
    monkeypatch.setattr(
        "uvicorn.run", lambda _app, host, port, log_level: served.append((host, port))
    )
    result = runner.invoke(
        app,
        ["up", "--host", "::1", "--port", "8791", "--no-browser", "--project-root", str(project)],
    )
    assert result.exit_code == 0, result.stderr
    assert "not present; building it now" in result.stdout
    assert acquired == [project.resolve() / "runtime" / "cache" / "isos" / "rocky.iso"]
    assert f"Verified lab image: {built}" in result.stdout
    assert "Linux Admin Lab: http://[::1]:8791/scenarios/topic/lfcs" in result.stdout
    assert served == [("::1", 8791)]


def test_first_launch_stops_before_serving_when_the_iso_fails_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[2]
    project = tmp_path / "lab"
    project.mkdir()
    for shared in ("scenarios", "images"):
        (project / shared).symlink_to(root / shared)

    def tampered(_self: object, _manifest: object, _cache: Path) -> Path:
        raise ImageVerificationError("sha256 mismatch for Rocky-10.2-x86_64-dvd1.iso")

    monkeypatch.setattr("sysadmin_lab.cli.ImageAcquirer.acquire", tampered)
    monkeypatch.setattr("uvicorn.run", lambda *_args, **_kwargs: pytest.fail("served"))
    result = runner.invoke(
        app, ["up", "--port", "8792", "--no-browser", "--project-root", str(project)]
    )
    assert result.exit_code == 1
    assert result.stderr == (
        "lab image preparation failed: sha256 mismatch for Rocky-10.2-x86_64-dvd1.iso\n"
    )
    assert "Verified lab image" not in result.stdout


def test_up_opens_the_catalog_in_a_browser_once_the_site_is_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[2]
    built = tmp_path / "built.qcow2"
    monkeypatch.setattr(
        "sysadmin_lab.cli.resolve_built_image",
        lambda *_args: SimpleNamespace(artifact=built),
    )
    opened: list[str] = []
    browser_opened = Event()

    def open_in_browser(url: str) -> bool:
        opened.append(url)
        browser_opened.set()
        return True

    def serve(*_args: object, **_kwargs: object) -> None:  # serves until the browser opens
        assert browser_opened.wait(timeout=10)

    monkeypatch.setattr("sysadmin_lab.cli.webbrowser.open", open_in_browser)
    monkeypatch.setattr("uvicorn.run", serve)
    result = runner.invoke(app, ["up", "--port", "8793", "--project-root", str(root)])
    assert result.exit_code == 0, result.stderr
    assert opened == ["http://127.0.0.1:8793/scenarios/topic/lfcs"]
