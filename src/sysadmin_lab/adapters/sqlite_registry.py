from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from sysadmin_lab.adapters.sqlite_security import secure_sqlite_path
from sysadmin_lab.application.resources import ResourceRecord
from sysadmin_lab.domain.resources import (
    ResourceCollisionError,
    ResourceIdentity,
    ResourceKind,
    ResourceSafetyError,
)


class SqliteResourceRegistry:
    """Durable ownership registry with exact-match deletion semantics."""

    def __init__(self, path: Path) -> None:
        secure_sqlite_path(path)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resources (
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                session_id TEXT NOT NULL,
                resource_id TEXT NOT NULL UNIQUE,
                scenario_id TEXT NOT NULL,
                role TEXT NOT NULL,
                hypervisor_uuid TEXT NOT NULL UNIQUE,
                PRIMARY KEY (kind, name)
            ) STRICT
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteResourceRegistry:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get(self, kind: ResourceKind, name: str) -> ResourceRecord | None:
        row = self._connection.execute(
            "SELECT * FROM resources WHERE kind = ? AND name = ?", (kind.value, name)
        ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def list_session(self, session_id: UUID, kind: ResourceKind) -> tuple[ResourceRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM resources WHERE session_id = ? AND kind = ? ORDER BY name",
            (str(session_id), kind.value),
        ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

    def add(self, record: ResourceRecord) -> None:
        identity = record.identity
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO resources (
                        kind, name, session_id, resource_id, scenario_id, role, hypervisor_uuid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.kind.value,
                        identity.name,
                        str(identity.session_id),
                        str(identity.resource_id),
                        identity.scenario_id,
                        identity.role,
                        str(record.hypervisor_uuid),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ResourceCollisionError(
                f"duplicate durable resource: {record.identity.name}"
            ) from exc

    def remove(self, record: ResourceRecord) -> None:
        identity = record.identity
        with self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM resources
                WHERE kind = ? AND name = ? AND session_id = ? AND resource_id = ?
                  AND scenario_id = ? AND role = ? AND hypervisor_uuid = ?
                """,
                (
                    identity.kind.value,
                    identity.name,
                    str(identity.session_id),
                    str(identity.resource_id),
                    identity.scenario_id,
                    identity.role,
                    str(record.hypervisor_uuid),
                ),
            )
        if cursor.rowcount != 1:
            raise ResourceSafetyError(f"cannot remove mismatched record: {identity.name}")

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ResourceRecord:
        try:
            identity = ResourceIdentity(
                kind=ResourceKind(row["kind"]),
                name=row["name"],
                session_id=UUID(row["session_id"]),
                resource_id=UUID(row["resource_id"]),
                scenario_id=row["scenario_id"],
                role=row["role"],
            )
            return ResourceRecord(identity=identity, hypervisor_uuid=UUID(row["hypervisor_uuid"]))
        except (ValueError, TypeError) as exc:
            raise ResourceSafetyError(f"invalid durable resource record: {exc}") from exc
