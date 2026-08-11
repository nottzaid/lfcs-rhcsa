from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from sysadmin_lab.application.session_artifacts import SessionArtifactBuilder

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


@dataclass
class FakeRunner:
    calls: list[list[str]] = field(default_factory=list)
    fail_on: str | None = None

    def run(self, arguments: Sequence[str]) -> None:
        command = list(arguments)
        self.calls.append(command)
        if self.fail_on == command[0]:
            raise RuntimeError("command failed")
        if command[0] == "ssh-keygen":
            private_key = Path(command[command.index("-f") + 1])
            private_key.write_text("private", encoding="utf-8")
            private_key.with_suffix(private_key.suffix + ".pub").write_text(
                "ssh-ed25519 public linux-admin-lab", encoding="utf-8"
            )
        elif command[0] == "qemu-img":
            Path(command[-2]).touch()
        elif command[0] == "xorriso":
            Path(command[command.index("-output") + 1]).touch()


def test_builder_creates_overlay_seed_and_access_details(tmp_path: Path) -> None:
    runner = FakeRunner()
    builder = SessionArtifactBuilder(tmp_path / "runtime", runner)
    base = tmp_path / "base.qcow2"
    base.touch()

    paths, access = builder.create(
        session_id=SESSION_ID,
        role="node1",
        hostname="lal-node1",
        base_image=base,
    )

    assert paths.overlay.is_file()
    assert paths.seed_iso.is_file()
    assert access.username == "labadmin"
    assert access.password
    assert access.private_key.stat().st_mode & 0o777 == 0o600
    assert [call[0] for call in runner.calls] == ["ssh-keygen", "qemu-img", "xorriso"]
    user_data = (paths.seed_source / "user-data").read_text(encoding="utf-8")
    assert user_data.startswith("#cloud-config\n")
    parsed = yaml.safe_load(user_data)
    assert parsed["hostname"] == "lal-node1"
    assert parsed["users"][1]["plain_text_passwd"] == access.password


def test_builder_reuses_key_but_never_existing_session(tmp_path: Path) -> None:
    runner = FakeRunner()
    builder = SessionArtifactBuilder(tmp_path / "runtime", runner)
    base = tmp_path / "base.qcow2"
    base.touch()
    builder.create(session_id=SESSION_ID, role="node1", hostname="one", base_image=base)
    with pytest.raises(FileExistsError, match="already exist"):
        builder.create(session_id=SESSION_ID, role="node1", hostname="one", base_image=base)


def test_builder_cleans_partial_session_on_failure(tmp_path: Path) -> None:
    runner = FakeRunner(fail_on="qemu-img")
    builder = SessionArtifactBuilder(tmp_path / "runtime", runner)
    base = tmp_path / "base.qcow2"
    base.touch()
    paths = builder.paths(SESSION_ID, "node1")

    with pytest.raises(RuntimeError, match="command failed"):
        builder.create(session_id=SESSION_ID, role="node1", hostname="one", base_image=base)
    assert not paths.directory.exists()
    assert paths.private_key.exists()


def test_builder_rejects_unsafe_inputs_and_destroy_is_exact(tmp_path: Path) -> None:
    builder = SessionArtifactBuilder(tmp_path / "runtime", FakeRunner())
    with pytest.raises(ValueError, match="invalid role"):
        builder.paths(SESSION_ID, "../personal")

    paths = builder.paths(SESSION_ID, "node1")
    paths.directory.mkdir(parents=True)
    sibling = paths.directory.parent / "node2"
    sibling.mkdir()
    builder.destroy(SESSION_ID, "node1")
    assert not paths.directory.exists()
    assert sibling.exists()
