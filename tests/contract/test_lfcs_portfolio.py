from __future__ import annotations

from pathlib import Path

import yaml

from sysadmin_lab.catalog import load_curriculum_manifest


def test_lfcs_portfolio_covers_every_published_competency_twice() -> None:
    root = Path(__file__).parents[2]
    curriculum = load_curriculum_manifest(root / "curricula" / "lfcs-2026-08.yaml")
    portfolio_path = root / "curricula" / "lfcs-2026-08-portfolio.yaml"
    portfolio = yaml.safe_load(portfolio_path.read_text())

    expected = {
        f"{domain.domain_id}.{competency.competency_id}"
        for domain in curriculum.domains
        for competency in domain.competencies
    }
    focused = {
        competency
        for scenario in portfolio["focused_scenarios"]
        for competency in scenario["competencies"]
    }
    integrated = {
        competency
        for scenario in portfolio["integrated_scenarios"]
        for competency in scenario["competencies"]
    }

    assert focused == expected
    assert integrated == expected
    assert portfolio["time_policy"] == "advisory-only"


def test_lfcs_portfolio_scenario_ids_are_unique_and_estimates_are_not_deadlines() -> None:
    root = Path(__file__).parents[2]
    portfolio = yaml.safe_load(
        (root / "curricula" / "lfcs-2026-08-portfolio.yaml").read_text()
    )
    scenarios = portfolio["focused_scenarios"] + portfolio["integrated_scenarios"]
    scenario_ids = [scenario["scenario_id"] for scenario in scenarios]

    assert len(scenario_ids) == len(set(scenario_ids))
    assert all(scenario["estimated_minutes"] >= 5 for scenario in scenarios)
    assert all("time_limit" not in scenario for scenario in scenarios)
