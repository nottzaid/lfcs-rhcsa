from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from sysadmin_lab.adapters.sqlite_security import secure_sqlite_path
from sysadmin_lab.application.machines import SessionMachineConflictError
from sysadmin_lab.domain.resources import ResourceIdentity, ResourceKind
from sysadmin_lab.domain.session_machines import SessionMachine


class SqliteSessionMachineRepository:
    def __init__(self, path: Path) -> None:
        secure_sqlite_path(path)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_machines (
                session_id TEXT NOT NULL,
                host_name TEXT NOT NULL,
                domain_name TEXT NOT NULL UNIQUE,
                resource_id TEXT NOT NULL UNIQUE,
                scenario_id TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                private_key TEXT NOT NULL,
                address TEXT,
                PRIMARY KEY (session_id, host_name)
            ) STRICT
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteSessionMachineRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def add(self, machine: SessionMachine) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO session_machines (
                        session_id, host_name, domain_name, resource_id, scenario_id,
                        username, password, private_key, address
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(machine.session_id),
                        machine.host_name,
                        machine.identity.name,
                        str(machine.identity.resource_id),
                        machine.identity.scenario_id,
                        machine.username,
                        machine.password,
                        str(machine.private_key),
                        machine.address,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SessionMachineConflictError(
                f"session machine already exists: {machine.identity.name}"
            ) from exc

    def get(self, session_id: UUID, host_name: str) -> SessionMachine | None:
        row = self._connection.execute(
            "SELECT * FROM session_machines WHERE session_id = ? AND host_name = ?",
            (str(session_id), host_name),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def list(self, session_id: UUID) -> tuple[SessionMachine, ...]:
        rows = self._connection.execute(
            "SELECT * FROM session_machines WHERE session_id = ? ORDER BY host_name",
            (str(session_id),),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def update_address(self, machine: SessionMachine, address: str) -> SessionMachine:
        updated = SessionMachine(
            session_id=machine.session_id,
            host_name=machine.host_name,
            identity=machine.identity,
            username=machine.username,
            password=machine.password,
            private_key=machine.private_key,
            address=address,
        )
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE session_machines SET address = ?
                WHERE session_id = ? AND host_name = ? AND resource_id = ?
                """,
                (
                    address,
                    str(machine.session_id),
                    machine.host_name,
                    str(machine.identity.resource_id),
                ),
            )
        if cursor.rowcount != 1:
            raise SessionMachineConflictError(
                f"session machine changed or is missing: {machine.identity.name}"
            )
        return updated

    def remove(self, machine: SessionMachine) -> None:
        with self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM session_machines
                WHERE session_id = ? AND host_name = ? AND resource_id = ?
                """,
                (
                    str(machine.session_id),
                    machine.host_name,
                    str(machine.identity.resource_id),
                ),
            )
        if cursor.rowcount != 1:
            raise SessionMachineConflictError(
                f"cannot remove mismatched session machine: {machine.identity.name}"
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SessionMachine:
        session_id = UUID(row["session_id"])
        host_name = row["host_name"]
        identity = ResourceIdentity(
            kind=ResourceKind.DOMAIN,
            name=row["domain_name"],
            session_id=session_id,
            resource_id=UUID(row["resource_id"]),
            scenario_id=row["scenario_id"],
            role=host_name,
        )
        return SessionMachine(
            session_id=session_id,
            host_name=host_name,
            identity=identity,
            username=row["username"],
            password=row["password"],
            private_key=Path(row["private_key"]),
            address=row["address"],
        )
