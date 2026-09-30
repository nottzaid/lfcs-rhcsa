from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import ValidationError

from sysadmin_lab.adapters.guest_checks import (
    CommandCheckProvider,
    CommandParameters,
    FileCheckProvider,
    FileParameters,
    ServiceCheckProvider,
    check_library,
)
from sysadmin_lab.application.guest_execution import (
    GuestCommandResult,
    GuestEndpoint,
)
from sysadmin_lab.domain.models import CheckKind, CheckSpec


@dataclass
class Executor:
    results: list[GuestCommandResult]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self,
        endpoint: GuestEndpoint,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GuestCommandResult:
        self.calls.append(arguments)
        return self.results.pop(0)


def endpoint(tmp_path: Path) -> GuestEndpoint:
    return GuestEndpoint("192.0.2.10", "labadmin", (tmp_path / "key").resolve())


def spec(kind: CheckKind, parameters: dict[str, object]) -> CheckSpec:
    return CheckSpec(
        check_id="state-check",
        kind=kind,
        target="node1",
        description="state",
        parameters=parameters,
    )


def result(exit_code: int, stdout: str = "", stderr: str = "") -> GuestCommandResult:
    return GuestCommandResult(exit_code, stdout, stderr)


def test_command_provider_checks_exit_stdout_and_sudo(tmp_path: Path) -> None:
    executor = Executor([result(0, "expected\n")])
    observation = CommandCheckProvider(executor).evaluate(
        spec(
            CheckKind.COMMAND,
            {"arguments": ["printf", "expected"], "stdout_equals": "expected", "sudo": True},
        ),
        endpoint(tmp_path),
    )
    assert observation.passed
    assert executor.calls == [("sudo", "--", "printf", "expected")]

    truncated = GuestCommandResult(0, "long", "", stdout_truncated=True)
    observation = CommandCheckProvider(Executor([truncated])).evaluate(
        spec(CheckKind.COMMAND, {"arguments": ["true"]}), endpoint(tmp_path)
    )
    assert observation.error


def test_command_parameters_reject_ambiguous_stdout_expectations() -> None:
    with pytest.raises(ValidationError, match="only one"):
        CommandParameters.model_validate(
            {"arguments": ["true"], "stdout_equals": "a", "stdout_contains": "a"}
        )


def test_file_provider_matches_metadata_and_content(tmp_path: Path) -> None:
    executor = Executor([result(0, "regular file|root|root|640\n"), result(0)])
    observation = FileCheckProvider(executor).evaluate(
        spec(
            CheckKind.FILE,
            {
                "path": "/etc/example.conf",
                "kind": "file",
                "owner": "root",
                "group": "root",
                "mode": "0640",
                "contains": "enabled=true",
            },
        ),
        endpoint(tmp_path),
    )
    assert observation.passed
    assert executor.calls[1] == (
        "sudo",
        "--",
        "grep",
        "--fixed-strings",
        "--quiet",
        "--",
        "enabled=true",
        "/etc/example.conf",
    )


def test_file_provider_handles_absence_mismatch_and_inspection_error(tmp_path: Path) -> None:
    absent = FileCheckProvider(Executor([result(1)])).evaluate(
        spec(CheckKind.FILE, {"path": "/missing", "exists": False}), endpoint(tmp_path)
    )
    assert absent.passed

    mismatch = FileCheckProvider(Executor([result(0, "directory|user|users|755\n")])).evaluate(
        spec(CheckKind.FILE, {"path": "/file", "kind": "file", "owner": "root"}),
        endpoint(tmp_path),
    )
    assert not mismatch.passed
    assert mismatch.message == (
        "/file is a directory, not a regular file; owner is user; expected root"
    )

    inspection = FileCheckProvider(
        Executor([result(0, "regular file|root|root|644\n"), result(2)])
    ).evaluate(spec(CheckKind.FILE, {"path": "/file", "contains": "text"}), endpoint(tmp_path))
    assert inspection.error


def test_file_parameters_reject_expectations_for_absent_path() -> None:
    with pytest.raises(ValidationError, match="absent file"):
        FileParameters.model_validate({"path": "/missing", "exists": False, "owner": "root"})


def test_service_provider_reports_actual_unit_state(tmp_path: Path) -> None:
    executor = Executor(
        [result(0, "LoadState=loaded\nActiveState=active\n"), result(0, "enabled\n")]
    )
    observation = ServiceCheckProvider(executor).evaluate(
        spec(CheckKind.SERVICE, {"name": "sshd.service", "active": True, "enabled": True}),
        endpoint(tmp_path),
    )
    assert observation.passed
    assert executor.calls[1] == ("systemctl", "is-enabled", "--", "sshd.service")

    journal = (
        "Starting app.service - App...\n"
        "app: /etc/app.conf: permission denied\n"
        "app.service: Main process exited, code=exited, status=1/FAILURE\n"
        "app.service: Failed with result 'exit-code'.\n"
        "Failed to start app.service - App.\n"
    )
    failing = ServiceCheckProvider(
        Executor(
            [
                result(0, "LoadState=loaded\nActiveState=failed\n"),
                result(0, journal),
                result(1, "disabled\n"),
            ]
        )
    ).evaluate(
        spec(CheckKind.SERVICE, {"name": "app.service", "active": True, "enabled": True}),
        endpoint(tmp_path),
    )
    assert not failing.passed
    assert failing.message == (
        "app.service is failed; expected active (its last message: app: /etc/app.conf: "
        "permission denied); app.service is disabled; expected enabled at boot"
    )

    unexplained = ServiceCheckProvider(
        Executor([result(0, "LoadState=loaded\nActiveState=failed\n"), result(1, "", "denied")])
    ).evaluate(spec(CheckKind.SERVICE, {"name": "app.service"}), endpoint(tmp_path))
    assert unexplained.message == "app.service is failed; expected active"

    missing = ServiceCheckProvider(Executor([result(0, "LoadState=not-found\n")])).evaluate(
        spec(CheckKind.SERVICE, {"name": "missing.service"}), endpoint(tmp_path)
    )
    assert not missing.passed and missing.message == "missing.service is not installed or loadable"


def test_script_checks_run_the_host_library_and_report_the_stated_reason(tmp_path: Path) -> None:
    executor = Executor([result(1, "", "noise\nUID is 4301; expected 4201\n")])
    observation = CommandCheckProvider(executor).evaluate(
        spec(CheckKind.COMMAND, {"script": 'expect_eq "$(id -u app)" 4201 UID', "sudo": True}),
        endpoint(tmp_path),
    )
    assert not observation.passed and not observation.error
    assert observation.message == "UID is 4301; expected 4201"
    sudo, separator, shell, flag, program = executor.calls[0]
    assert (sudo, separator, shell, flag) == ("sudo", "--", "bash", "-c")
    assert program.startswith(check_library())
    assert program.endswith('expect_eq "$(id -u app)" 4201 UID')


def test_command_failures_explain_what_was_observed(tmp_path: Path) -> None:
    mismatch = CommandCheckProvider(Executor([result(0, "Permissive\n")])).evaluate(
        spec(CheckKind.COMMAND, {"arguments": ["getenforce"], "stdout_equals": "Enforcing"}),
        endpoint(tmp_path),
    )
    assert mismatch.message == "found 'Permissive'"

    silent = CommandCheckProvider(Executor([result(0, "1234\n")])).evaluate(
        spec(CheckKind.COMMAND, {"arguments": ["pgrep", "spin"], "expected_exit_code": 1}),
        endpoint(tmp_path),
    )
    assert silent.message == "exit status 0; expected 1"

    stderr = CommandCheckProvider(
        Executor([result(255, "", "teamlead@node2: Permission denied (publickey).\n")])
    ).evaluate(spec(CheckKind.COMMAND, {"arguments": ["ssh", "node2", "true"]}), endpoint(tmp_path))
    assert stderr.message == "teamlead@node2: Permission denied (publickey)."


def test_command_parameters_require_exactly_one_program() -> None:
    with pytest.raises(ValidationError, match="exactly one of arguments or script"):
        CommandParameters.model_validate({"arguments": ["true"], "script": "true"})
    with pytest.raises(ValidationError, match="exactly one of arguments or script"):
        CommandParameters.model_validate({})


def library(script: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"{check_library()}\n{script}"],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_library_reads_sysctl_d_syntax_like_systemd_sysctl() -> None:
    config = (
        "# /usr/lib/sysctl.d/50-default.conf\n"
        "net.ipv4.ip_forward = 0\n"
        "; comment\n"
        "# /etc/sysctl.d/90-gateway.conf\n"
        "-net/ipv4/ip_forward=1\n"
        "  fs.inotify.max_user_watches   =   524288  \n"
    )
    forwarding = library("sysctl_assignment net.ipv4.ip_forward", config)
    assert forwarding.stdout == "1\n"
    watches = library("sysctl_assignment fs.inotify.max_user_watches", config)
    assert watches.stdout == "524288\n"
    assert library("sysctl_assignment vm.swappiness", config).stdout == ""


def test_library_parses_mount_options_and_states_failures() -> None:
    assert library("has_option defaults,_netdev,pri=50 _netdev").returncode == 0
    assert library("has_option defaults,_netdevx _netdev").returncode == 1
    assert library("has_option rw,pri=50 pri").returncode == 0
    assert library("option_value size=64m,mode=0750,size=96m size").stdout == "96m"
    failed = library("expect_eq 4301 4201 UID")
    assert failed.returncode == 1
    assert failed.stderr == "UID is 4301; expected 4201\n"
    assert library("expect_eq '' 4201 UID").stderr == "UID is empty; expected 4201\n"


def test_command_and_file_failures_name_the_unmet_requirement(tmp_path: Path) -> None:
    missing_text = CommandCheckProvider(Executor([result(0, "other\n")])).evaluate(
        spec(CheckKind.COMMAND, {"arguments": ["cat", "/etc/motd"], "stdout_contains": "hi"}),
        endpoint(tmp_path),
    )
    assert missing_text.message == "the output did not contain the expected text"

    script_stdout = CommandCheckProvider(
        Executor([result(1, "no route to 198.51.100.2\n")])
    ).evaluate(spec(CheckKind.COMMAND, {"script": "ping -c1 198.51.100.2"}), endpoint(tmp_path))
    assert script_stdout.message == "no route to 198.51.100.2"

    present = FileCheckProvider(Executor([result(0, "regular file|root|root|644\n")])).evaluate(
        spec(CheckKind.FILE, {"path": "/srv/archive.fill", "exists": False}), endpoint(tmp_path)
    )
    assert not present.passed and present.message == "/srv/archive.fill still exists"

    absent = FileCheckProvider(Executor([result(1)])).evaluate(
        spec(CheckKind.FILE, {"path": "/srv/ready"}), endpoint(tmp_path)
    )
    assert absent.message == "/srv/ready does not exist"

    wrong = FileCheckProvider(
        Executor([result(0, "directory|root|wheel|755\n"), result(1)])
    ).evaluate(
        spec(
            CheckKind.FILE,
            {"path": "/srv/data", "group": "archivists", "mode": "2770", "contains": "x"},
        ),
        endpoint(tmp_path),
    )
    assert wrong.message == (
        "group is wheel; expected archivists; mode is 0755; expected 2770; "
        "/srv/data lacks the required content"
    )

    long_line = "x" * 400
    clipped = CommandCheckProvider(Executor([result(1, "", long_line)])).evaluate(
        spec(CheckKind.COMMAND, {"arguments": ["false"]}), endpoint(tmp_path)
    )
    assert len(clipped.message) == 240 and clipped.message.endswith("…")
