from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sysadmin_lab.domain.images import (
    ImageManifest,
    ImageVerificationError,
    verify_installation_source,
)


def image_manifest(payload: bytes, filename: str = "source.iso") -> ImageManifest:
    return ImageManifest.model_validate(
        {
            "image_id": "rocky-10.2-test-v1",
            "distribution": "Rocky Linux",
            "release": "10.2",
            "architecture": "x86_64",
            "source": {
                "kind": "qcow2",
                "filename": filename,
                "url": f"https://example.invalid/{filename}",
                "checksum_url": f"https://example.invalid/{filename}.CHECKSUM",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            "build": {
                "method": "cloud-image",
                "disk_mib": 4096,
                "memory_mib": 1024,
                "vcpus": 1,
            },
        }
    )


def test_image_manifest_rejects_mismatched_source_and_build() -> None:
    raw = image_manifest(b"image").model_dump(mode="json")
    raw["source"]["kind"] = "iso"
    with pytest.raises(ValueError, match="require a qcow2"):
        ImageManifest.model_validate(raw)


def test_kickstart_build_requires_template() -> None:
    raw = image_manifest(b"image").model_dump(mode="json")
    raw["source"]["kind"] = "iso"
    raw["build"]["method"] = "kickstart"
    with pytest.raises(ValueError, match="require a kickstart template"):
        ImageManifest.model_validate(raw)


def test_image_sources_require_https() -> None:
    raw = image_manifest(b"image").model_dump(mode="json")
    raw["source"]["url"] = "http://example.invalid/image.qcow2"
    with pytest.raises(ValueError, match="must use HTTPS"):
        ImageManifest.model_validate(raw)


def test_source_verification_accepts_exact_file(tmp_path: Path) -> None:
    payload = b"trusted installation media"
    path = tmp_path / "source.iso"
    path.write_bytes(payload)
    assert (
        verify_installation_source(image_manifest(payload), path)
        == hashlib.sha256(payload).hexdigest()
    )


@pytest.mark.parametrize("problem", ["missing", "name", "size", "digest"])
def test_source_verification_rejects_any_mismatch(tmp_path: Path, problem: str) -> None:
    payload = b"trusted installation media"
    path = tmp_path / ("wrong.iso" if problem == "name" else "source.iso")
    manifest = image_manifest(
        payload + b"x" if problem == "size" else payload,
        filename="source.iso",
    )
    if problem != "missing":
        path.write_bytes(b"tampered" if problem == "digest" else payload)
    with pytest.raises(ImageVerificationError):
        verify_installation_source(manifest, path)
