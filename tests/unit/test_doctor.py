"""labctl doctor's findings, from real directories, ACLs, and libvirt's own capability XML."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sysadmin_lab.application.doctor import (
    Severity,
    can_traverse,
    firewall_finding,
    image_finding,
    kvm_finding,
    libvirt_findings,
    nested_finding,
    qemu_identity,
    runtime_access_finding,
    session_finding,
    tool_findings,
    unit_is_active,
)

QEMU_UID = 65534  # nobody: some other user, as QEMU is
CAPABILITIES = """<capabilities><host>
  <secmodel><model>selinux</model><doi>0</doi>
    <baselabel type='kvm'>system_u:system_r:svirt_t:s0</baselabel></secmodel>
  <secmodel><model>dac</model><doi>0</doi>
    <baselabel type='kvm'>+952:+952</baselabel>
    <baselabel type='qemu'>+952:+952</baselabel></secmodel>
</host></capabilities>"""

needs_acl_tools = pytest.mark.skipif(
    shutil.which("setfacl") is None or shutil.which("getfacl") is None,
    reason="needs setfacl and getfacl",
)


def test_libvirt_says_which_user_runs_qemu() -> None:
    assert qemu_identity(CAPABILITIES) == (952, 952)
    assert qemu_identity("<capabilities><host/></capabilities>") is None


def test_directory_modes_decide_whether_another_user_gets_through(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    assert not can_traverse(private, QEMU_UID, {QEMU_UID})
    private.chmod(0o711)
    assert can_traverse(private, QEMU_UID, {QEMU_UID})
    assert can_traverse(private, os.getuid(), set())  # the owner
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o750)
    assert can_traverse(shared, QEMU_UID, {os.getgid()})  # through the owning group
    assert not can_traverse(shared, QEMU_UID, {QEMU_UID})


@needs_acl_tools
def test_an_acl_entry_lets_one_user_through_unless_the_mask_forbids_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o710)
    subprocess.run(["setfacl", "-m", f"u:{QEMU_UID}:x", str(home)], check=True)
    assert can_traverse(home, QEMU_UID, {QEMU_UID})
    subprocess.run(["setfacl", "-m", "m::-", str(home)], check=True)
    assert not can_traverse(home, QEMU_UID, {QEMU_UID})


def test_the_first_blocking_directory_is_named_with_its_fix(tmp_path: Path) -> None:
    uid, gid = os.getuid(), os.getgid()  # every directory above tmp_path lets its owner in
    gate = tmp_path / "projects"
    runtime = gate / "lab" / "runtime"
    runtime.mkdir(parents=True)
    gate.chmod(0o600)  # not even the owner may enter
    try:
        finding = runtime_access_finding(runtime, uid, gid)
    finally:
        gate.chmod(0o700)
    assert finding.severity is Severity.FAIL
    assert str(gate) in finding.detail
    assert finding.fix == f"setfacl -m u:{uid}:x {gate}"
    assert runtime_access_finding(runtime, uid, gid).severity is Severity.OK
    not_created = runtime / "not-created-yet"
    assert runtime_access_finding(not_created, uid, gid).severity is Severity.OK


def test_host_level_findings(tmp_path: Path) -> None:
    assert kvm_finding(tmp_path / "kvm").severity is Severity.FAIL
    unusable = tmp_path / "kvm-locked"
    unusable.touch(mode=0o000)
    unusable.chmod(0o000)
    assert kvm_finding(unusable).fix == "join the kvm group"

    for value, severity in (("Y", Severity.OK), ("N", Severity.WARN)):
        flag = tmp_path / value / "kvm_amd" / "parameters" / "nested"
        flag.parent.mkdir(parents=True)
        flag.write_text(f"{value}\n")
        assert nested_finding(tmp_path / value).severity is severity
    assert nested_finding(tmp_path / "no-kvm").severity is Severity.WARN

    missing = tool_findings(lambda name: None if name == "xorriso" else f"/usr/bin/{name}")
    assert [(f.severity, f.detail) for f in missing] == [
        (Severity.FAIL, "xorriso is not installed")
    ]
    assert tool_findings(lambda name: f"/usr/bin/{name}")[0].severity is Severity.OK

    assert firewall_finding(lambda unit: unit == "ufw.service").severity is Severity.WARN
    assert firewall_finding(lambda _unit: False).severity is Severity.OK


def test_libvirt_findings_report_what_blocks_the_lab(tmp_path: Path) -> None:
    class Network:
        def __init__(self, active: bool) -> None:
            self.active = active

        def isActive(self) -> int:
            return int(self.active)

    class Connection:
        def __init__(self, network: Network | None) -> None:
            self.network = network
            self.closed = False

        def networkLookupByName(self, name: str) -> Network:
            if self.network is None:
                raise LookupError(name)
            return self.network

        def getCapabilities(self) -> str:
            return CAPABILITIES.replace("+952:+952", f"+{os.getuid()}:+{os.getgid()}")

        def close(self) -> None:
            self.closed = True

    def refuse() -> object:
        raise PermissionError("authentication unavailable")

    (failed,) = libvirt_findings(refuse, tmp_path)
    assert failed.severity is Severity.FAIL and "authentication unavailable" in failed.detail

    healthy = Connection(Network(active=True))
    findings = libvirt_findings(lambda: healthy, tmp_path)
    assert [f.severity for f in findings] == [Severity.OK, Severity.OK, Severity.OK]
    assert healthy.closed

    stopped = libvirt_findings(lambda: Connection(Network(active=False)), tmp_path)
    assert stopped[1].fix == "sudo virsh net-start default && sudo virsh net-autostart default"
    assert libvirt_findings(lambda: Connection(None), tmp_path)[1].severity is Severity.FAIL


def test_image_and_session_findings() -> None:
    def missing() -> Path:
        raise FileNotFoundError("built image is missing")

    assert image_finding(missing).severity is Severity.WARN
    assert image_finding(lambda: Path("/images/lab.qcow2")).detail == (
        "lab.qcow2 matches its build record"
    )
    clean = session_finding([("1", "destroyed", "local-account-repair")])
    assert clean.severity is Severity.OK
    left = session_finding([("2", "ready", "nfs-client-recovery"), ("3", "failed", "lvm-x")])
    assert left.severity is Severity.WARN
    assert (
        left.detail == "2 sessions were not destroyed: nfs-client-recovery (ready), lvm-x (failed)"
    )


def test_edge_cases_of_users_acls_and_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usable = tmp_path / "kvm"
    usable.touch()
    assert kvm_finding(usable).severity is Severity.OK

    no_kvm_label = CAPABILITIES.replace("type='kvm'>+952", "type='other'>+952")
    assert qemu_identity(no_kvm_label) is None

    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(0o700)
    assert can_traverse(closed, 0, set())  # root needs no permission

    monkeypatch.setattr("sysadmin_lab.application.doctor.shutil.which", lambda _name: None)
    assert not can_traverse(closed, QEMU_UID, {QEMU_UID})  # mode bits alone, without getfacl
    monkeypatch.undo()

    unknown_uid = 3_999_999_999  # no such user: only the uid's own group counts
    assert runtime_access_finding(tmp_path, unknown_uid, unknown_uid).subject == "QEMU access"

    assert unit_is_active("lal-no-such-unit.service") is False
    if Path("/run/systemd/system").is_dir():
        assert unit_is_active("systemd-journald.service") is True


def test_libvirt_that_hides_the_qemu_user_is_reported_as_a_warning(tmp_path: Path) -> None:
    class Connection:
        def networkLookupByName(self, _name: str) -> object:
            return type("Network", (), {"isActive": lambda self: 1})()

        def getCapabilities(self) -> str:
            return "<capabilities><host/></capabilities>"

        def close(self) -> None:
            pass

    findings = libvirt_findings(Connection, tmp_path)
    assert findings[-1].severity is Severity.WARN
    assert findings[-1].detail == "libvirt does not say which user runs QEMU"
