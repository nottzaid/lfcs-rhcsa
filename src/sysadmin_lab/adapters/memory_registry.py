from __future__ import annotations

from sysadmin_lab.application.resources import ResourceRecord
from sysadmin_lab.domain.resources import ResourceCollisionError, ResourceKind, ResourceSafetyError


class MemoryResourceRegistry:
    """Deterministic registry used by tests and short-lived tooling."""

    def __init__(self) -> None:
        self._records: dict[tuple[ResourceKind, str], ResourceRecord] = {}

    def get(self, kind: ResourceKind, name: str) -> ResourceRecord | None:
        return self._records.get((kind, name))

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
