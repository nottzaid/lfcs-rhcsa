from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, HttpUrl, model_validator

from sysadmin_lab.domain.models import StrictModel


class InstallationSource(StrictModel):
    kind: Literal["iso", "qcow2"]
    filename: str = Field(min_length=1)
    url: HttpUrl
    checksum_url: HttpUrl
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_https_sources(self) -> Self:
        if self.url.scheme != "https" or self.checksum_url.scheme != "https":
            raise ValueError("image sources and checksums must use HTTPS")
        return self


class ImageBuildSpec(StrictModel):
    method: Literal["kickstart", "cloud-image"]
    kickstart: str | None = Field(default=None, min_length=1)
    disk_mib: int = Field(ge=4096)
    memory_mib: int = Field(ge=1024)
    vcpus: int = Field(ge=1)
    firmware: Literal["uefi", "bios"] = "uefi"

    @model_validator(mode="after")
    def require_kickstart_for_kickstart_build(self) -> Self:
        if self.method == "kickstart" and self.kickstart is None:
            raise ValueError("kickstart builds require a kickstart template")
        if self.method == "cloud-image" and self.kickstart is not None:
            raise ValueError("cloud-image builds cannot specify a kickstart template")
        return self


class ImageManifest(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    image_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9.]+)*$")
    distribution: str = Field(min_length=1)
    release: str = Field(min_length=1)
    architecture: Literal["x86_64", "aarch64"]
    source: InstallationSource
    build: ImageBuildSpec

    @model_validator(mode="after")
    def source_matches_build_method(self) -> Self:
        expected_source = "iso" if self.build.method == "kickstart" else "qcow2"
        if self.source.kind != expected_source:
            raise ValueError(
                f"{self.build.method} builds require a {expected_source} installation source"
            )
        return self


class ImageVerificationError(ValueError):
    pass


def verify_installation_source(
    manifest: ImageManifest, path: Path, *, require_filename: bool = True
) -> str:
    if not path.is_file():
        raise ImageVerificationError(f"installation source is not a file: {path}")
    if require_filename and path.name != manifest.source.filename:
        raise ImageVerificationError(
            f"installation source filename mismatch: expected {manifest.source.filename}"
        )
    actual_size = path.stat().st_size
    if actual_size != manifest.source.size_bytes:
        raise ImageVerificationError(
            f"installation source size mismatch: expected {manifest.source.size_bytes}, "
            f"found {actual_size}"
        )

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_digest = digest.hexdigest()
    if actual_digest != manifest.source.sha256:
        raise ImageVerificationError(
            f"installation source checksum mismatch: expected {manifest.source.sha256}, "
            f"found {actual_digest}"
        )
    return actual_digest
