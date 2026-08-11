from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from sysadmin_lab.adapters.libvirt_gateway import LibvirtGateway
from sysadmin_lab.adapters.sqlite_registry import SqliteResourceRegistry
from sysadmin_lab.application.resources import ResourceManager
from sysadmin_lab.domain.resources import (
    ResourceIdentity,
    ResourceKind,
    build_resource_name,
)

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("LAL_RUN_LIVE") != "1",
    reason="set LAL_RUN_LIVE=1 to permit disposable system-libvirt resources",
)
def test_guarded_network_define_and_remove(tmp_path: Path) -> None:
    session_id = uuid4()
    identity = ResourceIdentity(
        kind=ResourceKind.NETWORK,
        name=build_resource_name("live-safety", session_id, "network"),
        session_id=session_id,
        resource_id=uuid4(),
        scenario_id="live-safety",
        role="network",
    )
    xml = f"<network><name>{identity.name}</name></network>"

    with (
        LibvirtGateway.connect() as gateway,
        SqliteResourceRegistry(tmp_path / "state.db") as registry,
    ):
        manager = ResourceManager(gateway, registry)
        try:
            record = manager.define(xml, identity)
            found = gateway.find(identity.kind, identity.name)
            assert found is not None
            assert found.uuid == record.hypervisor_uuid
        finally:
            if registry.get(identity.kind, identity.name) is not None:
                manager.remove(identity)
        assert gateway.find(identity.kind, identity.name) is None
