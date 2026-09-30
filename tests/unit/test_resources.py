from __future__ import annotations

from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import pytest

from sysadmin_lab.adapters.memory_registry import MemoryResourceRegistry
from sysadmin_lab.application.resources import ManagedResource, ResourceManager
from sysadmin_lab.domain.resources import (
    LAB_NAME_PREFIX,
    MAX_RESOURCE_NAME_LENGTH,
    ResourceCollisionError,
    ResourceDriftError,
    ResourceIdentity,
    ResourceKind,
    ResourceSafetyError,
    annotate_libvirt_xml,
    assert_identity_matches,
    build_resource_name,
    identity_from_libvirt_xml,
)

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")
RESOURCE_ID = UUID("20000000-0000-0000-0000-000000000002")


def identity(kind: ResourceKind = ResourceKind.NETWORK) -> ResourceIdentity:
    return ResourceIdentity(
        kind=kind,
        name=build_resource_name("selinux-web-port", SESSION_ID, "service-net"),
        session_id=SESSION_ID,
        resource_id=RESOURCE_ID,
        scenario_id="selinux-web-port",
        role="service-net",
    )


@dataclass
class FakeResource:
    kind: ResourceKind
    name: str
    uuid: UUID
    document: str
    active: bool = False
    calls: list[str] = field(default_factory=list)

    def xml(self) -> str:
        return self.document

    def is_active(self) -> bool:
        return self.active

    def start(self) -> None:
        self.calls.append("start")
        self.active = True

    def stop(self) -> None:
        self.calls.append("stop")
        self.active = False

    def undefine(self) -> None:
        self.calls.append("undefine")


@dataclass
class FakeGateway:
    resources: dict[tuple[ResourceKind, str], FakeResource] = field(default_factory=dict)

    def find(self, kind: ResourceKind, name: str) -> FakeResource | None:
        return self.resources.get((kind, name))

    def define(self, kind: ResourceKind, xml: str) -> FakeResource:
        parsed = identity_from_libvirt_xml(xml)
        resource = FakeResource(
            kind=kind,
            name=parsed.name,
            uuid=uuid4(),
            document=xml,
        )
        self.resources[(kind, parsed.name)] = resource
        return resource


def base_xml(value: ResourceIdentity) -> str:
    return f"<{value.kind.value}><name>{value.name}</name></{value.kind.value}>"


def test_resource_name_is_deterministic_and_bounded() -> None:
    short = build_resource_name("scenario", SESSION_ID, "node1")
    long = build_resource_name(
        "scenario-with-a-very-long-but-valid-identifier", SESSION_ID, "role-with-a-long-name"
    )
    assert short.startswith(LAB_NAME_PREFIX)
    assert len(long) <= MAX_RESOURCE_NAME_LENGTH
    assert long == build_resource_name(
        "scenario-with-a-very-long-but-valid-identifier", SESSION_ID, "role-with-a-long-name"
    )


@pytest.mark.parametrize("scenario_id,role", [("Bad", "node"), ("good", "bad_role")])
def test_resource_name_rejects_unsafe_components(scenario_id: str, role: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        build_resource_name(scenario_id, SESSION_ID, role)


def test_resource_identity_requires_project_prefix() -> None:
    with pytest.raises(ValueError, match="must start"):
        replace(identity(), name="personal-network")


def test_metadata_round_trip() -> None:
    expected = identity()
    annotated = annotate_libvirt_xml(base_xml(expected), expected)
    assert identity_from_libvirt_xml(annotated) == expected


def test_annotating_wrong_name_or_kind_is_rejected() -> None:
    expected = identity()
    with pytest.raises(ValueError, match="name does not match"):
        annotate_libvirt_xml("<network><name>wrong</name></network>", expected)
    with pytest.raises(ValueError, match="expected <network>"):
        annotate_libvirt_xml(f"<domain><name>{expected.name}</name></domain>", expected)


def test_unowned_or_foreign_metadata_is_rejected() -> None:
    with pytest.raises(ResourceSafetyError, match="no Linux Admin Lab"):
        identity_from_libvirt_xml("<network><name>lal-foreign</name></network>")
    foreign = (
        "<network><name>lal-foreign</name><metadata>"
        '<lal:resource xmlns:lal="https://linux-admin-lab.local/metadata/1" '
        'project="other" schema="1" /></metadata></network>'
    )
    with pytest.raises(ResourceSafetyError, match="wrong project"):
        identity_from_libvirt_xml(foreign)


def test_identity_mismatch_is_rejected() -> None:
    expected = identity()
    with pytest.raises(ResourceSafetyError, match="ownership mismatch"):
        assert_identity_matches(replace(expected, resource_id=uuid4()), expected)


def test_manager_defines_starts_and_removes_owned_resource() -> None:
    expected = identity()
    gateway = FakeGateway()
    registry = MemoryResourceRegistry()
    manager = ResourceManager(gateway, registry)

    record = manager.define(base_xml(expected), expected, start=True)
    resource: ManagedResource = gateway.resources[(expected.kind, expected.name)]

    assert registry.get(expected.kind, expected.name) == record
    assert manager.is_registered(expected)
    assert resource.is_active()
    manager.remove(expected)
    assert resource.calls == ["start", "stop", "undefine"]
    assert registry.get(expected.kind, expected.name) is None
    assert not manager.is_registered(expected)


def test_manager_refuses_existing_libvirt_resource() -> None:
    expected = identity()
    gateway = FakeGateway()
    gateway.resources[(expected.kind, expected.name)] = FakeResource(
        expected.kind, expected.name, uuid4(), base_xml(expected)
    )
    with pytest.raises(ResourceCollisionError, match="libvirt already contains"):
        ResourceManager(gateway, MemoryResourceRegistry()).define(base_xml(expected), expected)


def test_manager_refuses_unregistered_deletion() -> None:
    expected = identity()
    with pytest.raises(ResourceSafetyError, match="no ownership record"):
        ResourceManager(FakeGateway(), MemoryResourceRegistry()).remove(expected)


def test_manager_refuses_uuid_or_metadata_drift() -> None:
    expected = identity()
    gateway = FakeGateway()
    registry = MemoryResourceRegistry()
    manager = ResourceManager(gateway, registry)
    record = manager.define(base_xml(expected), expected)
    resource = gateway.resources[(expected.kind, expected.name)]

    resource.uuid = uuid4()
    with pytest.raises(ResourceSafetyError, match="UUID"):
        manager.remove(expected)

    resource.uuid = record.hypervisor_uuid
    resource.document = base_xml(expected)
    with pytest.raises(ResourceSafetyError, match="no Linux Admin Lab"):
        manager.remove(expected)


def test_manager_reports_registered_resource_missing_from_libvirt() -> None:
    expected = identity()
    gateway = FakeGateway()
    registry = MemoryResourceRegistry()
    manager = ResourceManager(gateway, registry)
    manager.define(base_xml(expected), expected)
    del gateway.resources[(expected.kind, expected.name)]
    with pytest.raises(ResourceDriftError, match="absent from libvirt"):
        manager.remove(expected)


def test_registry_refuses_duplicate_or_mismatched_record() -> None:
    expected = identity()
    gateway = FakeGateway()
    registry = MemoryResourceRegistry()
    record = ResourceManager(gateway, registry).define(base_xml(expected), expected)
    with pytest.raises(ResourceCollisionError, match="duplicate registry"):
        registry.add(record)
    with pytest.raises(ResourceSafetyError, match="mismatched record"):
        registry.remove(replace(record, hypervisor_uuid=uuid4()))


def test_manager_lists_resources_owned_by_one_session() -> None:
    expected = identity()
    manager = ResourceManager(FakeGateway(), MemoryResourceRegistry())
    manager.define(base_xml(expected), expected)

    assert manager.owned(expected.session_id, expected.kind) == (expected,)
    assert manager.owned(uuid4(), expected.kind) == ()
