from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sysadmin_lab.adapters.memory_registry import MemoryResourceRegistry
from sysadmin_lab.application.resources import ManagedResource, ResourceManager, ResourceRecord
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
    connected: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def find(self, kind: ResourceKind, name: str) -> FakeResource | None:
        return self.resources.get((kind, name))

    def connect_interfaces(self, name: str, macs: tuple[str, ...]) -> None:
        self.connected.append((name, macs))

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


def test_destroying_may_forget_a_resource_that_is_already_gone() -> None:
    expected = identity()
    gateway = FakeGateway()
    registry = MemoryResourceRegistry()
    manager = ResourceManager(gateway, registry)
    manager.define(base_xml(expected), expected)
    vanished = gateway.resources.pop((expected.kind, expected.name))

    manager.remove(expected, missing_ok=True)

    assert registry.get(expected.kind, expected.name) is None
    assert vanished.calls == []  # nothing in libvirt was touched
    with pytest.raises(ResourceSafetyError, match="no ownership record"):
        manager.remove(expected, missing_ok=True)  # never forgets what it does not own


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


components = st.from_regex(r"[a-z][a-z0-9]{0,15}(-[a-z0-9]{1,10}){0,4}", fullmatch=True)


@given(scenario_id=components, role=components, session_id=st.uuids())
def test_any_valid_identity_survives_annotation_and_parsing(
    scenario_id: str, role: str, session_id: UUID
) -> None:
    name = build_resource_name(scenario_id, session_id, role)
    assert name.startswith("lal-") and len(name) <= 63
    assert build_resource_name(scenario_id, session_id, role) == name  # deterministic
    identity = ResourceIdentity(
        kind=ResourceKind.NETWORK,
        name=name,
        session_id=session_id,
        resource_id=uuid4(),
        scenario_id=scenario_id,
        role=role,
    )
    annotated = annotate_libvirt_xml(f"<network><name>{name}</name></network>", identity)
    assert identity_from_libvirt_xml(annotated) == identity


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        ("<network><name>", "invalid libvirt XML"),
        ("<domain><name>{name}</name></domain>", "expected <network> XML, got <domain>"),
    ],
)
def test_annotation_refuses_xml_it_cannot_own(xml: str, message: str) -> None:
    owned = identity()
    with pytest.raises(ValueError, match=message):
        annotate_libvirt_xml(xml.format(name=owned.name), owned)
    annotated = annotate_libvirt_xml(f"<network><name>{owned.name}</name></network>", owned)
    with pytest.raises(ValueError, match="already contains Linux Admin Lab metadata"):
        annotate_libvirt_xml(annotated, owned)


def test_parsing_refuses_broken_or_incomplete_ownership_metadata() -> None:
    owned = identity()
    annotated = annotate_libvirt_xml(f"<network><name>{owned.name}</name></network>", owned)
    with pytest.raises(ResourceSafetyError, match="cannot parse resource XML"):
        identity_from_libvirt_xml("<network>")
    with pytest.raises(ResourceSafetyError, match="unsupported schema"):
        identity_from_libvirt_xml(annotated.replace('schema="1"', 'schema="99"'))
    with pytest.raises(ResourceSafetyError, match="invalid ownership metadata"):
        identity_from_libvirt_xml(annotated.replace(str(owned.session_id), "not-a-uuid"))


def test_identities_refuse_names_libvirt_or_the_lab_cannot_use() -> None:
    session_id = uuid4()
    with pytest.raises(ValueError, match="exceeds libvirt project limit"):
        ResourceIdentity(ResourceKind.NETWORK, "lal-" + "x" * 64, session_id, uuid4(), "a", "b")
    with pytest.raises(ValueError, match="invalid role"):
        ResourceIdentity(ResourceKind.NETWORK, "lal-ok", session_id, uuid4(), "a", "Bad Role")


def test_a_registry_record_for_another_owner_of_the_same_name_is_never_acted_on() -> None:
    expected = identity()
    gateway = FakeGateway()
    manager = ResourceManager(gateway, MemoryResourceRegistry())
    manager.define(base_xml(expected), expected)
    other = replace(expected, resource_id=uuid4())

    with pytest.raises(ResourceSafetyError, match="registry identity mismatch"):
        manager.is_registered(other)
    with pytest.raises(ResourceSafetyError, match="registry identity mismatch"):
        manager.remove(other)
    assert gateway.resources[(expected.kind, expected.name)].calls == []


@pytest.mark.parametrize(
    ("field_name", "value", "refusal"),
    [("kind", ResourceKind.DOMAIN, "kind mismatch"), ("name", "lal-other", "name mismatch")],
)
def test_a_resource_libvirt_describes_differently_is_left_alone(
    field_name: str, value: object, refusal: str
) -> None:
    expected = identity()
    gateway = FakeGateway()
    manager = ResourceManager(gateway, MemoryResourceRegistry())
    manager.define(base_xml(expected), expected)
    resource = gateway.resources[(expected.kind, expected.name)]
    setattr(resource, field_name, value)

    with pytest.raises(ResourceSafetyError, match=refusal):
        manager.remove(expected)
    assert resource.calls == []


class StrippingGateway(FakeGateway):
    """A libvirt that drops the lab's ownership metadata from what it defines."""

    def define(self, kind: ResourceKind, xml: str) -> FakeResource:
        resource = super().define(kind, xml)
        resource.document = base_xml(identity_from_libvirt_xml(xml))
        return resource


def test_a_definition_the_lab_cannot_prove_it_owns_is_not_rolled_back() -> None:
    expected = identity()
    gateway = StrippingGateway()
    registry = MemoryResourceRegistry()

    with pytest.raises(ResourceSafetyError, match="no Linux Admin Lab"):
        ResourceManager(gateway, registry).define(base_xml(expected), expected, start=True)
    assert gateway.resources[(expected.kind, expected.name)].calls == []
    assert registry.get(expected.kind, expected.name) is None


class HalfStartingResource(FakeResource):
    def start(self) -> None:
        self.calls.append("start")
        self.active = True
        raise TimeoutError("network start timed out")


class HalfStartingGateway(FakeGateway):
    def define(self, kind: ResourceKind, xml: str) -> FakeResource:
        defined = super().define(kind, xml)
        resource = HalfStartingResource(defined.kind, defined.name, defined.uuid, xml)
        self.resources[(kind, defined.name)] = resource
        return resource


def test_a_start_that_fails_halfway_is_stopped_and_rolled_back() -> None:
    expected = identity()
    gateway = HalfStartingGateway()
    registry = MemoryResourceRegistry()

    with pytest.raises(TimeoutError, match="timed out"):
        ResourceManager(gateway, registry).define(base_xml(expected), expected, start=True)
    assert gateway.resources[(expected.kind, expected.name)].calls == [
        "start",
        "stop",
        "undefine",
    ]
    assert registry.get(expected.kind, expected.name) is None


class LockedRegistry(MemoryResourceRegistry):
    def add(self, record: ResourceRecord) -> None:
        raise sqlite3.OperationalError("database is locked")


def test_a_definition_the_registry_could_not_record_is_undefined_again() -> None:
    expected = identity()
    gateway = FakeGateway()
    registry = LockedRegistry()

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        ResourceManager(gateway, registry).define(base_xml(expected), expected, start=True)
    assert gateway.resources[(expected.kind, expected.name)].calls == ["undefine"]
    assert registry.get(expected.kind, expected.name) is None


def test_only_an_owned_domain_has_its_nics_plugged_in() -> None:
    domain = identity(ResourceKind.DOMAIN)
    gateway = FakeGateway()
    manager = ResourceManager(gateway, MemoryResourceRegistry())
    macs = ("52:54:00:aa:00:01",)

    with pytest.raises(ResourceSafetyError, match="no ownership record"):
        manager.connect_interfaces(domain, macs)
    manager.define(base_xml(domain), domain)
    with pytest.raises(ResourceSafetyError, match="no ownership record"):
        manager.connect_interfaces(replace(domain, resource_id=uuid4()), macs)
    with pytest.raises(ResourceSafetyError, match="only domains have interfaces"):
        manager.connect_interfaces(identity(ResourceKind.NETWORK), macs)

    manager.connect_interfaces(domain, macs)
    assert gateway.connected == [(domain.name, macs)]

    del gateway.resources[(domain.kind, domain.name)]
    with pytest.raises(ResourceDriftError, match="absent from libvirt"):
        manager.connect_interfaces(domain, macs)
    assert gateway.connected == [(domain.name, macs)]
