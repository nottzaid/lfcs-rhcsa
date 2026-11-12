from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sysadmin_lab.adapters.sqlite_security import secure_sqlite_path
from sysadmin_lab.domain.rehearsals import Rehearsal


class SqliteMockRehearsalRepository:
    """The current rehearsal of each mock exam; starting again replaces it."""

    def __init__(self, path: Path) -> None:
        secure_sqlite_path(path)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mock_rehearsals (
                mock_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                deadline TEXT
            ) STRICT
            """
        )
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(mock_rehearsals)")}
        if "deadline" not in columns:  # created before rehearsals could be timed
            self._connection.execute("ALTER TABLE mock_rehearsals ADD COLUMN deadline TEXT")
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteMockRehearsalRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start(self, mock_id: str, rehearsal: Rehearsal) -> None:
        moments = (rehearsal.started_at, rehearsal.deadline or rehearsal.started_at)
        if any(moment.tzinfo is None for moment in moments):
            raise ValueError("rehearsal times must include a timezone")
        deadline = rehearsal.deadline.isoformat() if rehearsal.deadline else None
        with self._connection:
            self._connection.execute(
                "INSERT INTO mock_rehearsals (mock_id, started_at, deadline) VALUES (?, ?, ?) "
                "ON CONFLICT (mock_id) DO UPDATE SET "
                "started_at = excluded.started_at, deadline = excluded.deadline",
                (mock_id, rehearsal.started_at.isoformat(), deadline),
            )

    def get(self, mock_id: str) -> Rehearsal | None:
        row = self._connection.execute(
            "SELECT started_at, deadline FROM mock_rehearsals WHERE mock_id = ?", (mock_id,)
        ).fetchone()
        if row is None:
            return None
        deadline = datetime.fromisoformat(row[1]) if row[1] else None
        return Rehearsal(datetime.fromisoformat(row[0]), deadline)
