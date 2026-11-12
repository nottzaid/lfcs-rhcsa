from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sysadmin_lab.application.learning_path import build_learning_path
from sysadmin_lab.catalog import load_catalog, load_curriculum_manifest
from sysadmin_lab.domain.models import ObjectiveRef, Track
from sysadmin_lab.domain.progress import ScenarioProgress

ROOT = Path(__file__).parents[2]


def test_learning_path_orders_domains_then_difficulty_then_integrated() -> None:
    scenarios = load_catalog(ROOT / "scenarios")
    curriculum = load_curriculum_manifest(ROOT / "curricula" / "lfcs-2026-08.yaml")

    path = build_learning_path(scenarios, curriculum, ())

    assert [section.key for section in path] == [
        *(domain.domain_id for domain in curriculum.domains),
        "integrated",
    ]
    assert sum(len(section.steps) for section in path) == len(scenarios)
    for section in path:
        ranks = [step.scenario.difficulty.rank for step in section.steps]
        assert ranks == sorted(ranks)
    assert all(step.scenario.integrated for step in path[-1].steps)


def test_only_the_current_version_counts_as_solved_and_unknown_domains_are_kept() -> None:
    curriculum = load_curriculum_manifest(ROOT / "curricula" / "lfcs-2026-08.yaml")
    focused = next(s for s in load_catalog(ROOT / "scenarios") if not s.integrated)
    outside = focused.model_copy(
        update={
            "scenario_id": "outside-scope",
            "objectives": (
                ObjectiveRef(
                    track=Track.PROFESSIONAL,
                    version="v1",
                    objective_id="triage.incident",
                    title="Triage",
                ),
            ),
        }
    )
    stale = ScenarioProgress(
        focused.scenario_id, focused.version + 1, 1, True, 1, 1, datetime(2026, 9, 1, tzinfo=UTC)
    )

    path = build_learning_path((focused, outside), curriculum, (stale,))

    assert [section.key for section in path][-1] == "other"
    assert path[-1].steps[0].scenario.scenario_id == "outside-scope"
    assert sum(section.solved for section in path) == 0
