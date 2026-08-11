from __future__ import annotations

from pathlib import Path

import pytest

from sysadmin_lab.adapters.sqlite_security import secure_sqlite_path


def test_secure_sqlite_path_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.touch()
    link = tmp_path / "state.db"
    link.symlink_to(target)
    with pytest.raises(RuntimeError, match="symlink"):
        secure_sqlite_path(link)
