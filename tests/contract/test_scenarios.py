from __future__ import annotations

from pathlib import Path

from sysadmin_lab.adapters.guest_checks import validate_guest_check_parameters
from sysadmin_lab.catalog import (
    load_action_manifest,
    load_catalog,
    load_curriculum_manifest,
    load_mock_catalog,
    validate_objective_references,
)


def test_released_scenarios_resolve_curriculum_and_action_manifests() -> None:
    root = Path(__file__).parents[2]
    scenarios = load_catalog(root / "scenarios")
    curriculum = load_curriculum_manifest(root / "curricula" / "lfcs-2026-08.yaml")
    validate_objective_references(scenarios, (curriculum,))
    for scenario in scenarios:
        load_action_manifest(root / "scenarios" / scenario.setup)
        load_action_manifest(root / "scenarios" / scenario.reference_solution)
        for solution in (*scenario.alternate_solutions, *scenario.rejected_solutions):
            load_action_manifest(root / "scenarios" / solution)
        for check in scenario.checks:
            validate_guest_check_parameters(check)


def test_released_scenarios_teach_by_the_authoring_standard() -> None:
    """Every released scenario carries the learning layer docs/authoring.md describes."""
    root = Path(__file__).parents[2]
    problems: list[str] = []
    for scenario in load_catalog(root / "scenarios"):
        name = scenario.scenario_id
        exam = scenario.collection.value == "exam"
        if not scenario.requirements:
            problems.append(f"{name}: states no requirements")
        if exam and scenario.hints:
            problems.append(f"{name}: an exam task has no hints, as on the exam")
        if not exam and not 2 <= len(scenario.hints) <= 4:
            problems.append(f"{name}: has {len(scenario.hints)} hints; the standard is two to four")
        sections = scenario.debrief or ""
        expected = ["## Check it yourself", "## Tempting but wrong"]
        if exam:
            expected.insert(0, "## One way to do it")
        elif scenario.task_type.value in {"troubleshoot", "fix"}:
            expected.insert(0, "## What was wrong")
        problems.extend(
            f"{name}: debrief has no {section!r} section"
            for section in expected
            if section not in sections
        )
        if not scenario.rejected_solutions:
            problems.append(f"{name}: has no rejected solution to prove its checks")
    assert not problems, "\n".join(problems)


def test_every_competency_has_a_focused_verified_scenario() -> None:
    """Coverage comes from the scenarios themselves, so it cannot drift from what they teach."""
    root = Path(__file__).parents[2]
    curriculum = load_curriculum_manifest(root / "curricula" / "lfcs-2026-08.yaml")
    covered = {
        objective.objective_id
        for scenario in load_catalog(root / "scenarios")
        if scenario.status.value == "verified"
        and not scenario.integrated
        and scenario.collection.value == "practice"
        for objective in scenario.objectives
        if objective.track.value == curriculum.track.value
    }
    missing = sorted(curriculum.objective_ids - covered)
    assert not missing, f"competencies without a focused verified scenario: {missing}"


def test_the_exam_rehearsals_together_cover_every_competency() -> None:
    root = Path(__file__).parents[2]
    curriculum = load_curriculum_manifest(root / "curricula" / "lfcs-2026-08.yaml")
    scenarios = {scenario.scenario_id: scenario for scenario in load_catalog(root / "scenarios")}
    covered = {
        objective.objective_id
        for mock in load_mock_catalog(root / "mock-exams")
        if mock.kind == "exam"
        for task in mock.tasks
        for objective in scenarios[task].objectives
        if objective.track.value == curriculum.track.value
    }
    missing = sorted(curriculum.objective_ids - covered)
    assert not missing, f"competencies no exam rehearsal asks for: {missing}"
