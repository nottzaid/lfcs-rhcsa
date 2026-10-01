"""Every page the lab publishes, rendered from the real catalog through the real templates.

The workspace reads scenarios, the curriculum, and mock exams straight from the repository;
only the methods that would reach libvirt report an empty lab.
"""

from __future__ import annotations

import dataclasses
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from sysadmin_lab.application.lab_workspace import (
    LabWorkspace,
    ScenarioSessionSnapshot,
    WorkspacePaths,
)
from sysadmin_lab.domain.progress import CheckAttempt, ScenarioProgress
from sysadmin_lab.domain.rehearsals import Rehearsal, RehearsalScore, score_rehearsal
from sysadmin_lab.web import create_app, render_markdown

ROOT = Path(__file__).parents[2]


class CatalogOnlyWorkspace(LabWorkspace):
    def sessions(self, *, limit: int = 100) -> tuple[ScenarioSessionSnapshot, ...]:
        return ()

    def session(self, session_id: UUID) -> ScenarioSessionSnapshot:
        raise LookupError(f"session does not exist: {session_id}")

    def progress(self) -> tuple[ScenarioProgress, ...]:
        return ()

    def rehearsal(self, mock_id: str) -> RehearsalScore | None:
        self.mock_exam(mock_id)
        return None


@pytest.fixture(scope="module")
def site() -> TestClient:
    workspace = CatalogOnlyWorkspace(WorkspacePaths.under(ROOT))
    with TestClient(create_app(ROOT, workspace=workspace)) as client:
        yield client


def test_every_released_scenario_page_renders_its_brief(site: TestClient) -> None:
    workspace = CatalogOnlyWorkspace(WorkspacePaths.under(ROOT))
    for scenario in workspace.scenarios():
        page = site.get(f"/scenarios/{scenario.scenario_id}")
        assert page.status_code == 200, scenario.scenario_id
        assert str(render_markdown(scenario.task)) in page.text, scenario.scenario_id
        for requirement in scenario.requirements:
            assert str(render_markdown(requirement)) in page.text, scenario.scenario_id
        assert "Launch scenario" in page.text


def test_the_catalog_lists_every_scenario_on_the_practice_path(site: TestClient) -> None:
    page = site.get("/scenarios/topic/lfcs")
    assert page.status_code == 200
    workspace = CatalogOnlyWorkspace(WorkspacePaths.under(ROOT))
    for scenario in workspace.scenarios():
        assert scenario.title.replace("'", "&#39;") in page.text, scenario.scenario_id


def test_every_mock_exam_page_lists_its_tasks(site: TestClient) -> None:
    workspace = CatalogOnlyWorkspace(WorkspacePaths.under(ROOT))
    titles = {scenario.scenario_id: scenario.title for scenario in workspace.scenarios()}
    mocks = workspace.mock_exams()
    assert mocks
    for mock in mocks:
        page = site.get(f"/mocks/{mock.mock_id}")
        assert page.status_code == 200, mock.mock_id
        for task in mock.tasks:
            assert titles[task].replace("'", "&#39;") in page.text, (mock.mock_id, task)


@pytest.mark.parametrize(
    "path",
    [
        "/scenarios/no-such-scenario",
        "/mocks/no-such-mock",
        f"/sessions/{uuid4()}",
    ],
)
def test_unknown_pages_are_not_found(site: TestClient, path: str) -> None:
    assert site.get(path).status_code == 404


def test_draft_scenarios_and_mocks_are_not_published(tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    shutil.copytree(ROOT / "scenarios", scenarios)
    draft = yaml.safe_load((scenarios / "local-account-repair.yaml").read_text())
    draft["status"] = "draft"
    (scenarios / "local-account-repair.yaml").write_text(yaml.safe_dump(draft))
    mocks = tmp_path / "mock-exams"
    mocks.mkdir()
    mock = yaml.safe_load((ROOT / "mock-exams" / "lfcs-mock-a.yaml").read_text())
    mock["status"] = "draft"
    (mocks / "lfcs-mock-a.yaml").write_text(yaml.safe_dump(mock))
    paths = dataclasses.replace(
        WorkspacePaths.under(ROOT), scenario_directory=scenarios, mock_exam_directory=mocks
    )
    with TestClient(create_app(ROOT, workspace=CatalogOnlyWorkspace(paths))) as client:
        assert client.get("/scenarios/local-account-repair").status_code == 404
        assert client.get(f"/mocks/{mock['mock_id']}").status_code == 404
        assert "lfcs-mock-a" not in client.get("/scenarios/topic/lfcs").text


class RehearsingWorkspace(CatalogOnlyWorkspace):
    """A rehearsal of mock A in which one task was solved and one half done."""

    def __init__(self, paths: WorkspacePaths, deadline: datetime | None = None) -> None:
        super().__init__(paths)
        self.started: list[tuple[str, bool]] = []
        self.deadline = deadline

    def rehearsal(self, mock_id: str) -> RehearsalScore | None:
        mock = self.mock_exam(mock_id)
        start = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
        versions = {scenario.scenario_id: scenario.version for scenario in self.scenarios()}
        attempts = [
            CheckAttempt(uuid4(), uuid4(), task, versions[task], start, earned, 2, solved, False)
            for task, earned, solved in ((mock.tasks[0], 2, True), (mock.tasks[1], 1, False))
        ]
        return score_rehearsal(mock.tasks, versions, attempts, Rehearsal(start, self.deadline), 67)

    def start_rehearsal(self, mock_id: str, *, timed: bool = False) -> Rehearsal:
        self.started.append((mock_id, timed))
        return Rehearsal(datetime.now(UTC))


def finished(client: TestClient, response: object) -> dict[str, object]:
    job_id = response.json()["job_id"]  # type: ignore[attr-defined]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
    raise AssertionError("job did not finish")


def test_a_mock_page_shows_the_rehearsal_score_against_the_pass_mark() -> None:
    workspace = RehearsingWorkspace(WorkspacePaths.under(ROOT))
    with TestClient(create_app(ROOT, workspace=workspace)) as client:
        page = client.get("/mocks/lfcs-mock-a").text
        assert "<strong>8%</strong>: 1 of 20 tasks solved" in page
        assert "The LFCS passes at 67%." in page
        assert page.count('class="nowrap result-solved"') == 1
        assert page.count('class="nowrap result-partial"') == 1
        assert page.count('class="nowrap result-untouched"') == 18
        assert "50% of requirements" in page
        assert "Untimed: every check from now on counts." in page
        assert "Start over, timed" in page and "Start over, untimed" in page
        assert "as on the exam" not in page  # partial credit is reported, not documented

        assert client.post("/api/mocks/lfcs-mock-a/rehearsal").status_code == 403
        browser = {"X-Lab-Request": "browser"}
        untimed = client.post("/api/mocks/lfcs-mock-a/rehearsal", headers=browser)
        timed = client.post("/api/mocks/lfcs-mock-a/rehearsal?timed=true", headers=browser)
        assert untimed.status_code == 202
        assert finished(client, untimed)["result"] == {"redirect_url": "/mocks/lfcs-mock-a"}
        finished(client, timed)
        assert workspace.started == [("lfcs-mock-a", False), ("lfcs-mock-a", True)]


@pytest.mark.parametrize("minutes_left", [90, -5])
def test_a_timed_rehearsal_counts_down_then_says_time_was_up(minutes_left: int) -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=minutes_left)
    workspace = RehearsingWorkspace(WorkspacePaths.under(ROOT), deadline=deadline)
    with TestClient(create_app(ROOT, workspace=workspace)) as client:
        page = client.get("/mocks/lfcs-mock-a").text
    if minutes_left > 0:
        assert f'data-deadline="{deadline.isoformat()}"' in page
        assert "Checks after that will not count." in page
    else:
        assert "data-deadline" not in page
        assert "Time was up at" in page and "Checks after that do not count." in page


def test_a_mock_without_a_rehearsal_offers_to_start_one(site: TestClient) -> None:
    page = site.get("/mocks/lfcs-mock-b").text
    assert "Start timed (120 min)" in page and "Start untimed" in page
    assert "Result</th>" not in page
    assert "Review rehearsal" in page
