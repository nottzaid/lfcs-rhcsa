from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sysadmin_lab.domain.images import (
    BuiltImageRecord,
    ImageManifest,
    image_recipe_sha256,
    sha256_file,
    verify_installation_source,
)


class ImageBuildError(RuntimeError):
    pass


class BuildCommandRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> None: ...


class SubprocessBuildRunner:
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> None:
        environment = os.environ.copy()
        environment.pop("VIRTUAL_ENV", None)
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        subprocess.run(
            arguments,
            check=check,
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class BuiltImage:
    artifact: Path
    record_path: Path
    record: BuiltImageRecord


class KickstartImageBuilder:
    """Build an immutable qcow2 from checksum-pinned installation media."""

    def __init__(self, runner: BuildCommandRunner, *, connection_uri: str = "qemu:///system"):
        self._runner = runner
        self._connection_uri = connection_uri

    def build(
        self,
        manifest: ImageManifest,
        manifest_path: Path,
        installation_source: Path,
        output_directory: Path,
    ) -> BuiltImage:
        if manifest.build.method != "kickstart" or manifest.build.kickstart is None:
            raise ImageBuildError("image build requires a kickstart manifest")
        source_digest = verify_installation_source(manifest, installation_source)
        template_path = (manifest_path.resolve().parent / manifest.build.kickstart).resolve()
        if template_path.parent != manifest_path.resolve().parent:
            raise ImageBuildError("kickstart template must remain beside its image manifest")
        try:
            kickstart = template_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ImageBuildError(f"cannot read kickstart template: {template_path}") from exc
        if "@@" in kickstart:
            raise ImageBuildError("kickstart template contains an unresolved placeholder")

        recipe_digest = image_recipe_sha256(manifest, kickstart)
        output_directory = output_directory.resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        artifact = output_directory / f"{manifest.image_id}.qcow2"
        record_path = output_directory / f"{manifest.image_id}.build.json"
        if artifact.exists() or record_path.exists():
            raise FileExistsError(f"refusing to overwrite built image: {artifact}")

        token = uuid4().hex[:12]
        domain_name = f"lal-image-{token}"
        temporary_root = Path(
            tempfile.mkdtemp(prefix=f".{manifest.image_id}-", dir=output_directory)
        )
        # system libvirt changes disk ownership to its unprivileged QEMU account, which
        # still needs search permission through this exact short-lived build directory.
        os.chmod(temporary_root, 0o711)
        install_disk = temporary_root / "install.qcow2"
        kickstart_path = temporary_root / "ks.cfg"
        converted = temporary_root / "published.qcow2"
        try:
            kickstart_path.write_text(kickstart, encoding="utf-8")
            self._runner.run(
                [
                    "qemu-img",
                    "create",
                    "-f",
                    "qcow2",
                    "-o",
                    "compat=1.1,lazy_refcounts=on",
                    str(install_disk),
                    f"{manifest.build.disk_mib}M",
                ]
            )
            self._runner.run(
                self._virt_install_command(
                    manifest,
                    installation_source.resolve(),
                    install_disk,
                    kickstart_path,
                    domain_name,
                ),
                timeout_seconds=90 * 60,
            )
            self._runner.run(
                [
                    "qemu-img",
                    "convert",
                    "-p",
                    "-O",
                    "qcow2",
                    "-o",
                    "compat=1.1,lazy_refcounts=on",
                    str(install_disk),
                    str(converted),
                ]
            )
            os.chmod(converted, 0o444)
            artifact_digest = sha256_file(converted)
            record = BuiltImageRecord.create(
                image_id=manifest.image_id,
                source_sha256=source_digest,
                recipe_sha256=recipe_digest,
                artifact_sha256=artifact_digest,
                artifact_size_bytes=converted.stat().st_size,
            )
            record_temporary = temporary_root / "build.json"
            record_temporary.write_text(
                json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(converted, artifact)
            os.replace(record_temporary, record_path)
            return BuiltImage(artifact, record_path, record)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ImageBuildError(f"golden image build failed: {exc}") from exc
        finally:
            self._cleanup_domain(domain_name)
            shutil.rmtree(temporary_root, ignore_errors=True)

    def _virt_install_command(
        self,
        manifest: ImageManifest,
        installation_source: Path,
        install_disk: Path,
        kickstart_path: Path,
        domain_name: str,
    ) -> list[str]:
        command = [
            "virt-install",
            "--connect",
            self._connection_uri,
            "--name",
            domain_name,
            "--memory",
            str(manifest.build.memory_mib),
            "--vcpus",
            str(manifest.build.vcpus),
            "--cpu",
            "host-passthrough",
            "--osinfo",
            "rocky10",
            "--disk",
            f"path={install_disk},format=qcow2,bus=virtio,cache=none",
            "--network",
            "network=default,model=virtio",
            "--graphics",
            "none",
            "--location",
            str(installation_source),
            "--initrd-inject",
            str(kickstart_path),
            "--extra-args",
            "inst.ks=file:/ks.cfg inst.text console=ttyS0,115200n8",
            "--noautoconsole",
            "--noreboot",
            "--wait=-1",
        ]
        if manifest.build.firmware == "uefi":
            command.extend(["--boot", "uefi"])
        return command

    def _cleanup_domain(self, domain_name: str) -> None:
        self._runner.run(
            ["virsh", "--connect", self._connection_uri, "destroy", domain_name], check=False
        )
        self._runner.run(
            [
                "virsh",
                "--connect",
                self._connection_uri,
                "undefine",
                domain_name,
                "--nvram",
            ],
            check=False,
        )


def resolve_built_image(
    manifest: ImageManifest, manifest_path: Path, image_directory: Path
) -> BuiltImage:
    if manifest.build.method != "kickstart" or manifest.build.kickstart is None:
        raise ImageBuildError("only built Kickstart images have provenance records")
    artifact = image_directory.resolve() / f"{manifest.image_id}.qcow2"
    record_path = image_directory.resolve() / f"{manifest.image_id}.build.json"
    if not artifact.is_file() or not record_path.is_file():
        raise ImageBuildError(f"built image is missing; run labctl image build {manifest_path}")
    try:
        record = BuiltImageRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
        kickstart = (manifest_path.resolve().parent / manifest.build.kickstart).read_text(
            encoding="utf-8"
        )
    except (OSError, ValueError) as exc:
        raise ImageBuildError(f"invalid built image provenance: {exc}") from exc
    expected_recipe = image_recipe_sha256(manifest, kickstart)
    if record.image_id != manifest.image_id:
        raise ImageBuildError("built image identifier does not match its manifest")
    if record.source_sha256 != manifest.source.sha256:
        raise ImageBuildError("built image source provenance is stale")
    if record.recipe_sha256 != expected_recipe:
        raise ImageBuildError("built image recipe provenance is stale")
    if artifact.stat().st_size != record.artifact_size_bytes:
        raise ImageBuildError("built image size does not match its provenance")
    if sha256_file(artifact) != record.artifact_sha256:
        raise ImageBuildError("built image checksum does not match its provenance")
    return BuiltImage(artifact, record_path, record)
