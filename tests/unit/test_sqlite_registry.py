from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from sysadmin_lab.adapters.sqlite_registry import SqliteResourceRegistry
from sysadmin_lab.application.resources import ResourceRecord
from sysadmin_lab.domain.resources import (
    ResourceCollisionError,
    ResourceIdentity,
    ResourceKind,
    ResourceSafetyError,
    build_resource_name,
)


def record() -> ResourceRecord:
    session_id = UUID("30000000-0000-0000-0000-000000000003")
    identity = ResourceIdentity(
        kind=ResourceKind.NETWORK,
        name=build_resource_name("registry-test", session_id, "network"),
        session_id=session_id,
        resource_id=UUID("40000000-0000-0000-0000-000000000004"),
        scenario_id="registry-test",
        role="network",
    )
    return ResourceRecord(
        identity=identity,
        hypervisor_uuid=UUID("50000000-0000-0000-0000-000000000005"),
    )


def test_registry_persists_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    expected = record()
    with SqliteResourceRegistry(path) as registry:
        registry.add(expected)
    with SqliteResourceRegistry(path) as registry:
        assert registry.get(expected.identity.kind, expected.identity.name) == expected
        registry.remove(expected)
        assert registry.get(expected.identity.kind, expected.identity.name) is None


def test_registry_refuses_duplicate_and_mismatched_deletion(tmp_path: Path) -> None:
    expected = record()
    with SqliteResourceRegistry(tmp_path / "state.db") as registry:
        registry.add(expected)
        with pytest.raises(ResourceCollisionError, match="duplicate durable"):
            registry.add(expected)
        with pytest.raises(ResourceSafetyError, match="mismatched record"):
            registry.remove(replace(expected, hypervisor_uuid=uuid4()))


def test_corrupt_registry_row_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    expected = record()
    with SqliteResourceRegistry(path) as registry:
        registry.add(expected)
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE resources SET session_id = 'not-a-uuid' WHERE name = ?",
            (expected.identity.name,),
        )
        connection.commit()
        connection.close()
        with pytest.raises(ResourceSafetyError, match="invalid durable"):
            registry.get(expected.identity.kind, expected.identity.name)


def test_registry_lists_one_sessions_records_of_one_kind(tmp_path: Path) -> None:
    network = record()
    other_session = replace(
        network.identity,
        name=build_resource_name("registry-test", uuid4(), "network"),
        session_id=uuid4(),
        resource_id=uuid4(),
    )
    with SqliteResourceRegistry(tmp_path / "state.db") as registry:
        registry.add(network)
        registry.add(ResourceRecord(other_session, uuid4()))
        assert registry.list_session(network.identity.session_id, ResourceKind.NETWORK) == (
            network,
        )
        assert registry.list_session(network.identity.session_id, ResourceKind.DOMAIN) == ()


def test_session_listings_need_a_positive_limit(tmp_path: Path) -> None:
    from sysadmin_lab.adapters.sqlite_sessions import SqliteSessionRepository

    with SqliteSessionRepository(tmp_path / "state.db") as sessions:
        assert sessions.list_all(limit=1) == ()
        with pytest.raises(ValueError, match="session list limit must be positive"):
            sessions.list_all(limit=0)
