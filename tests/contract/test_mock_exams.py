from __future__ import annotations

from collections import Counter
from pathlib import Path

from sysadmin_lab.catalog import load_catalog, load_mock_catalog
from sysadmin_lab.domain.models import ScenarioCollection

ROOT = Path(__file__).parents[2]
EXAM_WEIGHTS = {  # 20 tasks at the published 25/25/20/20/10 weighting
    "operations": 5,
    "networking": 5,
    "storage": 4,
    "essential-commands": 4,
    "users-groups": 2,
}


def lfcs_domains(scenario: object) -> set[str]:
    return {
        objective.objective_id.split(".", 1)[0]
        for objective in scenario.objectives  # type: ignore[attr-defined]
        if objective.track.value == "lfcs"
    }


def test_every_mock_is_twenty_focused_tasks_at_the_exam_weights() -> None:
    scenarios = {scenario.scenario_id: scenario for scenario in load_catalog(ROOT / "scenarios")}
    for mock in load_mock_catalog(ROOT / "mock-exams"):
        assert mock.suggested_minutes == 120, mock.mock_id
        assert len(mock.tasks) == 20, mock.mock_id
        assert set(mock.tasks) <= set(scenarios), mock.mock_id
        counts: Counter[str] = Counter()
        for task in mock.tasks:
            domains = lfcs_domains(scenarios[task])
            assert len(domains) == 1, f"{task}: mock tasks must stay within one domain"
            counts.update(domains)
        assert counts == EXAM_WEIGHTS, mock.mock_id


def test_review_mocks_revisit_practice_and_exam_mocks_use_their_own_tasks() -> None:
    scenarios = {scenario.scenario_id: scenario for scenario in load_catalog(ROOT / "scenarios")}
    exam_tasks: Counter[str] = Counter()
    for mock in load_mock_catalog(ROOT / "mock-exams"):
        expected = ScenarioCollection.EXAM if mock.kind == "exam" else ScenarioCollection.PRACTICE
        wrong = [task for task in mock.tasks if scenarios[task].collection is not expected]
        assert not wrong, f"{mock.mock_id} ({mock.kind}) holds {wrong}"
        if mock.kind == "exam":
            exam_tasks.update(mock.tasks)
    every_exam_task = {
        scenario_id
        for scenario_id, scenario in scenarios.items()
        if scenario.collection is ScenarioCollection.EXAM
    }
    assert set(exam_tasks) == every_exam_task, "every exam task belongs to an exam mock"
    assert all(count == 1 for count in exam_tasks.values()), "an exam task is a first attempt"
