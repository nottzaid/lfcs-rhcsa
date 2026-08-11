from __future__ import annotations

import os
from pathlib import Path


def secure_sqlite_path(path: Path) -> None:
    """Create or tighten a project state file before SQLite opens it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"refusing SQLite state symlink: {path}")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(descriptor)
    os.chmod(path, 0o600)
