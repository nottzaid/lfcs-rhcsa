from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from sysadmin_lab.application.session_artifacts import SessionArtifactBuilder
from sysadmin_lab.domain.models import DiskSpec
from sysadmin_lab.domain.virtual_machines import ScenarioInterface

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
    # Only the guest's own name is managed; scenario edits to /etc/hosts must survive boots.
    assert parsed["manage_etc_hosts"] == "localhost"


def test_builder_reuses_key_but_never_existing_session(tmp_path: Path) -> None:
    runner = FakeRunner()
    builder = SessionArtifactBuilder(tmp_path / "runtime", runner)
    base = tmp_path / "base.qcow2"
    base.touch()
    builder.create(session_id=SESSION_ID, role="node1", hostname="one", base_image=base)
    with pytest.raises(FileExistsError, match="already exist"):
        builder.create(session_id=SESSION_ID, role="node1", hostname="one", base_image=base)


def test_builder_creates_exact_declared_data_disks(tmp_path: Path) -> None:
    runner = FakeRunner()
    builder = SessionArtifactBuilder(tmp_path / "runtime", runner)
    base = tmp_path / "base.qcow2"
    base.touch()

    paths, _access = builder.create(
        session_id=SESSION_ID,
        role="node2",
        hostname="storage-node",
        base_image=base,
        data_disks=(DiskSpec(name="data", size_mib=768, role="unused training disk"),),
    )

    assert paths.data_disks == (("data", paths.directory / "data.qcow2"),)
    assert paths.data_disks[0][1].is_file()
    data_command = [call for call in runner.calls if call[0] == "qemu-img"][1]
    assert data_command[-1] == "768M"


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


def test_scenario_interfaces_are_named_by_mac_and_left_to_the_learner(tmp_path: Path) -> None:
    runner = FakeRunner()
    builder = SessionArtifactBuilder(tmp_path / "runtime", runner)
    base = tmp_path / "base.qcow2"
    base.touch()
    lan = ScenarioInterface("lan0", "lal-branch-10000000-net-lan", "52:54:00:12:34:56")

    paths, _access = builder.create(
        session_id=SESSION_ID, role="router", hostname="router", base_image=base, interfaces=(lan,)
    )

    parsed = yaml.safe_load((paths.seed_source / "user-data").read_text(encoding="utf-8"))
    files = {item["path"]: item["content"] for item in parsed["write_files"]}
    assert files["/etc/systemd/network/70-lal-lan0.link"] == (
        "[Match]\nMACAddress=52:54:00:12:34:56\n\n[Link]\nName=lan0\n"
    )
    assert files["/etc/NetworkManager/conf.d/70-lal-scenario-interfaces.conf"] == (
        "[main]\nno-auto-default=mac:52:54:00:12:34:56\n"
    )
    assert files["/etc/linux-admin-lab/interfaces"] == "52:54:00:12:34:56 lan0\n"
    assert parsed["runcmd"][0][:2] == ["sh", "-c"]
    assert parsed["hostname"] == "router"
