from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sysadmin_lab.adapters.sqlite_security import secure_sqlite_path
from sysadmin_lab.domain.progress import CheckAttempt


class SqliteCheckAttemptRepository:
    def __init__(self, path: Path) -> None:
        secure_sqlite_path(path)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS check_attempts (
                attempt_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                scenario_version INTEGER NOT NULL CHECK (scenario_version >= 1),
                checked_at TEXT NOT NULL,
                earned_weight INTEGER NOT NULL CHECK (earned_weight >= 0),
                available_weight INTEGER NOT NULL CHECK (available_weight >= 1),
                required_passed INTEGER NOT NULL CHECK (required_passed IN (0, 1)),
                has_errors INTEGER NOT NULL CHECK (has_errors IN (0, 1)),
                CHECK (earned_weight <= available_weight)
            ) STRICT
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteCheckAttemptRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def add(self, attempt: CheckAttempt) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO check_attempts (
                    attempt_id, session_id, scenario_id, scenario_version, checked_at,
                    earned_weight, available_weight, required_passed, has_errors
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(attempt.attempt_id),
                    str(attempt.session_id),
                    attempt.scenario_id,
                    attempt.scenario_version,
                    attempt.checked_at.isoformat(),
                    attempt.earned_weight,
                    attempt.available_weight,
                    int(attempt.required_passed),
                    int(attempt.has_errors),
                ),
            )

    def list_all(self) -> tuple[CheckAttempt, ...]:
        rows = self._connection.execute(
            "SELECT * FROM check_attempts ORDER BY checked_at DESC"
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> CheckAttempt:
        return CheckAttempt(
            attempt_id=UUID(row["attempt_id"]),
            session_id=UUID(row["session_id"]),
            scenario_id=row["scenario_id"],
            scenario_version=row["scenario_version"],
            checked_at=datetime.fromisoformat(row["checked_at"]),
            earned_weight=row["earned_weight"],
            available_weight=row["available_weight"],
            required_passed=bool(row["required_passed"]),
            has_errors=bool(row["has_errors"]),
        )
