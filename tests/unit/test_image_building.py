from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sysadmin_lab.application.image_building import (
    ImageBuildError,
    KickstartImageBuilder,
    SubprocessBuildRunner,
    resolve_built_image,
)
from sysadmin_lab.domain.images import ImageManifest
from tests.unit.test_images import image_manifest

BuiltLab = tuple[ImageManifest, Path, Path]  # manifest, its path, the output directory


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


def build_into(tmp_path: Path) -> BuiltLab:
    source = tmp_path / "source.iso"
    source.write_bytes(b"installer")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    (tmp_path / "kickstart.ks").write_text("shutdown", encoding="utf-8")
    manifest = kickstart_manifest(b"installer")
    output = tmp_path / "output"
    KickstartImageBuilder(FakeRunner()).build(manifest, manifest_path, source, output)
    return manifest, manifest_path, output


def corrupt_the_record(manifest: ImageManifest, output: Path) -> ImageManifest:
    (output / f"{manifest.image_id}.build.json").write_text("{", encoding="utf-8")
    return manifest


def rename_in_the_record(manifest: ImageManifest, output: Path) -> ImageManifest:
    record_path = output / f"{manifest.image_id}.build.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["image_id"] = "rocky-10.2-other-v1"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return manifest


def change_the_artifact(content: bytes) -> Callable[[ImageManifest, Path], ImageManifest]:
    def change(manifest: ImageManifest, output: Path) -> ImageManifest:
        artifact = output / f"{manifest.image_id}.qcow2"
        artifact.chmod(0o644)
        artifact.write_bytes(content)
        return manifest

    return change


@pytest.mark.parametrize(
    ("tamper", "refusal"),
    [
        (lambda _manifest, _output: image_manifest(b"installer"), "only built Kickstart images"),
        (corrupt_the_record, "invalid built image provenance"),
        (rename_in_the_record, "identifier does not match its manifest"),
        (
            lambda _manifest, _output: kickstart_manifest(b"a newer installer"),
            "source provenance is stale",
        ),
        (change_the_artifact(b"normalized golden image, grown"), "size does not match"),
        (change_the_artifact(b"normalized golden IMAGE"), "checksum does not match"),
    ],
    ids=["cloud-image", "corrupt", "renamed", "new-iso", "grown", "altered"],
)
def test_runtime_resolver_refuses_an_image_its_provenance_does_not_vouch_for(
    tamper: Callable[[ImageManifest, Path], ImageManifest], refusal: str, tmp_path: Path
) -> None:
    manifest, manifest_path, output = build_into(tmp_path)
    with pytest.raises(ImageBuildError, match=refusal):
        resolve_built_image(tamper(manifest, output), manifest_path, output)


def test_builder_refuses_a_manifest_it_cannot_build_from(tmp_path: Path) -> None:
    source = tmp_path / "source.iso"
    source.write_bytes(b"installer")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    builder = KickstartImageBuilder(FakeRunner())

    with pytest.raises(ImageBuildError, match="requires a kickstart manifest"):
        builder.build(image_manifest(b"installer"), manifest_path, source, tmp_path / "out")
    with pytest.raises(ImageBuildError, match="cannot read kickstart template"):
        builder.build(kickstart_manifest(b"installer"), manifest_path, source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@dataclass
class FailingInstall(FakeRunner):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> None:
        super().run(arguments, timeout_seconds=timeout_seconds, check=check)
        if arguments[0] == "virt-install":
            raise subprocess.CalledProcessError(1, "virt-install")


def test_a_failed_install_publishes_nothing_and_removes_its_domain(tmp_path: Path) -> None:
    source = tmp_path / "source.iso"
    source.write_bytes(b"installer")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    (tmp_path / "kickstart.ks").write_text("shutdown", encoding="utf-8")
    raw = kickstart_manifest(b"installer").model_dump(mode="json")
    raw["build"]["firmware"] = "bios"
    manifest = ImageManifest.model_validate(raw)
    runner = FailingInstall()
    output = tmp_path / "output"

    with pytest.raises(ImageBuildError, match="golden image build failed"):
        KickstartImageBuilder(runner).build(manifest, manifest_path, source, output)

    assert list(output.iterdir()) == []  # neither an artifact nor the scratch directory
    virt_install = next(call[0] for call in runner.calls if call[0][0] == "virt-install")
    assert "--boot" not in virt_install  # BIOS firmware needs no boot override
    domain = virt_install[virt_install.index("--name") + 1]
    assert [call[0][-2:] for call in runner.calls[-2:]] == [
        ["destroy", domain],
        [domain, "--nvram"],
    ]
    assert all(not call[2] for call in runner.calls[-2:])  # cleanup tolerates a missing domain


def test_build_commands_run_with_the_systems_tools_not_the_projects_virtualenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # virt-install is a Python program; inside the lab's virtualenv it would import the
    # wrong interpreter's modules.
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("PATH", f"{tmp_path / '.venv' / 'bin'}:/usr/bin")
    seen = tmp_path / "environment"
    SubprocessBuildRunner().run(
        ["sh", "-c", f'echo "${{VIRTUAL_ENV-unset}} ${{PYTHONPATH-unset}} $PATH" > {seen}']
    )
    venv, pythonpath, path = seen.read_text(encoding="utf-8").split()
    assert (venv, pythonpath) == ("unset", "unset")
    assert ".venv" not in path and "/usr/sbin" in path.split(":")

    SubprocessBuildRunner().run(["false"], check=False)  # cleanup commands may fail quietly
    with pytest.raises(subprocess.CalledProcessError):
        SubprocessBuildRunner().run(["false"])
    with pytest.raises(subprocess.TimeoutExpired):
        SubprocessBuildRunner().run(["sleep", "5"], timeout_seconds=0.2)
