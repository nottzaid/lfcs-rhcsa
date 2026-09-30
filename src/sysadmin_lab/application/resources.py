from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sysadmin_lab.domain.resources import (
    ResourceCollisionError,
    ResourceDriftError,
    ResourceIdentity,
    ResourceKind,
    ResourceSafetyError,
    annotate_libvirt_xml,
    assert_identity_matches,
    identity_from_libvirt_xml,
)


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    identity: ResourceIdentity
    hypervisor_uuid: UUID


class ResourceRegistry(Protocol):
    def get(self, kind: ResourceKind, name: str) -> ResourceRecord | None: ...

    def list_session(self, session_id: UUID, kind: ResourceKind) -> tuple[ResourceRecord, ...]: ...

    def add(self, record: ResourceRecord) -> None: ...

    def remove(self, record: ResourceRecord) -> None: ...


class ManagedResource(Protocol):
    @property
    def kind(self) -> ResourceKind: ...

    @property
    def name(self) -> str: ...

    @property
    def uuid(self) -> UUID: ...

    def xml(self) -> str: ...

    def is_active(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def undefine(self) -> None: ...


class HypervisorGateway(Protocol):
    def find(self, kind: ResourceKind, name: str) -> ManagedResource | None: ...

    def define(self, kind: ResourceKind, xml: str) -> ManagedResource: ...


class ResourceManager:
    """Mutates libvirt only when resource ownership is independently provable."""

    def __init__(self, gateway: HypervisorGateway, registry: ResourceRegistry) -> None:
        self._gateway = gateway
        self._registry = registry

    def define(
        self, xml: str, identity: ResourceIdentity, *, start: bool = False
    ) -> ResourceRecord:
        if self._registry.get(identity.kind, identity.name) is not None:
            raise ResourceCollisionError(f"registry already contains {identity.name}")
        if self._gateway.find(identity.kind, identity.name) is not None:
            raise ResourceCollisionError(f"libvirt already contains {identity.name}")

        annotated_xml = annotate_libvirt_xml(xml, identity)
        resource = self._gateway.define(identity.kind, annotated_xml)
        record = ResourceRecord(identity=identity, hypervisor_uuid=resource.uuid)
        try:
            self._assert_resource(resource, record)
            self._registry.add(record)
            if start:
                resource.start()
        except Exception:
            self._rollback_new_resource(resource, record)
            raise
        return record

    def remove(self, identity: ResourceIdentity) -> None:
        record = self._registry.get(identity.kind, identity.name)
        if record is None:
            raise ResourceSafetyError(f"registry has no ownership record for {identity.name}")
        if record.identity != identity:
            raise ResourceSafetyError(f"registry identity mismatch for {identity.name}")

        resource = self._gateway.find(identity.kind, identity.name)
        if resource is None:
            raise ResourceDriftError(f"registered resource is absent from libvirt: {identity.name}")
        self._assert_resource(resource, record)
        if resource.is_active():
            resource.stop()
        resource.undefine()
        self._registry.remove(record)

    def owned(self, session_id: UUID, kind: ResourceKind) -> tuple[ResourceIdentity, ...]:
        """Identities the registry records for one session, for exact per-session cleanup."""
        return tuple(record.identity for record in self._registry.list_session(session_id, kind))

    def is_registered(self, identity: ResourceIdentity) -> bool:
        record = self._registry.get(identity.kind, identity.name)
        if record is None:
            return False
        if record.identity != identity:
            raise ResourceSafetyError(f"registry identity mismatch for {identity.name}")
        return True

    @staticmethod
    def _assert_resource(resource: ManagedResource, record: ResourceRecord) -> None:
        if resource.kind is not record.identity.kind:
            raise ResourceSafetyError("libvirt resource kind mismatch")
        if resource.name != record.identity.name:
            raise ResourceSafetyError("libvirt resource name mismatch")
        if resource.uuid != record.hypervisor_uuid:
            raise ResourceSafetyError("libvirt UUID does not match registry")
        actual_identity = identity_from_libvirt_xml(resource.xml())
        assert_identity_matches(actual_identity, record.identity)

    def _rollback_new_resource(self, resource: ManagedResource, record: ResourceRecord) -> None:
        """Rollback only the resource just returned by define, never a looked-up resource."""
        try:
            self._assert_resource(resource, record)
        except ResourceSafetyError:
            return
        if resource.is_active():
            resource.stop()
        resource.undefine()
        stored = self._registry.get(record.identity.kind, record.identity.name)
        if stored == record:
            self._registry.remove(record)
