"""labctl doctor on a working lab host finds nothing that blocks the lab."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("LAL_RUN_LIVE") != "1", reason="set LAL_RUN_LIVE=1 on a lab host"
    ),
]


def test_doctor_passes_on_a_working_lab_host() -> None:
    root = Path(__file__).parents[2]
    labctl = Path(sys.executable).with_name("labctl")
    done = subprocess.run(
        [str(labctl), "doctor", "--project-root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout
    lines = done.stdout.splitlines()
    assert not [line for line in lines if line.startswith("fail")]
    for subject in ("KVM", "libvirt", "default network", "QEMU access", "lab image"):
        assert any(line.startswith("ok") and f" {subject}:" in line for line in lines), subject
