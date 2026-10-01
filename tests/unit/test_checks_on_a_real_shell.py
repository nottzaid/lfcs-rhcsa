"""The check providers' real commands, run on this machine instead of a guest.

A guest is reached over SSH, but what a check runs there is ordinary bash, stat, and
systemctl. Running those here proves the providers' commands and the host check library
behave as the checks claim, without a VM and without faking any output.
"""

from __future__ import annotations

import grp
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

from sysadmin_lab.adapters.guest_checks import (
    CommandCheckProvider,
    FileCheckProvider,
    ServiceCheckProvider,
)
from sysadmin_lab.application.guest_execution import GuestCommandResult, GuestEndpoint
from sysadmin_lab.domain.models import CheckKind, CheckSpec


class ThisMachine:
    """Runs a check's command locally, as if the guest were this host."""

    def run(
        self, endpoint: GuestEndpoint, arguments: tuple[str, ...], *, timeout_seconds: float
    ) -> GuestCommandResult:
        if arguments[:2] == ("sudo", "--"):
            arguments = arguments[2:]
        done = subprocess.run(
            arguments, capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
        return GuestCommandResult(done.returncode, done.stdout, done.stderr)


def endpoint(tmp_path: Path) -> GuestEndpoint:
    return GuestEndpoint("192.0.2.10", "labadmin", (tmp_path / "key").resolve())


def check(kind: CheckKind, parameters: dict[str, object]) -> CheckSpec:
    return CheckSpec(
        check_id="real", kind=kind, target="node1", description="real", parameters=parameters
    )


def run_command(tmp_path: Path, parameters: dict[str, object]) -> tuple[bool, str]:
    observation = CommandCheckProvider(ThisMachine()).evaluate(
        check(CheckKind.COMMAND, parameters), endpoint(tmp_path)
    )
    assert not observation.error, observation.message
    return observation.passed, observation.message


def test_scripts_use_the_host_library_and_state_their_reason(tmp_path: Path) -> None:
    config = tmp_path / "app.conf"
    config.write_text("BATCH_SIZE=5\n")
    script = f'expect_eq "$(sed -n "s/^BATCH_SIZE=//p" {config})" 50 "the batch size"'
    assert run_command(tmp_path, {"script": script}) == (
        False,
        "the batch size is 5; expected 50",
    )
    config.write_text("BATCH_SIZE=50\n")
    assert run_command(tmp_path, {"script": script}) == (True, "requirement met")

    flaky = tmp_path / "ready"
    waits = f"ready() {{ [[ -e {flaky} ]] || {{ touch {flaky}; false; }}; }}; retry 3 ready"
    assert run_command(tmp_path, {"script": waits}) == (True, "requirement met")

    silent = "echo 'nothing on stderr, so stdout explains'; exit 3"
    assert run_command(tmp_path, {"script": silent}) == (
        False,
        "nothing on stderr, so stdout explains",
    )


def test_argument_checks_compare_exit_status_and_output(tmp_path: Path) -> None:
    printf = shutil.which("printf") or "/usr/bin/printf"
    assert run_command(tmp_path, {"arguments": [printf, "ready\\n"], "stdout_equals": "ready"})[0]
    assert run_command(
        tmp_path, {"arguments": [printf, "not yet\\n"], "stdout_equals": "ready"}
    ) == (False, "found 'not yet'")
    assert run_command(tmp_path, {"arguments": [printf, ""], "stdout_equals": "ready"}) == (
        False,
        "found 'no output'",
    )
    assert run_command(
        tmp_path, {"arguments": [printf, "a\\nb ready c\\n"], "stdout_contains": "ready"}
    )[0]
    assert run_command(tmp_path, {"arguments": ["false"], "expected_exit_code": 1})[0]
    assert run_command(tmp_path, {"arguments": ["true"], "expected_exit_code": 1}) == (
        False,
        "exit status 0; expected 1",
    )


def test_file_checks_read_real_metadata_and_content(tmp_path: Path) -> None:
    owner = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o2770)
    shared.chmod(0o2770)
    plan = shared / "plan"
    plan.write_text("platform-plan\n")
    plan.chmod(0o640)
    provider = FileCheckProvider(ThisMachine())

    def observe(parameters: dict[str, object]) -> tuple[bool, str]:
        observation = provider.evaluate(check(CheckKind.FILE, parameters), endpoint(tmp_path))
        assert not observation.error, observation.message
        return observation.passed, observation.message

    assert observe(
        {"path": str(shared), "kind": "directory", "owner": owner, "group": group, "mode": "2770"}
    ) == (True, "requirement met")
    assert observe({"path": str(plan), "kind": "file", "mode": "0640", "contains": "plan"})[0]
    assert observe({"path": str(plan), "kind": "directory", "mode": "0600"}) == (
        False,
        f"{plan} is a regular file, not a directory; mode is 0640; expected 0600",
    )
    assert observe({"path": str(plan), "contains": "nothing like it"}) == (
        False,
        f"{plan} lacks the required content",
    )
    assert observe({"path": str(tmp_path / "archive.fill"), "exists": False}) == (
        True,
        "path is absent",
    )
    assert observe({"path": str(plan), "exists": False}) == (False, f"{plan} still exists")


@pytest.mark.skipif(not Path("/run/systemd/system").is_dir(), reason="needs a systemd host")
def test_service_checks_read_real_systemd_units(tmp_path: Path) -> None:
    provider = ServiceCheckProvider(ThisMachine())

    def observe(parameters: dict[str, object]) -> tuple[bool, str]:
        observation = provider.evaluate(check(CheckKind.SERVICE, parameters), endpoint(tmp_path))
        return observation.passed, observation.message

    assert observe({"name": "systemd-journald.service"}) == (True, "requirement met")
    assert observe({"name": "systemd-journald.service", "active": False}) == (
        False,
        "systemd-journald.service is active; expected stopped",
    )
    assert observe({"name": "systemd-journald.service", "enabled": True}) == (
        False,
        "systemd-journald.service is static; expected enabled at boot",
    )
    assert observe({"name": "lal-no-such-unit.service"}) == (
        False,
        "lal-no-such-unit.service is not installed or loadable",
    )
