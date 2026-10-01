"""The ownership rules that keep the lab away from everything else on the host.

Each case builds the dangerous situation with real libvirt networks, which are cheap to
create, and proves the resource manager refuses to act, or cleans up after itself.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from sysadmin_lab.adapters.libvirt_gateway import LibvirtGateway
from sysadmin_lab.adapters.sqlite_registry import SqliteResourceRegistry
from sysadmin_lab.application.resources import (
    ResourceCollisionError,
    ResourceDriftError,
    ResourceManager,
    ResourceSafetyError,
)
from sysadmin_lab.domain.resources import ResourceIdentity, ResourceKind, build_resource_name

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("LAL_RUN_LIVE") != "1",
        reason="set LAL_RUN_LIVE=1 to permit disposable system-libvirt resources",
    ),
]


def network_identity(role: str) -> ResourceIdentity:
    session_id = uuid4()
    return ResourceIdentity(
        kind=ResourceKind.NETWORK,
        name=build_resource_name("live-safety", session_id, role),
        session_id=session_id,
        resource_id=uuid4(),
        scenario_id="live-safety",
        role=role,
    )


def plain_network(name: str) -> str:
    return f"<network><name>{name}</name></network>"


@pytest.fixture
def lab(tmp_path: Path) -> Iterator[tuple[ResourceManager, SqliteResourceRegistry, object]]:
    import libvirt  # type: ignore[import-untyped]

    raw = libvirt.open("qemu:///system")
    created: list[str] = []
    with (
        LibvirtGateway.connect() as gateway,
        SqliteResourceRegistry(tmp_path / "state.db") as registry,
    ):
        yield ResourceManager(gateway, registry), registry, (raw, created)
    for name in created:
        try:
            network = raw.networkLookupByName(name)
        except libvirt.libvirtError:
            continue
        if network.isActive():
            network.destroy()
        network.undefine()
    raw.close()


def test_the_manager_never_takes_over_a_network_it_did_not_create(lab: tuple) -> None:
    manager, _registry, (raw, created) = lab
    identity = network_identity("outsider")
    created.append(identity.name)
    raw.networkDefineXML(plain_network(identity.name))  # someone else's, with a lab-like name

    with pytest.raises(ResourceCollisionError, match="libvirt already contains"):
        manager.define(plain_network(identity.name), identity)
    with pytest.raises(ResourceSafetyError, match="no ownership record"):
        manager.remove(identity)
    assert raw.networkLookupByName(identity.name)  # untouched


def test_a_registered_network_is_defined_once(lab: tuple) -> None:
    manager, _registry, (_raw, created) = lab
    identity = network_identity("once")
    created.append(identity.name)
    manager.define(plain_network(identity.name), identity)
    assert manager.is_registered(identity)
    with pytest.raises(ResourceCollisionError, match="registry already contains"):
        manager.define(plain_network(identity.name), identity)
    manager.remove(identity)
    assert not manager.is_registered(identity)


def test_a_network_removed_behind_the_managers_back_is_reported(lab: tuple) -> None:
    manager, registry, (raw, created) = lab
    identity = network_identity("drift")
    created.append(identity.name)
    manager.define(plain_network(identity.name), identity)
    raw.networkLookupByName(identity.name).undefine()

    with pytest.raises(ResourceDriftError, match="absent from libvirt"):
        manager.remove(identity)
    assert registry.get(identity.kind, identity.name) is not None  # the record says what was lost


def test_an_impostor_with_the_same_name_and_metadata_is_left_alone(lab: tuple) -> None:
    manager, _registry, (raw, created) = lab
    identity = network_identity("impostor")
    created.append(identity.name)
    manager.define(plain_network(identity.name), identity)
    original = raw.networkLookupByName(identity.name)
    copied_xml = original.XMLDesc(0)
    original.undefine()
    impostor = raw.networkDefineXML(copied_xml.replace(original.UUIDString(), str(uuid4())))

    with pytest.raises(ResourceSafetyError, match="UUID does not match registry"):
        manager.remove(identity)
    assert raw.networkLookupByName(identity.name).UUIDString() == impostor.UUIDString()


def test_a_network_that_fails_to_start_is_rolled_back(lab: tuple) -> None:
    manager, registry, (raw, created) = lab
    identity = network_identity("unstartable")
    created.append(identity.name)
    # The libvirt default network already uses this bridge address, so starting fails.
    clashing = (
        f"<network><name>{identity.name}</name>"
        "<ip address='192.168.122.1' netmask='255.255.255.0'/></network>"
    )
    with pytest.raises(Exception, match="already in use"):
        manager.define(clashing, identity, start=True)
    assert registry.get(identity.kind, identity.name) is None
    with pytest.raises(Exception, match="no network with matching name"):
        raw.networkLookupByName(identity.name)
