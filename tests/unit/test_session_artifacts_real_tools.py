"""Session artifacts built with the real tools every lab host already has.

qemu-img, xorriso, and ssh-keygen do the work here, so the test inspects what a VM would
really receive: an overlay backed by the base image, the declared data disks, and a seed
ISO whose cloud-init configuration names the host and carries the lab's key.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from sysadmin_lab.application.session_artifacts import SessionArtifactBuilder, SubprocessRunner
from sysadmin_lab.domain.models import DiskSpec

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("qemu-img", "xorriso", "ssh-keygen")),
    reason="needs qemu-img, xorriso, and ssh-keygen, which every lab host has",
)

SESSION = UUID("30000000-0000-4000-8000-000000000003")


def image_info(path: Path) -> dict[str, object]:
    output = subprocess.run(
        ["qemu-img", "info", "--output=json", "-U", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return dict(json.loads(output))


def iso_file(iso: Path, name: str) -> str:
    extracted = iso.parent / f"extracted-{name}"
    subprocess.run(
        ["xorriso", "-indev", str(iso), "-osirrox", "on", "-extract", f"/{name}", str(extracted)],
        capture_output=True,
        check=True,
    )
    return extracted.read_text()


def test_a_session_gets_an_overlay_disks_and_a_seed_that_name_its_host(tmp_path: Path) -> None:
    base = tmp_path / "base.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(base), "1G"], check=True)
    builder = SessionArtifactBuilder(tmp_path / "runtime", SubprocessRunner())

    paths, access = builder.create(
        session_id=SESSION,
        role="node2",
        hostname="node2",
        base_image=base,
        data_disks=(DiskSpec(name="records", size_mib=64, role="records disk"),),
    )

    overlay = image_info(paths.overlay)
    assert overlay["backing-filename"] == str(base.resolve())
    assert overlay["virtual-size"] == 20 * 1024**3
    ((name, disk),) = paths.data_disks
    assert name == "records" and image_info(disk)["virtual-size"] == 64 * 1024**2

    assert stat.S_IMODE(access.private_key.stat().st_mode) == 0o600
    public_key = access.public_key.read_text().strip()
    user_data = iso_file(paths.seed_iso, "user-data")
    assert user_data.startswith("#cloud-config")
    config = yaml.safe_load(user_data)
    assert config["hostname"] == "node2"
    (labadmin,) = [user for user in config["users"] if isinstance(user, dict)]
    assert labadmin["name"] == access.username
    assert labadmin["ssh_authorized_keys"] == [public_key]
    assert yaml.safe_load(iso_file(paths.seed_iso, "meta-data"))["local-hostname"] == "node2"

    with pytest.raises(FileExistsError, match="session artifacts already exist"):
        builder.create(session_id=SESSION, role="node2", hostname="node2", base_image=base)

    _other, again = builder.create(
        session_id=SESSION, role="node1", hostname="node1", base_image=base
    )
    assert again.public_key.read_text().strip() == public_key  # one lab key, reused


def test_a_session_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    base = tmp_path / "base.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(base), "1G"], check=True)
    builder = SessionArtifactBuilder(tmp_path / "runtime", SubprocessRunner())
    with pytest.raises(FileNotFoundError, match="base image does not exist"):
        builder.create(
            session_id=SESSION, role="node1", hostname="node1", base_image=tmp_path / "missing"
        )
    with pytest.raises(ValueError, match="at least 10 GiB"):
        builder.create(
            session_id=SESSION, role="node1", hostname="node1", base_image=base, disk_gib=9
        )
    assert not (tmp_path / "runtime" / "sessions").exists()

    keys = tmp_path / "runtime" / "keys"
    keys.mkdir(parents=True)
    (keys / "lab_ed25519").write_text("half a key pair")
    with pytest.raises(RuntimeError, match="incomplete project SSH key pair"):
        builder.create(session_id=SESSION, role="node1", hostname="node1", base_image=base)
    assert not builder.paths(SESSION, "node1").directory.exists()  # cleaned up
