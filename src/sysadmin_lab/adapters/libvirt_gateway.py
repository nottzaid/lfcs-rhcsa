from __future__ import annotations

import xml.etree.ElementTree as ET
from importlib import import_module
from types import TracebackType
from typing import Protocol, Self, cast
from uuid import UUID

from sysadmin_lab.application.resources import ManagedResource
from sysadmin_lab.domain.resources import ResourceKind


class RawDomain(Protocol):
    def name(self) -> str: ...

    def UUIDString(self) -> str: ...

    def XMLDesc(self, flags: int = 0) -> str: ...

    def isActive(self) -> int: ...

    def create(self) -> int: ...

    def destroy(self) -> int: ...

    def undefineFlags(self, flags: int = 0) -> int: ...

    def interfaceAddresses(self, source: int, flags: int = 0) -> object: ...

    def updateDeviceFlags(self, xml: str, flags: int = 0) -> int: ...


class RawNetwork(Protocol):
    def name(self) -> str: ...

    def UUIDString(self) -> str: ...

    def XMLDesc(self, flags: int = 0) -> str: ...

    def isActive(self) -> int: ...

    def create(self) -> int: ...

    def destroy(self) -> int: ...

    def undefine(self) -> int: ...


class RawConnection(Protocol):
    def lookupByName(self, name: str) -> RawDomain: ...

    def networkLookupByName(self, name: str) -> RawNetwork: ...

    def defineXML(self, xml: str) -> RawDomain: ...

    def networkDefineXML(self, xml: str) -> RawNetwork: ...

    def close(self) -> int: ...


class LibvirtException(Protocol):
    def get_error_code(self) -> int: ...


class LibvirtApi(Protocol):
    VIR_ERR_NO_DOMAIN: int
    VIR_ERR_NO_NETWORK: int
    VIR_DOMAIN_XML_INACTIVE: int
    VIR_DOMAIN_AFFECT_LIVE: int
    VIR_DOMAIN_AFFECT_CONFIG: int
    VIR_DOMAIN_UNDEFINE_MANAGED_SAVE: int
    VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA: int
    VIR_DOMAIN_UNDEFINE_NVRAM: int
    VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA: int
    VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE: int
    VIR_IP_ADDR_TYPE_IPV4: int
    libvirtError: type[Exception]

    def open(self, uri: str) -> RawConnection | None: ...


class _DomainResource:
    kind = ResourceKind.DOMAIN

    def __init__(self, domain: RawDomain, api: LibvirtApi) -> None:
        self._domain = domain
        self._api = api

    @property
    def name(self) -> str:
        return self._domain.name()

    @property
    def uuid(self) -> UUID:
        return UUID(self._domain.UUIDString())

    def xml(self) -> str:
        return self._domain.XMLDesc(self._api.VIR_DOMAIN_XML_INACTIVE)

    def is_active(self) -> bool:
        return bool(self._domain.isActive())

    def start(self) -> None:
        self._domain.create()

    def stop(self) -> None:
        self._domain.destroy()

    def undefine(self) -> None:
        flags = (
            self._api.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
            | self._api.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
            | self._api.VIR_DOMAIN_UNDEFINE_NVRAM
            | self._api.VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA
        )
        self._domain.undefineFlags(flags)


class _NetworkResource:
    kind = ResourceKind.NETWORK

    def __init__(self, network: RawNetwork) -> None:
        self._network = network

    @property
    def name(self) -> str:
        return self._network.name()

    @property
    def uuid(self) -> UUID:
        return UUID(self._network.UUIDString())

    def xml(self) -> str:
        return self._network.XMLDesc(0)

    def is_active(self) -> bool:
        return bool(self._network.isActive())

    def start(self) -> None:
        self._network.create()

    def stop(self) -> None:
        self._network.destroy()

    def undefine(self) -> None:
        self._network.undefine()


class LibvirtGateway:
    """Small typed boundary around the untyped libvirt Python extension."""

    def __init__(self, connection: RawConnection, api: LibvirtApi) -> None:
        self._connection = connection
        self._api = api

    @classmethod
    def connect(cls, uri: str = "qemu:///system") -> Self:
        api = cast(LibvirtApi, import_module("libvirt"))
        connection = api.open(uri)
        if connection is None:
            raise RuntimeError(f"libvirt returned no connection for {uri}")
        return cls(connection, api)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def find(self, kind: ResourceKind, name: str) -> ManagedResource | None:
        try:
            if kind is ResourceKind.DOMAIN:
                return _DomainResource(self._connection.lookupByName(name), self._api)
            return _NetworkResource(self._connection.networkLookupByName(name))
        except self._api.libvirtError as exc:
            error = cast(LibvirtException, exc)
            expected = (
                self._api.VIR_ERR_NO_DOMAIN
                if kind is ResourceKind.DOMAIN
                else self._api.VIR_ERR_NO_NETWORK
            )
            if error.get_error_code() == expected:
                return None
            raise

    def define(self, kind: ResourceKind, xml: str) -> ManagedResource:
        if kind is ResourceKind.DOMAIN:
            return _DomainResource(self._connection.defineXML(xml), self._api)
        return _NetworkResource(self._connection.networkDefineXML(xml))

    def connect_interfaces(self, name: str, macs: tuple[str, ...]) -> None:
        """Plug in a running domain's NICs, now and in its saved definition, by MAC."""
        domain = self._connection.lookupByName(name)
        for flags, scope in (
            (0, self._api.VIR_DOMAIN_AFFECT_LIVE),
            (self._api.VIR_DOMAIN_XML_INACTIVE, self._api.VIR_DOMAIN_AFFECT_CONFIG),
        ):
            interfaces = {
                address.get("address", "").lower(): element
                for element in ET.fromstring(domain.XMLDesc(flags)).iterfind("./devices/interface")
                for address in element.iterfind("mac")
            }
            for mac in macs:
                interface = interfaces.get(mac.lower())
                if interface is None:
                    raise LookupError(f"{name} has no interface with MAC {mac}")
                link = interface.find("link")
                if link is None:
                    link = ET.SubElement(interface, "link")
                link.set("state", "up")
                domain.updateDeviceFlags(ET.tostring(interface, encoding="unicode"), scope)

    def domain_ipv4_addresses(self, name: str) -> tuple[str, ...]:
        """Return DHCP lease addresses for one explicitly named domain."""
        domain = self._connection.lookupByName(name)
        raw = domain.interfaceAddresses(self._api.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE, 0)
        interfaces = cast(dict[str, dict[str, object]], raw)
        addresses: list[str] = []
        for interface in interfaces.values():
            for address in cast(list[dict[str, object]], interface.get("addrs", [])):
                if address.get("type") == self._api.VIR_IP_ADDR_TYPE_IPV4:
                    value = address.get("addr")
                    if isinstance(value, str) and not value.startswith("127."):
                        addresses.append(value)
        return tuple(addresses)
