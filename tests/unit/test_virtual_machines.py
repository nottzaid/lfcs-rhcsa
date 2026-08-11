from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.domain.resources import ResourceKind
from sysadmin_lab.domain.virtual_machines import DomainSpec, domain_identity, render_domain_xml

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


def test_domain_identity_is_deterministic() -> None:
    first = domain_identity("base-smoke", SESSION_ID, "node1")
    second = domain_identity("base-smoke", SESSION_ID, "node1")
    assert first == second
    assert first.kind is ResourceKind.DOMAIN
    assert first.name == "lal-base-smoke-10000000-node1"


def test_domain_spec_rejects_wrong_kind_or_relative_paths() -> None:
    identity = domain_identity("base-smoke", SESSION_ID)
    with pytest.raises(ValueError, match="absolute"):
        DomainSpec(identity, Path("disk.qcow2"), Path("seed.iso"))


def test_domain_xml_contains_boot_console_and_guest_agent_contract(tmp_path: Path) -> None:
    identity = domain_identity("base-smoke", SESSION_ID)
    spec = DomainSpec(identity, tmp_path / "root.qcow2", tmp_path / "seed.iso")
    root = ET.fromstring(render_domain_xml(spec))

    assert root.tag == "domain"
    assert root.findtext("name") == identity.name
    assert root.findtext("uuid") == str(identity.resource_id)
    assert root.find("./os/type").attrib == {"arch": "x86_64", "machine": "q35"}
    assert root.find("./devices/disk/source").attrib["file"] == str(spec.disk)
    assert root.find("./devices/interface/source").attrib["network"] == "default"
    assert root.find("./devices/console") is not None
    assert root.find("./devices/graphics").attrib["type"] == "spice"
    assert root.find("./devices/channel/target").attrib["name"] == "org.qemu.guest_agent.0"


def test_domain_xml_attaches_named_data_disks_after_root(tmp_path: Path) -> None:
    identity = domain_identity("storage-lab", SESSION_ID)
    spec = DomainSpec(
        identity,
        tmp_path / "root.qcow2",
        tmp_path / "seed.iso",
        data_disks=(("data", tmp_path / "data.qcow2"),),
    )

    disks = ET.fromstring(render_domain_xml(spec)).findall("./devices/disk")
    assert disks[0].find("target").attrib["dev"] == "vda"
    assert disks[1].find("target").attrib["dev"] == "vdb"
    assert disks[1].findtext("serial") == "lal-data"
