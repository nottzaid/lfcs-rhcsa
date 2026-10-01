from __future__ import annotations

from uuid import UUID

from sysadmin_lab.application.resources import ResourceRecord
from sysadmin_lab.domain.resources import ResourceCollisionError, ResourceKind, ResourceSafetyError


class MemoryResourceRegistry:
    """Deterministic registry used by tests and short-lived tooling."""

    def __init__(self) -> None:
        self._records: dict[tuple[ResourceKind, str], ResourceRecord] = {}

    def get(self, kind: ResourceKind, name: str) -> ResourceRecord | None:
        return self._records.get((kind, name))

    def list_session(self, session_id: UUID, kind: ResourceKind) -> tuple[ResourceRecord, ...]:
        return tuple(
            record
            for (record_kind, name), record in sorted(self._records.items())
            if record_kind is kind and record.identity.session_id == session_id
        )

    def add(self, record: ResourceRecord) -> None:
        key = (record.identity.kind, record.identity.name)
        if key in self._records:
            raise ResourceCollisionError(f"duplicate registry resource: {record.identity.name}")
        self._records[key] = record

    def remove(self, record: ResourceRecord) -> None:
        key = (record.identity.kind, record.identity.name)
        if self._records.get(key) != record:
            raise ResourceSafetyError(f"cannot remove mismatched record: {record.identity.name}")
        del self._records[key]
