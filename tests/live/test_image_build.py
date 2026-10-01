"""The lab image builds from its pinned recipe and passes the verification labctl up applies.

This runs a full unattended Rocky installation, about twenty minutes, from the cached ISO.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from sysadmin_lab.application.image_building import resolve_built_image
from sysadmin_lab.catalog import load_image_manifest
from sysadmin_lab.domain.images import sha256_file

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("LAL_RUN_IMAGE_BUILD") != "1",
        reason="set LAL_RUN_IMAGE_BUILD=1 to install Rocky into a fresh lab image",
    ),
]
ROOT = Path(__file__).parents[2]


def test_the_lab_image_builds_from_its_recipe_and_verifies() -> None:
    manifest_path = ROOT / "images" / "rocky-10.2" / "iso-manifest.yaml"
    manifest = load_image_manifest(manifest_path)
    output = ROOT / "runtime" / "image-build-test" / uuid4().hex  # QEMU must reach it
    labctl = Path(sys.executable).with_name("labctl")
    try:
        done = subprocess.run(
            [
                str(labctl),
                "image",
                "build",
                str(manifest_path),
                "--source-cache",
                str(ROOT / "runtime" / "cache" / "isos"),
                "--output-directory",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        assert f"built {manifest.image_id}:" in done.stdout

        built = resolve_built_image(manifest, manifest_path, output)
        assert built.record.image_id == manifest.image_id
        assert sha256_file(built.artifact) == built.record.artifact_sha256
        assert f"sha256:{built.record.artifact_sha256}" in done.stdout
        leftovers = subprocess.run(
            ["virsh", "--connect", "qemu:///system", "list", "--all", "--name"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert not [name for name in leftovers.split() if name.startswith("lal-image-")]
    finally:
        shutil.rmtree(output, ignore_errors=True)
