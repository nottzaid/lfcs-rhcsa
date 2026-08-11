from __future__ import annotations

from pathlib import Path

from sysadmin_lab.adapters.guest_checks import validate_guest_check_parameters
from sysadmin_lab.catalog import (
    load_action_manifest,
    load_catalog,
    load_curriculum_manifest,
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
        for alternate in scenario.alternate_solutions:
            load_action_manifest(root / "scenarios" / alternate)
        for check in scenario.checks:
            validate_guest_check_parameters(check)
