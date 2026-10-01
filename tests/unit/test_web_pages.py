"""Every page the lab publishes, rendered from the real catalog through the real templates.

The workspace reads scenarios, the curriculum, and mock exams straight from the repository;
only the methods that would reach libvirt report an empty lab.
"""

from __future__ import annotations

import dataclasses
import shutil
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
from sysadmin_lab.domain.progress import ScenarioProgress
from sysadmin_lab.web import create_app, render_markdown

ROOT = Path(__file__).parents[2]


class CatalogOnlyWorkspace(LabWorkspace):
    def sessions(self, *, limit: int = 100) -> tuple[ScenarioSessionSnapshot, ...]:
        return ()

    def session(self, session_id: UUID) -> ScenarioSessionSnapshot:
        raise LookupError(f"session does not exist: {session_id}")

    def progress(self) -> tuple[ScenarioProgress, ...]:
        return ()


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
