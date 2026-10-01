from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

from sysadmin_lab.domain.resources import (
    NAME_COMPONENT,
    ResourceIdentity,
    ResourceKind,
    build_resource_name,
)

MAC_PATTERN = re.compile(r"^52:54:00(?::[0-9a-f]{2}){3}$")


@dataclass(frozen=True, slots=True)
class ScenarioInterface:
    """A guest NIC attached to one of the session's isolated scenario networks."""

    name: str
    network: str
    mac: str

    def __post_init__(self) -> None:
        if not MAC_PATTERN.fullmatch(self.mac):
            raise ValueError(f"invalid scenario interface MAC: {self.mac}")


@dataclass(frozen=True, slots=True)
class DomainSpec:
    identity: ResourceIdentity
    disk: Path
    seed_iso: Path
    memory_mib: int = 2048
    vcpus: int = 2
    network: str = "default"
    data_disks: tuple[tuple[str, Path], ...] = ()
    interfaces: tuple[ScenarioInterface, ...] = ()

    def __post_init__(self) -> None:
        if self.identity.kind is not ResourceKind.DOMAIN:
            raise ValueError("domain specification requires a domain identity")
        if not self.disk.is_absolute() or not self.seed_iso.is_absolute():
            raise ValueError("domain artifact paths must be absolute")
        if self.memory_mib < 1024:
            raise ValueError("domain memory must be at least 1024 MiB")
        if self.vcpus < 1:
            raise ValueError("domain must have at least one vCPU")
        if not self.network:
            raise ValueError("domain network must not be empty")
        for name, path in self.data_disks:
            if not NAME_COMPONENT.fullmatch(name):
                raise ValueError(f"invalid data disk name: {name}")
            if not path.is_absolute():
                raise ValueError("domain data disk paths must be absolute")


def network_identity(scenario_id: str, session_id: UUID, network: str) -> ResourceIdentity:
    role = f"net-{network}"
    return ResourceIdentity(
        kind=ResourceKind.NETWORK,
        name=build_resource_name(scenario_id, session_id, role),
        session_id=session_id,
        resource_id=uuid5(session_id, f"network:{network}"),
        scenario_id=scenario_id,
        role=role,
    )


def interface_mac(session_id: UUID, host_name: str, interface: str) -> str:
    """Derive a stable locally administered QEMU MAC for one scenario NIC."""
    digest = hashlib.sha256(f"{session_id}:{host_name}:{interface}".encode()).digest()
    return "52:54:00:" + ":".join(f"{byte:02x}" for byte in digest[:3])


def render_network_xml(identity: ResourceIdentity) -> str:
    """Render an isolated bridge: libvirt gives it no address, DHCP, or forwarding."""
    if identity.kind is not ResourceKind.NETWORK:
        raise ValueError("network XML requires a network identity")
    root = ET.Element("network")
    ET.SubElement(root, "name").text = identity.name
    ET.SubElement(root, "uuid").text = str(identity.resource_id)
    ET.SubElement(root, "bridge", {"stp": "off", "delay": "0"})
    return ET.tostring(root, encoding="unicode")


def domain_identity(scenario_id: str, session_id: UUID, role: str = "node1") -> ResourceIdentity:
    resource_id = uuid5(session_id, f"domain:{role}")
    return ResourceIdentity(
        kind=ResourceKind.DOMAIN,
        name=build_resource_name(scenario_id, session_id, role),
        session_id=session_id,
        resource_id=resource_id,
        scenario_id=scenario_id,
        role=role,
    )


def render_domain_xml(spec: DomainSpec) -> str:
    """Render a system-libvirt domain suitable for KVM and virt-manager."""
    root = ET.Element("domain", {"type": "kvm"})
    ET.SubElement(root, "name").text = spec.identity.name
    ET.SubElement(root, "uuid").text = str(spec.identity.resource_id)
    ET.SubElement(root, "memory", {"unit": "MiB"}).text = str(spec.memory_mib)
    ET.SubElement(root, "currentMemory", {"unit": "MiB"}).text = str(spec.memory_mib)
    ET.SubElement(root, "vcpu", {"placement": "static"}).text = str(spec.vcpus)

    os_element = ET.SubElement(root, "os", {"firmware": "efi"})
    ET.SubElement(os_element, "type", {"arch": "x86_64", "machine": "q35"}).text = "hvm"
    ET.SubElement(os_element, "boot", {"dev": "hd"})

    features = ET.SubElement(root, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")
    ET.SubElement(root, "cpu", {"mode": "host-passthrough", "check": "none"})
    ET.SubElement(root, "clock", {"offset": "utc"})
    ET.SubElement(root, "on_poweroff").text = "destroy"
    ET.SubElement(root, "on_reboot").text = "restart"
    ET.SubElement(root, "on_crash").text = "restart"

    devices = ET.SubElement(root, "devices")
    disk = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
    ET.SubElement(
        disk,
        "driver",
        {
            "name": "qemu",
            "type": "qcow2",
            "cache": "none",
            "discard": "unmap",
            "detect_zeroes": "unmap",
        },
    )
    ET.SubElement(disk, "source", {"file": str(spec.disk)})
    ET.SubElement(disk, "target", {"dev": "vda", "bus": "virtio"})

    for index, (name, path) in enumerate(spec.data_disks, start=1):
        data_disk = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
        ET.SubElement(
            data_disk,
            "driver",
            {
                "name": "qemu",
                "type": "qcow2",
                "cache": "none",
                "discard": "unmap",
            },
        )
        ET.SubElement(data_disk, "source", {"file": str(path)})
        ET.SubElement(data_disk, "target", {"dev": f"vd{chr(ord('a') + index)}", "bus": "virtio"})
        ET.SubElement(data_disk, "serial").text = f"lal-{name}"

    seed = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
    ET.SubElement(seed, "driver", {"name": "qemu", "type": "raw"})
    ET.SubElement(seed, "source", {"file": str(spec.seed_iso)})
    ET.SubElement(seed, "target", {"dev": "sda", "bus": "sata"})
    ET.SubElement(seed, "readonly")

    interface = ET.SubElement(devices, "interface", {"type": "network"})
    ET.SubElement(interface, "source", {"network": spec.network})
    ET.SubElement(interface, "model", {"type": "virtio"})
    for scenario_interface in spec.interfaces:
        extra = ET.SubElement(devices, "interface", {"type": "network"})
        ET.SubElement(extra, "mac", {"address": scenario_interface.mac})
        ET.SubElement(extra, "source", {"network": scenario_interface.network})
        ET.SubElement(extra, "model", {"type": "virtio"})
        # Unplugged until first boot has named the NIC and told NetworkManager to leave it
        # alone. With carrier, NetworkManager would try DHCP on a segment without a DHCP
        # server, and cloud-init, which creates the login user, waits a minute for that.
        ET.SubElement(extra, "link", {"state": "down"})

    serial = ET.SubElement(devices, "serial", {"type": "pty"})
    serial_target = ET.SubElement(serial, "target", {"type": "isa-serial", "port": "0"})
    ET.SubElement(serial_target, "model", {"name": "isa-serial"})
    console = ET.SubElement(devices, "console", {"type": "pty"})
    ET.SubElement(console, "target", {"type": "serial", "port": "0"})

    channel = ET.SubElement(devices, "channel", {"type": "unix"})
    ET.SubElement(channel, "source", {"mode": "bind"})
    ET.SubElement(
        channel,
        "target",
        {"type": "virtio", "name": "org.qemu.guest_agent.0"},
    )

    graphics = ET.SubElement(
        devices,
        "graphics",
        {"type": "spice", "autoport": "yes", "listen": "127.0.0.1"},
    )
    ET.SubElement(graphics, "listen", {"type": "address", "address": "127.0.0.1"})
    video = ET.SubElement(devices, "video")
    ET.SubElement(video, "model", {"type": "virtio", "heads": "1", "primary": "yes"})

    rng = ET.SubElement(devices, "rng", {"model": "virtio"})
    ET.SubElement(rng, "backend", {"model": "random"}).text = "/dev/urandom"
    ET.SubElement(devices, "memballoon", {"model": "virtio"})
    return ET.tostring(root, encoding="unicode")
