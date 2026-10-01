"""Read-only diagnosis of everything a lab host needs, with the fix for each problem."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Severity(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Finding:
    subject: str
    severity: Severity
    detail: str
    fix: str = ""


TOOLS = {
    "qemu-img": "qemu-img (QEMU)",
    "xorriso": "xorriso",
    "ssh": "openssh",
    "ssh-keygen": "openssh",
    "virt-install": "virt-install, to build the lab image",
}


def kvm_finding(device: Path = Path("/dev/kvm")) -> Finding:
    if not device.exists():
        return Finding(
            "KVM",
            Severity.FAIL,
            f"{device} does not exist",
            "enable virtualization in the firmware and load the kvm_intel or kvm_amd module",
        )
    if not os.access(device, os.R_OK | os.W_OK):
        return Finding("KVM", Severity.FAIL, f"{device} is not usable by you", "join the kvm group")
    return Finding("KVM", Severity.OK, f"{device} is usable")


def nested_finding(parameters: Path = Path("/sys/module")) -> Finding:
    for module in ("kvm_intel", "kvm_amd"):
        flag = parameters / module / "parameters" / "nested"
        if flag.exists():
            if flag.read_text().strip() in {"Y", "1"}:
                return Finding("nested KVM", Severity.OK, f"{module} allows nested guests")
            return Finding(
                "nested KVM",
                Severity.WARN,
                f"{module} has nested virtualization off; libvirt labs will emulate their guest",
                f"set options {module} nested=1 in /etc/modprobe.d and reload the module",
            )
    return Finding("nested KVM", Severity.WARN, "no KVM module reports nested virtualization")


def tool_findings(which: Callable[[str], str | None] = shutil.which) -> tuple[Finding, ...]:
    missing = [name for name in TOOLS if which(name) is None]
    if not missing:
        return (Finding("tools", Severity.OK, "every command the lab runs is installed"),)
    return tuple(
        Finding("tools", Severity.FAIL, f"{name} is not installed", f"install {TOOLS[name]}")
        for name in missing
    )


def qemu_identity(capabilities_xml: str) -> tuple[int, int] | None:
    """The uid and gid libvirt runs QEMU as, from its DAC security model."""
    root = ET.fromstring(capabilities_xml)
    for secmodel in root.iter("secmodel"):
        if secmodel.findtext("model") != "dac":
            continue
        for label in secmodel.findall("baselabel"):
            if label.get("type") == "kvm" and label.text:
                user, _, group = label.text.strip().partition(":")
                return int(user.lstrip("+")), int(group.lstrip("+"))
    return None


def _acl_entries(directory: Path) -> dict[str, str] | None:
    """A directory's access ACL entries by tag, or None without getfacl."""
    if shutil.which("getfacl") is None:
        return None
    listing = subprocess.run(
        ["getfacl", "--numeric", "--omit-header", "--absolute-names", str(directory)],
        capture_output=True,
        text=True,
        check=False,
    )
    entries = {}
    for line in listing.stdout.splitlines():
        if line and not line.startswith(("#", "default:")):
            tag, _, perms = line.rpartition(":")
            entries[tag] = perms[:3]
    return entries


def can_traverse(directory: Path, uid: int, gids: set[int]) -> bool:
    """Whether uid may enter directory, by the POSIX ACL access check algorithm."""
    if uid == 0:
        return True
    info = directory.stat()
    if info.st_uid == uid:
        return bool(info.st_mode & 0o100)
    entries = _acl_entries(directory) or {}
    extended = "mask:" in entries
    if extended and f"user:{uid}" in entries:
        return entries[f"user:{uid}"][2] == "x" and entries["mask:"][2] == "x"
    matching = []
    if info.st_gid in gids:
        matching.append(entries["group:"] if extended else "x" if info.st_mode & 0o010 else "-")
    matching += [entries[f"group:{gid}"] for gid in gids if extended and f"group:{gid}" in entries]
    if matching:
        return any(perms[-1] == "x" for perms in matching) and (
            not extended or entries["mask:"][2] == "x"
        )
    return bool(info.st_mode & 0o001)


def _deepest_visible(path: Path) -> Path:
    """The deepest existing directory on path that the current user can see."""

    def visible(candidate: Path) -> bool:
        try:
            return candidate.exists()
        except OSError:  # a closed directory above it
            return False

    return next(filter(visible, (path, *path.parents)))  # the parents end at /, or at .


def runtime_access_finding(runtime_root: Path, uid: int, gid: int) -> Finding:
    """Every directory down to the runtime must let QEMU through to the session disks."""
    try:
        gids = set(os.getgrouplist(pwd.getpwuid(uid).pw_name, gid))
    except KeyError:
        gids = {gid}
    target = runtime_root.resolve()
    for directory in reversed((_deepest_visible(target), *_deepest_visible(target).parents)):
        if not can_traverse(directory, uid, gids):
            return Finding(
                "QEMU access",
                Severity.FAIL,
                f"QEMU (uid {uid}) cannot enter {directory}, so it cannot open session disks",
                f"setfacl -m u:{uid}:x {directory}",
            )
    return Finding("QEMU access", Severity.OK, f"QEMU (uid {uid}) can reach {target}")


def firewall_finding(is_active: Callable[[str], bool]) -> Finding:
    if is_active("ufw.service"):
        return Finding(
            "host firewall",
            Severity.WARN,
            "ufw is active; if it drops DNS from guests, their logins and services stall",
            "sudo ufw allow in on virbr0 to any port 53",
        )
    return Finding("host firewall", Severity.OK, "ufw is not active")


def unit_is_active(unit: str) -> bool:
    return (
        subprocess.run(
            ["systemctl", "is-active", "--quiet", unit], check=False, stdin=subprocess.DEVNULL
        ).returncode
        == 0
    )


def libvirt_findings(
    open_connection: Callable[[], object], runtime_root: Path
) -> tuple[Finding, ...]:
    """Reach system libvirt, its default network, and QEMU's view of the runtime."""
    try:
        connection: Any = open_connection()
    except Exception as exc:
        return (
            Finding(
                "libvirt",
                Severity.FAIL,
                f"cannot connect to qemu:///system: {exc}",
                "start virtqemud.socket (or libvirtd) and join the libvirt group",
            ),
        )
    try:
        findings = [Finding("libvirt", Severity.OK, "connected to qemu:///system")]
        try:
            active = bool(connection.networkLookupByName("default").isActive())
        except Exception:
            findings.append(
                Finding(
                    "default network",
                    Severity.FAIL,
                    "libvirt has no network called default; lab machines are reached through it",
                    "install your distribution's libvirt default network configuration",
                )
            )
        else:
            findings.append(
                Finding("default network", Severity.OK, "libvirt's default network is running")
                if active
                else Finding(
                    "default network",
                    Severity.FAIL,
                    "libvirt's default network is not running",
                    "sudo virsh net-start default && sudo virsh net-autostart default",
                )
            )
        identity = qemu_identity(connection.getCapabilities())
        if identity is None:
            findings.append(
                Finding("QEMU access", Severity.WARN, "libvirt does not say which user runs QEMU")
            )
        else:
            findings.append(runtime_access_finding(runtime_root, *identity))
        return tuple(findings)
    finally:
        connection.close()


def image_finding(resolve: Callable[[], Path]) -> Finding:
    try:
        artifact = resolve()
    except Exception as exc:
        return Finding(
            "lab image",
            Severity.WARN,
            f"not ready ({exc})",
            "./lab downloads the pinned Rocky ISO (about 10 GB) and builds it on first launch",
        )
    return Finding("lab image", Severity.OK, f"{artifact.name} matches its build record")


def session_finding(states: Iterable[tuple[str, str, str]]) -> Finding:
    """Sessions that were never destroyed, as (session id, status, scenario id)."""
    leftovers = [state for state in states if state[1] != "destroyed"]
    if not leftovers:
        return Finding("sessions", Severity.OK, "no scenario sessions are left running")
    listing = ", ".join(f"{scenario} ({status})" for _session, status, scenario in leftovers)
    return Finding(
        "sessions",
        Severity.WARN,
        f"{len(leftovers)} sessions were not destroyed: {listing}",
        "labctl scenario list, then labctl scenario destroy SESSION_UUID",
    )
