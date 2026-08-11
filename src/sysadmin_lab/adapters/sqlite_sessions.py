from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from sysadmin_lab.adapters.sqlite_security import secure_sqlite_path
from sysadmin_lab.application.sessions import SessionConflictError
from sysadmin_lab.domain.sessions import SessionState, SessionStatus


class SqliteSessionRepository:
    def __init__(self, path: Path) -> None:
        secure_sqlite_path(path)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                status TEXT NOT NULL,
                generation INTEGER NOT NULL CHECK (generation >= 0),
                revision INTEGER NOT NULL CHECK (revision >= 0),
                error TEXT
            ) STRICT
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteSessionRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def create(self, state: SessionState) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id, scenario_id, status, generation, revision, error
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    self._values(state),
                )
        except sqlite3.IntegrityError as exc:
            raise SessionConflictError(f"session already exists: {state.session_id}") from exc

    def get(self, session_id: UUID) -> SessionState | None:
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (str(session_id),)
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list_all(self, *, limit: int = 100) -> tuple[SessionState, ...]:
        if limit < 1:
            raise ValueError("session list limit must be positive")
        rows = self._connection.execute(
            "SELECT * FROM sessions ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SessionState:
        return SessionState(
            session_id=UUID(row["session_id"]),
            scenario_id=row["scenario_id"],
            status=SessionStatus(row["status"]),
            generation=row["generation"],
            revision=row["revision"],
            error=row["error"],
        )

    def save(self, previous_revision: int, state: SessionState) -> None:
        if state.revision != previous_revision + 1:
            raise SessionConflictError("session revision must advance by exactly one")
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE sessions
                SET scenario_id = ?, status = ?, generation = ?, revision = ?, error = ?
                WHERE session_id = ? AND revision = ?
                """,
                (
                    state.scenario_id,
                    state.status.value,
                    state.generation,
                    state.revision,
                    state.error,
                    str(state.session_id),
                    previous_revision,
                ),
            )
        if cursor.rowcount != 1:
            raise SessionConflictError(
                f"session changed concurrently or is missing: {state.session_id}"
            )

    @staticmethod
    def _values(state: SessionState) -> tuple[str, str, str, int, int, str | None]:
        return (
            str(state.session_id),
            state.scenario_id,
            state.status.value,
            state.generation,
            state.revision,
            state.error,
        )
