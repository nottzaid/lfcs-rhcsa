from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from sysadmin_lab.application.checking import CheckReport, CheckResult
from sysadmin_lab.application.lab_workspace import ScenarioSessionSnapshot
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.application.scenario_sessions import StartedScenario
from sysadmin_lab.catalog import load_catalog
from sysadmin_lab.domain.models import ScenarioStatus
from sysadmin_lab.domain.progress import ScenarioProgress
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity
from sysadmin_lab.web import create_app

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")
RESET_ID = UUID("20000000-0000-0000-0000-000000000002")


class FakeWorkspace:
    def __init__(self, tmp_path: Path) -> None:
        root = Path(__file__).parents[2]
        self.manifest = next(
            scenario
            for scenario in load_catalog(root / "scenarios")
            if scenario.status is ScenarioStatus.VERIFIED
        )
        self.state = (
            SessionState.declared(self.manifest.scenario_id, SESSION_ID)
            .transition(SessionStatus.PROVISIONING)
            .transition(SessionStatus.READY)
        )
        self.machine = SessionMachine(
            SESSION_ID,
            "node1",
            domain_identity(self.manifest.scenario_id, SESSION_ID, "node1"),
            "labadmin",
            "secret",
            (tmp_path / "id_ed25519").resolve(),
            "192.0.2.10",
        )
        self.snapshot = ScenarioSessionSnapshot(self.state, (self.machine,))
        self.report = CheckReport(
            (
                CheckResult(
                    self.manifest.checks[0],
                    CheckObservation(self.manifest.checks[0].check_id, True, "matched"),
                ),
            )
        )
        self.progress_value: tuple[ScenarioProgress, ...] = ()

    def scenarios(self) -> tuple:
        return (self.manifest,)

    def scenario(self, scenario_id: str):
        if scenario_id != self.manifest.scenario_id:
            raise LookupError("scenario does not exist")
        return self.manifest

    def mock_exams(self) -> tuple:
        return ()

    def mock_exam(self, _mock_id: str):
        raise LookupError("mock exam does not exist")

    def sessions(self, *, limit: int = 100) -> tuple[ScenarioSessionSnapshot, ...]:
        return (self.snapshot,)[:limit]

    def session(self, session_id: UUID) -> ScenarioSessionSnapshot:
        if session_id != SESSION_ID:
            raise LookupError("session does not exist")
        return self.snapshot

    def progress(self) -> tuple[ScenarioProgress, ...]:
        return self.progress_value

    def start(self, scenario_id: str) -> StartedScenario:
        self.scenario(scenario_id)
        return StartedScenario(
            SimpleNamespace(state=self.state, machine=self.machine),  # type: ignore[arg-type]
            self.report,
        )

    def check(self, session_id: UUID) -> CheckReport:
        self.session(session_id)
        return self.report

    def reset(self, session_id: UUID) -> StartedScenario:
        self.session(session_id)
        reset_state = (
            SessionState.declared(self.manifest.scenario_id, RESET_ID)
            .transition(SessionStatus.PROVISIONING)
            .transition(SessionStatus.READY)
        )
        return StartedScenario(
            SimpleNamespace(state=reset_state, machine=self.machine),  # type: ignore[arg-type]
            self.report,
        )

    def destroy(self, session_id: UUID) -> SessionState:
        self.session(session_id)
        return self.state.transition(SessionStatus.DESTROYING).transition(SessionStatus.DESTROYED)


def wait_for_job(client: TestClient, job_id: str) -> dict:
    for _attempt in range(100):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
    raise AssertionError("job did not finish")


def test_lfcs_topic_and_scenario_pages_expose_hands_on_loop(tmp_path: Path) -> None:
    workspace = FakeWorkspace(tmp_path)
    with TestClient(create_app(workspace=workspace)) as client:  # type: ignore[arg-type]
        redirect = client.get("/", follow_redirects=False)
        assert redirect.headers["location"] == "/scenarios/topic/lfcs"

        catalog = client.get("/scenarios/topic/lfcs")
        assert catalog.status_code == 200
        assert "LFCS" in catalog.text
        assert workspace.manifest.title in catalog.text
        assert f"{workspace.manifest.estimated_minutes} min" in catalog.text
        assert "Times are advisory estimates" in catalog.text
        assert "No limit" not in catalog.text
        assert "Info" in catalog.text and "Run" in catalog.text
        assert "Active" in catalog.text
        assert catalog.headers["cache-control"] == "no-store"

        detail = client.get(f"/scenarios/{workspace.manifest.scenario_id}")
        assert workspace.manifest.task.split(".", maxsplit=1)[0] in detail.text
        assert "Launch scenario" in detail.text
        assert workspace.manifest.topology.hosts[0].name in detail.text


def test_session_page_and_api_show_connection_and_state_grading(tmp_path: Path) -> None:
    workspace = FakeWorkspace(tmp_path)
    with TestClient(create_app(workspace=workspace)) as client:  # type: ignore[arg-type]
        page = client.get(f"/sessions/{SESSION_ID}")
        assert page.status_code == 200
        assert "Check solution" in page.text
        assert "192.0.2.10" in page.text
        assert "secret" in page.text
        assert "virt-manager" in page.text

        forbidden = client.post(f"/api/sessions/{SESSION_ID}/check")
        assert forbidden.status_code == 403

        accepted = client.post(
            f"/api/sessions/{SESSION_ID}/check", headers={"X-Lab-Request": "browser"}
        )
        assert accepted.status_code == 202
        completed = wait_for_job(client, accepted.json()["job_id"])
        assert completed["status"] == "succeeded"
        assert completed["result"]["required_passed"] is True
        assert completed["result"]["results"][0]["message"] == "matched"


def test_start_reset_destroy_jobs_return_browser_destinations(tmp_path: Path) -> None:
    workspace = FakeWorkspace(tmp_path)
    headers = {"X-Lab-Request": "browser"}
    with TestClient(create_app(workspace=workspace)) as client:  # type: ignore[arg-type]
        started = client.post(
            f"/api/scenarios/{workspace.manifest.scenario_id}/sessions", headers=headers
        )
        start_job = wait_for_job(client, started.json()["job_id"])
        assert start_job["result"]["redirect_url"] == f"/sessions/{SESSION_ID}"

        reset = client.post(f"/api/sessions/{SESSION_ID}/reset", headers=headers)
        reset_job = wait_for_job(client, reset.json()["job_id"])
        assert reset_job["result"]["redirect_url"] == f"/sessions/{RESET_ID}"

        destroyed = client.post(f"/api/sessions/{SESSION_ID}/destroy", headers=headers)
        destroy_job = wait_for_job(client, destroyed.json()["job_id"])
        assert destroy_job["result"]["status"] == "destroyed"
        assert destroy_job["result"]["redirect_url"] == "/"


def test_missing_resources_and_static_assets_are_handled(tmp_path: Path) -> None:
    workspace = FakeWorkspace(tmp_path)
    with TestClient(create_app(workspace=workspace)) as client:  # type: ignore[arg-type]
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/progress").json() == []
        assert client.get("/static/app.css").status_code == 200
        assert client.get("/scenarios/missing").status_code == 404
        assert client.get(f"/sessions/{RESET_ID}").status_code == 404
        assert client.get(f"/api/jobs/{RESET_ID}").status_code == 404


def test_topic_page_prefers_durable_solved_progress_over_active_state(tmp_path: Path) -> None:
    workspace = FakeWorkspace(tmp_path)
    workspace.progress_value = (
        ScenarioProgress(
            scenario_id=workspace.manifest.scenario_id,
            scenario_version=workspace.manifest.version,
            attempts=3,
            solved=True,
            best_earned_weight=6,
            best_available_weight=6,
            last_checked_at=datetime(2026, 8, 11, tzinfo=UTC),
        ),
    )
    with TestClient(create_app(workspace=workspace)) as client:  # type: ignore[arg-type]
        catalog = client.get("/scenarios/topic/lfcs")
        assert "Solved" in catalog.text
        progress = client.get("/api/progress").json()[0]
        assert progress["solved"] is True
        assert progress["attempts"] == 3
