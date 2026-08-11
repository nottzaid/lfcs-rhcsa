from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sysadmin_lab.application.image_building import (
    ImageBuildError,
    KickstartImageBuilder,
    resolve_built_image,
)
from sysadmin_lab.domain.images import ImageManifest
from tests.unit.test_images import image_manifest


@dataclass
class FakeRunner:
    calls: list[tuple[list[str], float | None, bool]] = field(default_factory=list)

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> None:
        command = list(arguments)
        self.calls.append((command, timeout_seconds, check))
        if command[:3] == ["qemu-img", "create", "-f"]:
            Path(command[-2]).write_bytes(b"install disk")
        elif command[:3] == ["qemu-img", "convert", "-p"]:
            Path(command[-1]).write_bytes(b"normalized golden image")


def kickstart_manifest(payload: bytes, kickstart: str = "kickstart.ks") -> ImageManifest:
    raw = image_manifest(payload, filename="source.iso").model_dump(mode="json")
    raw["source"]["kind"] = "iso"
    raw["build"]["method"] = "kickstart"
    raw["build"]["kickstart"] = kickstart
    raw["build"]["firmware"] = "uefi"
    return ImageManifest.model_validate(raw)


def test_builder_publishes_artifact_and_provenance_atomically(tmp_path: Path) -> None:
    source = tmp_path / "source.iso"
    source.write_bytes(b"installer")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    (tmp_path / "kickstart.ks").write_text("text\nshutdown\n", encoding="utf-8")
    runner = FakeRunner()

    built = KickstartImageBuilder(runner).build(
        kickstart_manifest(b"installer"), manifest_path, source, tmp_path / "output"
    )

    assert built.artifact.read_bytes() == b"normalized golden image"
    assert built.artifact.stat().st_mode & 0o777 == 0o444
    assert built.record_path.is_file()
    assert built.record.source_sha256 == kickstart_manifest(b"installer").source.sha256
    virt_install = next(call[0] for call in runner.calls if call[0][0] == "virt-install")
    assert "--initrd-inject" in virt_install
    assert "inst.ks=file:/ks.cfg inst.text console=ttyS0,115200n8" in virt_install
    assert virt_install[-2:] == ["--boot", "uefi"]
    assert runner.calls[-2][0][-2] == "destroy"
    assert runner.calls[-1][0][-3] == "undefine"


def test_builder_refuses_unresolved_or_escaping_kickstart(tmp_path: Path) -> None:
    source = tmp_path / "source.iso"
    source.write_bytes(b"installer")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    outside = tmp_path.parent / "outside.ks"
    outside.write_text("shutdown", encoding="utf-8")
    with pytest.raises(ImageBuildError, match="remain beside"):
        KickstartImageBuilder(FakeRunner()).build(
            kickstart_manifest(b"installer", "../outside.ks"),
            manifest_path,
            source,
            tmp_path / "output",
        )

    (tmp_path / "kickstart.ks").write_text("@@SECRET@@", encoding="utf-8")
    with pytest.raises(ImageBuildError, match="unresolved placeholder"):
        KickstartImageBuilder(FakeRunner()).build(
            kickstart_manifest(b"installer"), manifest_path, source, tmp_path / "other-output"
        )


def test_builder_never_overwrites_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source.iso"
    source.write_bytes(b"installer")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    (tmp_path / "kickstart.ks").write_text("shutdown", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    (output / "rocky-10.2-test-v1.qcow2").write_bytes(b"owned")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        KickstartImageBuilder(FakeRunner()).build(
            kickstart_manifest(b"installer"), manifest_path, source, output
        )
    assert (output / "rocky-10.2-test-v1.qcow2").read_bytes() == b"owned"


def test_runtime_resolver_revalidates_artifact_and_recipe(tmp_path: Path) -> None:
    source = tmp_path / "source.iso"
    source.write_bytes(b"installer")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    kickstart = tmp_path / "kickstart.ks"
    kickstart.write_text("shutdown", encoding="utf-8")
    manifest = kickstart_manifest(b"installer")
    output = tmp_path / "output"
    built = KickstartImageBuilder(FakeRunner()).build(manifest, manifest_path, source, output)

    assert resolve_built_image(manifest, manifest_path, output) == built

    kickstart.write_text("shutdown\n# changed", encoding="utf-8")
    with pytest.raises(ImageBuildError, match="recipe provenance is stale"):
        resolve_built_image(manifest, manifest_path, output)
