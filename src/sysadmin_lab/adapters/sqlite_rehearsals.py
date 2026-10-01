from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sysadmin_lab.adapters.sqlite_security import secure_sqlite_path


class SqliteMockRehearsalRepository:
    """When the current rehearsal of each mock exam began; starting again replaces it."""

    def __init__(self, path: Path) -> None:
        secure_sqlite_path(path)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mock_rehearsals (
                mock_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL
            ) STRICT
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteMockRehearsalRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start(self, mock_id: str, started_at: datetime) -> None:
        if started_at.tzinfo is None:
            raise ValueError("rehearsal start time must include a timezone")
        with self._connection:
            self._connection.execute(
                "INSERT INTO mock_rehearsals (mock_id, started_at) VALUES (?, ?) "
                "ON CONFLICT (mock_id) DO UPDATE SET started_at = excluded.started_at",
                (mock_id, started_at.isoformat()),
            )

    def started_at(self, mock_id: str) -> datetime | None:
        row = self._connection.execute(
            "SELECT started_at FROM mock_rehearsals WHERE mock_id = ?", (mock_id,)
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None
