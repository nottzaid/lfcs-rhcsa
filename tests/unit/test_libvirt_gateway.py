from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from types import TracebackType
from uuid import UUID

import pytest

import sysadmin_lab.adapters.libvirt_gateway as gateway_module
from sysadmin_lab.adapters.libvirt_gateway import LibvirtGateway
from sysadmin_lab.domain.resources import ResourceKind

DOMAIN_UUID = "60000000-0000-0000-0000-000000000006"
NETWORK_UUID = "70000000-0000-0000-0000-000000000007"


class FakeLibvirtError(Exception):
    def __init__(self, code: int) -> None:
        self._code = code

    def get_error_code(self) -> int:
        return self._code


@dataclass
class FakeDomain:
    resource_name: str
    document: str
    active: bool = False
    calls: list[object] = field(default_factory=list)

    def name(self) -> str:
        return self.resource_name

    def UUIDString(self) -> str:
        return DOMAIN_UUID

    def XMLDesc(self, flags: int = 0) -> str:
        self.calls.append(("xml", flags))
        return self.document

    def isActive(self) -> int:
        return int(self.active)

    def create(self) -> int:
        self.active = True
        self.calls.append("start")
        return 0

    def destroy(self) -> int:
        self.active = False
        self.calls.append("stop")
        return 0

    def undefineFlags(self, flags: int = 0) -> int:
        self.calls.append(("undefine", flags))
        return 0

    def updateDeviceFlags(self, xml: str, flags: int = 0) -> int:
        self.calls.append(("update", xml, flags))
        return 0

    def interfaceAddresses(self, source: int, flags: int = 0) -> object:
        self.calls.append(("addresses", source, flags))
        return {
            "lo": {"addrs": [{"type": FakeApi.VIR_IP_ADDR_TYPE_IPV4, "addr": "127.0.0.1"}]},
            "eth0": {
                "addrs": [
                    {"type": FakeApi.VIR_IP_ADDR_TYPE_IPV4, "addr": "192.0.2.10"},
                    {"type": 1, "addr": "2001:db8::10"},
                    {"type": FakeApi.VIR_IP_ADDR_TYPE_IPV4, "addr": None},
                ]
            },
        }


@dataclass
class FakeNetwork:
    resource_name: str
    document: str
    active: bool = False
    calls: list[object] = field(default_factory=list)

    def name(self) -> str:
        return self.resource_name

    def UUIDString(self) -> str:
        return NETWORK_UUID

    def XMLDesc(self, flags: int = 0) -> str:
        self.calls.append(("xml", flags))
        return self.document

    def isActive(self) -> int:
        return int(self.active)

    def create(self) -> int:
        self.active = True
        self.calls.append("start")
        return 0

    def destroy(self) -> int:
        self.active = False
        self.calls.append("stop")
        return 0

    def undefine(self) -> int:
        self.calls.append("undefine")
        return 0


@dataclass
class FakeConnection:
    domains: dict[str, FakeDomain] = field(default_factory=dict)
    networks: dict[str, FakeNetwork] = field(default_factory=dict)
    unexpected_error: int | None = None
    closed: bool = False

    def lookupByName(self, name: str) -> FakeDomain:
        if self.unexpected_error is not None:
            raise FakeLibvirtError(self.unexpected_error)
        try:
            return self.domains[name]
        except KeyError as exc:
            raise FakeLibvirtError(FakeApi.VIR_ERR_NO_DOMAIN) from exc

    def networkLookupByName(self, name: str) -> FakeNetwork:
        if self.unexpected_error is not None:
            raise FakeLibvirtError(self.unexpected_error)
        try:
            return self.networks[name]
        except KeyError as exc:
            raise FakeLibvirtError(FakeApi.VIR_ERR_NO_NETWORK) from exc

    def defineXML(self, xml: str) -> FakeDomain:
        name = ET.fromstring(xml).findtext("name") or ""
        domain = FakeDomain(name, xml)
        self.domains[name] = domain
        return domain

    def networkDefineXML(self, xml: str) -> FakeNetwork:
        name = ET.fromstring(xml).findtext("name") or ""
        network = FakeNetwork(name, xml)
        self.networks[name] = network
        return network

    def close(self) -> int:
        self.closed = True
        return 0


class FakeApi:
    VIR_ERR_NO_DOMAIN = 42
    VIR_ERR_NO_NETWORK = 43
    VIR_DOMAIN_XML_INACTIVE = 1
    VIR_DOMAIN_AFFECT_LIVE = 1
    VIR_DOMAIN_AFFECT_CONFIG = 2
    VIR_DOMAIN_UNDEFINE_MANAGED_SAVE = 2
    VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA = 4
    VIR_DOMAIN_UNDEFINE_NVRAM = 8
    VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA = 16
    VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE = 17
    VIR_IP_ADDR_TYPE_IPV4 = 0
    libvirtError = FakeLibvirtError

    def __init__(self, connection: FakeConnection | None) -> None:
        self.connection = connection
        self.opened_uri: str | None = None

    def open(self, uri: str) -> FakeConnection | None:
        self.opened_uri = uri
        return self.connection


def test_domain_resource_lifecycle_uses_safe_undefine_flags() -> None:
    connection = FakeConnection()
    gateway = LibvirtGateway(connection, FakeApi(connection))  # type: ignore[arg-type]
    resource = gateway.define(ResourceKind.DOMAIN, "<domain><name>lal-domain</name></domain>")

    assert resource.name == "lal-domain"
    assert resource.uuid == UUID(DOMAIN_UUID)
    assert "lal-domain" in resource.xml()
    resource.start()
    assert resource.is_active()
    resource.stop()
    resource.undefine()

    domain = connection.domains["lal-domain"]
    assert ("xml", FakeApi.VIR_DOMAIN_XML_INACTIVE) in domain.calls
    assert (
        "undefine",
        FakeApi.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
        | FakeApi.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
        | FakeApi.VIR_DOMAIN_UNDEFINE_NVRAM
        | FakeApi.VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA,
    ) in domain.calls


def test_network_resource_lifecycle() -> None:
    connection = FakeConnection()
    gateway = LibvirtGateway(connection, FakeApi(connection))  # type: ignore[arg-type]
    resource = gateway.define(ResourceKind.NETWORK, "<network><name>lal-network</name></network>")

    assert resource.name == "lal-network"
    assert resource.uuid == UUID(NETWORK_UUID)
    assert "lal-network" in resource.xml()
    resource.start()
    resource.stop()
    resource.undefine()
    assert connection.networks["lal-network"].calls == [
        ("xml", 0),
        "start",
        "stop",
        "undefine",
    ]


def test_find_returns_resources_or_none_only_for_not_found() -> None:
    connection = FakeConnection()
    connection.defineXML("<domain><name>lal-domain</name></domain>")
    connection.networkDefineXML("<network><name>lal-network</name></network>")
    gateway = LibvirtGateway(connection, FakeApi(connection))  # type: ignore[arg-type]

    assert gateway.find(ResourceKind.DOMAIN, "lal-domain") is not None
    assert gateway.find(ResourceKind.NETWORK, "lal-network") is not None
    assert gateway.find(ResourceKind.DOMAIN, "missing") is None
    assert gateway.find(ResourceKind.NETWORK, "missing") is None

    connection.unexpected_error = 999
    with pytest.raises(FakeLibvirtError):
        gateway.find(ResourceKind.DOMAIN, "missing")


def test_domain_ipv4_addresses_returns_non_loopback_dhcp_leases() -> None:
    connection = FakeConnection()
    connection.defineXML("<domain><name>lal-domain</name></domain>")
    gateway = LibvirtGateway(connection, FakeApi(connection))  # type: ignore[arg-type]

    assert gateway.domain_ipv4_addresses("lal-domain") == ("192.0.2.10",)
    assert ("addresses", FakeApi.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE, 0) in (
        connection.domains["lal-domain"].calls
    )
    assert gateway.domain_ipv4_addresses("lal-gone") == ()  # a deleted domain holds nothing


def test_connect_and_context_manager_close_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()
    api = FakeApi(connection)
    monkeypatch.setattr(gateway_module, "import_module", lambda _name: api)

    with LibvirtGateway.connect("test:///default") as gateway:
        assert gateway.find(ResourceKind.DOMAIN, "missing") is None

    assert api.opened_uri == "test:///default"
    assert connection.closed


def test_connect_rejects_missing_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(None)
    monkeypatch.setattr(gateway_module, "import_module", lambda _name: api)
    with pytest.raises(RuntimeError, match="returned no connection"):
        LibvirtGateway.connect()


def test_explicit_close_is_supported() -> None:
    connection = FakeConnection()
    gateway = LibvirtGateway(connection, FakeApi(connection))  # type: ignore[arg-type]
    gateway.close()
    assert connection.closed


def test_context_exit_signature_accepts_exception_details() -> None:
    connection = FakeConnection()
    gateway = LibvirtGateway(connection, FakeApi(connection))  # type: ignore[arg-type]
    traceback: TracebackType | None = None
    gateway.__exit__(RuntimeError, RuntimeError("test"), traceback)
    assert connection.closed


def test_connect_interfaces_plugs_in_the_named_nics_now_and_at_every_later_start() -> None:
    document = """<domain><name>lal-node</name><devices>
      <interface type="network"><source network="default"/></interface>
      <interface type="network"><mac address="52:54:00:AA:00:01"/><link state="down"/></interface>
      <interface type="network"><mac address="52:54:00:aa:00:02"/></interface>
      <interface type="network"><mac address="52:54:00:aa:00:03"/><link state="down"/></interface>
    </devices></domain>"""
    domain = FakeDomain("lal-node", document)
    gateway = LibvirtGateway(FakeConnection(domains={"lal-node": domain}), FakeApi(None))

    gateway.connect_interfaces("lal-node", ("52:54:00:aa:00:01", "52:54:00:aa:00:02"))

    updates = [call for call in domain.calls if call[0] == "update"]
    assert [flags for _update, _xml, flags in updates] == [
        FakeApi.VIR_DOMAIN_AFFECT_LIVE,
        FakeApi.VIR_DOMAIN_AFFECT_LIVE,
        FakeApi.VIR_DOMAIN_AFFECT_CONFIG,
        FakeApi.VIR_DOMAIN_AFFECT_CONFIG,
    ]
    assert ("xml", FakeApi.VIR_DOMAIN_XML_INACTIVE) in domain.calls  # the saved definition
    for _update, xml, _flags in updates:
        interface = ET.fromstring(xml)
        assert interface.find("mac").attrib["address"].lower() != "52:54:00:aa:00:03"
        assert interface.find("link").attrib == {"state": "up"}

    with pytest.raises(LookupError, match="no interface with MAC 52:54:00:aa:00:09"):
        gateway.connect_interfaces("lal-node", ("52:54:00:aa:00:09",))
