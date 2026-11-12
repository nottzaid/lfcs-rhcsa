from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sysadmin_lab.catalog import (
    CatalogError,
    load_curriculum_manifest,
    validate_objective_references,
)
from sysadmin_lab.domain.curricula import CurriculumManifest
from sysadmin_lab.domain.models import ScenarioManifest
from tests.unit.test_models import minimal_manifest


def curriculum_payload() -> dict[str, object]:
    return {
        "curriculum_id": "lfcs-test",
        "track": "lfcs",
        "title": "LFCS test curriculum",
        "effective_as_of": "2026-08-11",
        "sources": [
            {
                "source_id": "official",
                "title": "Official scope",
                "url": "https://example.com/scope",
                "retrieved_on": "2026-08-11",
            }
        ],
        "domains": [
            {
                "domain_id": "operations",
                "title": "Operations",
                "weight_percent": 100,
                "competencies": [{"competency_id": "services", "title": "Services"}],
            }
        ],
        "exam": {
            "performance_based": True,
            "command_line": True,
            "duration_minutes": 120,
            "task_count_min": 17,
            "task_count_max": 20,
            "passing_score_percent": 67,
            "distribution_specific": False,
            "operational_notes": ["Use the designated host."],
            "source_ids": ["official"],
        },
    }


def test_current_lfcs_snapshot_has_official_shape() -> None:
    root = Path(__file__).parents[2]
    curriculum = load_curriculum_manifest(root / "curricula" / "lfcs-2026-08.yaml")
    assert [domain.weight_percent for domain in curriculum.domains] == [25, 25, 20, 20, 10]
    assert len(curriculum.objective_ids) == 34
    assert curriculum.exam.task_count_min == 17
    assert curriculum.exam.task_count_max == 20


def test_curriculum_rejects_bad_weights_ranges_and_sources() -> None:
    bad_weights = curriculum_payload()
    bad_weights["domains"][0]["weight_percent"] = 99  # type: ignore[index]
    with pytest.raises(ValidationError, match="weights must total 100"):
        CurriculumManifest.model_validate(bad_weights)

    bad_range = curriculum_payload()
    bad_range["exam"]["task_count_min"] = 21  # type: ignore[index]
    with pytest.raises(ValidationError, match="minimum exceeds"):
        CurriculumManifest.model_validate(bad_range)

    bad_source = curriculum_payload()
    bad_source["exam"]["source_ids"] = ["missing"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="unknown sources"):
        CurriculumManifest.model_validate(bad_source)


def test_scenario_objectives_resolve_against_versioned_curriculum() -> None:
    curriculum = CurriculumManifest.model_validate(curriculum_payload())
    raw = minimal_manifest()
    raw["objectives"] = [
        {
            "track": "lfcs",
            "version": "lfcs-test",
            "objective_id": "operations.services",
            "title": "Services",
        }
    ]
    scenario = ScenarioManifest.model_validate(raw)
    validate_objective_references((scenario,), (curriculum,))

    wrong = scenario.model_copy(
        update={
            "objectives": (
                scenario.objectives[0].model_copy(update={"objective_id": "operations.missing"}),
            )
        }
    )
    with pytest.raises(CatalogError, match="unknown objective"):
        validate_objective_references((wrong,), (curriculum,))

    retitled = scenario.model_copy(
        update={"objectives": (scenario.objectives[0].model_copy(update={"title": "Daemons"}),)}
    )
    with pytest.raises(CatalogError, match="is titled 'Daemons'; the curriculum says 'Services'"):
        validate_objective_references((retitled,), (curriculum,))

    future = scenario.model_copy(
        update={"objectives": (scenario.objectives[0].model_copy(update={"version": "lfcs-2099"}),)}
    )
    with pytest.raises(CatalogError, match="unknown lfcs curriculum lfcs-2099"):
        validate_objective_references((future,), (curriculum,))

    other_track = scenario.model_copy(
        update={
            "objectives": (
                scenario.objectives[0],
                scenario.objectives[0].model_copy(
                    update={"track": "professional", "version": "v1", "objective_id": "anything"}
                ),
            )
        }
    )
    validate_objective_references((other_track,), (curriculum,))  # no curriculum, no claim
