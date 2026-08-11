from __future__ import annotations

from collections import Counter
from pathlib import Path

from sysadmin_lab.catalog import load_catalog, load_mock_catalog


def test_mock_exams_are_balanced_advisory_collections() -> None:
    root = Path(__file__).parents[2]
    scenarios = {scenario.scenario_id: scenario for scenario in load_catalog(root / "scenarios")}
    objective_domains = {
        scenario_id: {
            objective.objective_id.split(".", 1)[0]
            for objective in scenario.objectives
            if objective.track.value == "lfcs"
        }
        for scenario_id, scenario in scenarios.items()
    }

    for manifest in load_mock_catalog(root / "mock-exams"):
        mock = manifest.model_dump(mode="json")
        assert mock["time_policy"] == "advisory-only"
        assert mock["suggested_minutes"] == 120
        assert len(mock["tasks"]) == 20
        assert len(set(mock["tasks"])) == 20
        assert set(mock["tasks"]) <= set(scenarios)

        counts = Counter()
        for scenario_id in mock["tasks"]:
            domains = objective_domains[scenario_id]
            assert len(domains) == 1, "mock tasks must be focused scenarios"
            counts.update(domains)
        assert counts == {
            "operations": 5,
            "networking": 5,
            "storage": 4,
            "essential-commands": 4,
            "users-groups": 2,
        }
