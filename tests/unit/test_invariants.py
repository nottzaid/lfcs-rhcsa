"""Manifests and runtime records refuse states the rest of the system cannot handle."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.application.placeholders import render_host_addresses
from sysadmin_lab.domain.curricula import CurriculumManifest
from sysadmin_lab.domain.images import ImageManifest
from sysadmin_lab.domain.mock_exams import MockExamManifest
from sysadmin_lab.domain.models import HostSpec
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.virtual_machines import DomainSpec, domain_identity, network_identity
from tests.unit.test_curricula import curriculum_payload
from tests.unit.test_images import image_manifest

SESSION = UUID("10000000-0000-4000-8000-000000000001")


def split_the_only_domain_in_two(payload: dict[str, Any]) -> None:
    domain = payload["domains"].pop()
    payload["domains"] = [{**domain, "weight_percent": 50}, {**domain, "weight_percent": 50}]


def curriculum_with(change: Any) -> dict[str, Any]:
    payload: dict[str, Any] = copy.deepcopy(curriculum_payload())
    change(payload)
    return payload


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda p: p["domains"][0]["competencies"].append(
                {"competency_id": "services", "title": "Again"}
            ),
            "duplicate competency identifiers in operations",
        ),
        (split_the_only_domain_in_two, "curriculum domain identifiers must be unique"),
        (
            lambda p: p["sources"].append(p["sources"][0]),
            "curriculum source identifiers must be unique",
        ),
    ],
)
def test_curricula_refuse_duplicate_identifiers(change: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        CurriculumManifest.model_validate(curriculum_with(change))


def test_mock_exams_refuse_a_task_twice() -> None:
    tasks = [f"task-{n}" for n in range(16)] + ["task-1"]
    with pytest.raises(ValidationError, match="mock exam task identifiers must be unique"):
        MockExamManifest.model_validate(
            {
                "mock_id": "mock-x",
                "title": "Mock",
                "summary": "Mock",
                "suggested_minutes": 120,
                "time_policy": "advisory-only",
                "tasks": tasks,
            }
        )


def test_hosts_refuse_duplicate_disks_and_too_little_memory() -> None:
    disk = {"name": "data", "size_mib": 512, "role": "data"}
    with pytest.raises(ValidationError, match="disk names on node2 must be unique"):
        HostSpec.model_validate({"name": "node2", "image": "img", "disks": [disk, disk]})
    with pytest.raises(ValidationError, match="greater than or equal to 1024"):
        HostSpec.model_validate({"name": "node2", "image": "img", "memory_mib": 512})


def test_cloud_image_builds_take_no_kickstart() -> None:
    payload = image_manifest(b"media").model_dump(mode="json")
    payload["build"] = {**payload["build"], "method": "cloud-image", "kickstart": "ks.cfg.in"}
    with pytest.raises(ValidationError, match="cloud-image builds cannot specify a kickstart"):
        ImageManifest.model_validate(payload)


def test_session_machines_must_be_the_domain_of_their_host(tmp_path: Path) -> None:
    key = (tmp_path / "key").resolve()
    network = network_identity("base-smoke", SESSION, "net")
    with pytest.raises(ValueError, match="session machines require a domain identity"):
        SessionMachine(SESSION, "node1", network, "labadmin", "secret", key)
    other_host = domain_identity("base-smoke", SESSION, "node2")
    with pytest.raises(ValueError, match="machine host name and resource role differ"):
        SessionMachine(SESSION, "node1", other_host, "labadmin", "secret", key)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"memory_mib": 512}, "domain memory must be at least 1024 MiB"),
        ({"vcpus": 0}, "domain must have at least one vCPU"),
        ({"network": ""}, "domain network must not be empty"),
        ({"data_disks": (("Bad Name", Path("/d.qcow2")),)}, "invalid data disk name"),
        ({"data_disks": (("data", Path("d.qcow2")),)}, "data disk paths must be absolute"),
    ],
)
def test_domain_specifications_refuse_unbootable_machines(
    changes: dict[str, Any], message: str
) -> None:
    identity = domain_identity("base-smoke", SESSION, "node1")
    with pytest.raises(ValueError, match=message):
        DomainSpec(identity, Path("/disk.qcow2"), Path("/seed.iso"), **changes)
    network = network_identity("base-smoke", SESSION, "net")
    with pytest.raises(ValueError, match="requires a domain identity"):
        DomainSpec(network, Path("/disk.qcow2"), Path("/seed.iso"))


def test_host_placeholders_resolve_inside_nested_parameters(tmp_path: Path) -> None:
    endpoints = {"node1": GuestEndpoint("192.0.2.7", "labadmin", (tmp_path / "k").resolve())}
    parameters = {"arguments": ["ping", "{{host.node1.address}}"], "nested": {"n": 3}}
    assert render_host_addresses(parameters, endpoints) == {
        "arguments": ["ping", "192.0.2.7"],
        "nested": {"n": 3},
    }
