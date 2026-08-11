from __future__ import annotations

import pytest
from pydantic import ValidationError

from sysadmin_lab.domain.models import ScenarioManifest


def minimal_manifest() -> dict[str, object]:
    return {
        "scenario_id": "valid-scenario",
        "version": 1,
        "title": "A valid scenario",
        "summary": "A concise summary",
        "estimated_minutes": 15,
        "task_type": "troubleshoot",
        "difficulty": "foundation",
        "task": "Reach the required state.",
        "objectives": [
            {
                "track": "lfcs",
                "version": "current-2023",
                "objective_id": "operations.services",
                "title": "Troubleshoot services",
            }
        ],
        "sources": [{"kind": "man-page", "title": "systemctl", "man_page": "systemctl(1)"}],
        "topology": {"hosts": [{"name": "node1", "image": "rocky-base"}]},
        "checks": [
            {
                "check_id": "service-active",
                "kind": "service",
                "target": "node1",
                "description": "The service is active.",
            }
        ],
        "setup": "actions/valid.setup.yaml",
        "reference_solution": "solutions/valid.yaml",
    }


def test_minimal_manifest_is_valid() -> None:
    manifest = ScenarioManifest.model_validate(minimal_manifest())
    assert manifest.scenario_id == "valid-scenario"


def test_unknown_check_host_is_rejected() -> None:
    raw = minimal_manifest()
    raw["checks"][0]["target"] = "missing"  # type: ignore[index]
    with pytest.raises(ValidationError, match="check targets are not topology hosts"):
        ScenarioManifest.model_validate(raw)


def test_undeclared_network_is_rejected() -> None:
    raw = minimal_manifest()
    raw["topology"]["hosts"][0]["nics"] = [{"network": "missing-net"}]  # type: ignore[index]
    with pytest.raises(ValidationError, match="undeclared topology networks"):
        ScenarioManifest.model_validate(raw)


def test_reboot_hosts_require_reboot() -> None:
    raw = minimal_manifest()
    raw["persistence"] = {"reboot": False, "hosts": ["node1"]}
    with pytest.raises(ValidationError, match="persistence hosts require reboot=true"):
        ScenarioManifest.model_validate(raw)


def test_source_requires_a_locator() -> None:
    raw = minimal_manifest()
    raw["sources"] = [{"kind": "upstream", "title": "Missing locator"}]
    with pytest.raises(ValidationError, match="requires either url or man_page"):
        ScenarioManifest.model_validate(raw)


def test_duplicate_host_names_are_rejected() -> None:
    raw = minimal_manifest()
    raw["topology"]["hosts"].append(  # type: ignore[index,union-attr]
        {"name": "node1", "image": "rocky-base"}
    )
    with pytest.raises(ValidationError, match="host names must be unique"):
        ScenarioManifest.model_validate(raw)


def test_duplicate_network_names_are_rejected() -> None:
    raw = minimal_manifest()
    raw["topology"]["networks"] = [  # type: ignore[index]
        {"name": "service-net"},
        {"name": "service-net"},
    ]
    with pytest.raises(ValidationError, match="network names must be unique"):
        ScenarioManifest.model_validate(raw)


def test_duplicate_check_identifiers_are_rejected() -> None:
    raw = minimal_manifest()
    raw["checks"].append(dict(raw["checks"][0]))  # type: ignore[index,union-attr,call-overload]
    with pytest.raises(ValidationError, match="check identifiers must be unique"):
        ScenarioManifest.model_validate(raw)


def test_unknown_reboot_host_is_rejected() -> None:
    raw = minimal_manifest()
    raw["persistence"] = {"reboot": True, "hosts": ["missing"]}
    with pytest.raises(ValidationError, match="persistence hosts are not topology hosts"):
        ScenarioManifest.model_validate(raw)
