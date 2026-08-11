from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import yaml

from sysadmin_lab.domain.models import DiskSpec
from sysadmin_lab.domain.resources import NAME_COMPONENT


class CommandRunner(Protocol):
    def run(self, arguments: Sequence[str]) -> None: ...


class SubprocessRunner:
    def run(self, arguments: Sequence[str]) -> None:
        subprocess.run(arguments, check=True, stdin=subprocess.DEVNULL)


@dataclass(frozen=True, slots=True)
class SessionPaths:
    directory: Path
    overlay: Path
    seed_iso: Path
    seed_source: Path
    private_key: Path
    public_key: Path
    data_disks: tuple[tuple[str, Path], ...] = ()


@dataclass(frozen=True, slots=True)
class GuestAccess:
    username: str
    password: str
    private_key: Path
    public_key: Path


class SessionArtifactBuilder:
    def __init__(self, runtime_root: Path, runner: CommandRunner) -> None:
        self._runtime_root = runtime_root.resolve()
        self._runner = runner

    def paths(self, session_id: UUID, role: str) -> SessionPaths:
        if not NAME_COMPONENT.fullmatch(role):
            raise ValueError(f"invalid role: {role}")
        directory = self._runtime_root / "sessions" / str(session_id) / role
        key_root = self._runtime_root / "keys"
        return SessionPaths(
            directory=directory,
            overlay=directory / "root.qcow2",
            seed_iso=directory / "seed.iso",
            seed_source=directory / "seed-source",
            private_key=key_root / "lab_ed25519",
            public_key=key_root / "lab_ed25519.pub",
        )

    def create(
        self,
        *,
        session_id: UUID,
        role: str,
        hostname: str,
        base_image: Path,
        disk_gib: int = 20,
        data_disks: tuple[DiskSpec, ...] = (),
    ) -> tuple[SessionPaths, GuestAccess]:
        paths = self.paths(session_id, role)
        if paths.directory.exists():
            raise FileExistsError(f"session artifacts already exist: {paths.directory}")
        if not base_image.is_file():
            raise FileNotFoundError(f"base image does not exist: {base_image}")
        if disk_gib < 10:
            raise ValueError("session disk must be at least 10 GiB")

        paths.directory.mkdir(parents=True)
        try:
            self._ensure_ssh_key(paths)
            password = secrets.token_urlsafe(18)
            public_key = paths.public_key.read_text(encoding="utf-8").strip()
            self._create_overlay(base_image.resolve(), paths.overlay, disk_gib)
            created_data_disks = self._create_data_disks(paths.directory, data_disks)
            self._create_seed(paths, hostname, "labadmin", password, public_key)
        except Exception:
            shutil.rmtree(paths.directory, ignore_errors=True)
            raise
        paths = SessionPaths(
            paths.directory,
            paths.overlay,
            paths.seed_iso,
            paths.seed_source,
            paths.private_key,
            paths.public_key,
            created_data_disks,
        )
        return paths, GuestAccess("labadmin", password, paths.private_key, paths.public_key)

    def _ensure_ssh_key(self, paths: SessionPaths) -> None:
        paths.private_key.parent.mkdir(parents=True, exist_ok=True)
        if paths.private_key.is_file() and paths.public_key.is_file():
            return
        if paths.private_key.exists() or paths.public_key.exists():
            raise RuntimeError("incomplete project SSH key pair")
        self._runner.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "linux-admin-lab",
                "-f",
                str(paths.private_key),
            ]
        )
        os.chmod(paths.private_key, 0o600)

    def _create_overlay(self, base_image: Path, overlay: Path, disk_gib: int) -> None:
        self._runner.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(base_image),
                "-o",
                "lazy_refcounts=on",
                str(overlay),
                f"{disk_gib}G",
            ]
        )

    def _create_data_disks(
        self, directory: Path, specifications: tuple[DiskSpec, ...]
    ) -> tuple[tuple[str, Path], ...]:
        created: list[tuple[str, Path]] = []
        for specification in specifications:
            path = directory / f"{specification.name}.qcow2"
            self._runner.run(
                [
                    "qemu-img",
                    "create",
                    "-f",
                    "qcow2",
                    "-o",
                    "lazy_refcounts=on",
                    str(path),
                    f"{specification.size_mib}M",
                ]
            )
            created.append((specification.name, path))
        return tuple(created)

    def _create_seed(
        self,
        paths: SessionPaths,
        hostname: str,
        username: str,
        password: str,
        public_key: str,
    ) -> None:
        paths.seed_source.mkdir()
        metadata = {"instance-id": paths.directory.parent.name, "local-hostname": hostname}
        userdata = {
            "hostname": hostname,
            # Scenarios may legitimately administer /etc/hosts.  Cloud-init still applies
            # the per-session hostname, but must not rewrite learner configuration after a
            # reboot and invalidate an otherwise persistent solution.
            "manage_etc_hosts": False,
            "ssh_pwauth": True,
            "users": [
                "default",
                {
                    "name": username,
                    "groups": ["wheel"],
                    "lock_passwd": False,
                    "plain_text_passwd": password,
                    "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
                    "ssh_authorized_keys": [public_key],
                },
            ],
            "chpasswd": {"expire": False},
            "runcmd": [["systemctl", "start", "qemu-guest-agent.service"]],
        }
        (paths.seed_source / "meta-data").write_text(
            yaml.safe_dump(metadata, sort_keys=True), encoding="utf-8"
        )
        (paths.seed_source / "user-data").write_text(
            "#cloud-config\n" + yaml.safe_dump(userdata, sort_keys=False), encoding="utf-8"
        )
        self._runner.run(
            [
                "xorriso",
                "-as",
                "mkisofs",
                "-quiet",
                "-output",
                str(paths.seed_iso),
                "-volid",
                "CIDATA",
                "-joliet",
                "-rock",
                str(paths.seed_source / "user-data"),
                str(paths.seed_source / "meta-data"),
            ]
        )

    def destroy(self, session_id: UUID, role: str) -> None:
        paths = self.paths(session_id, role)
        if paths.directory.parent.parent != self._runtime_root / "sessions":
            raise RuntimeError("refusing to remove artifacts outside the session root")
        shutil.rmtree(paths.directory, ignore_errors=True)
        with suppress(OSError):
            paths.directory.parent.rmdir()
