from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

LAB_NAME_PREFIX = "lal-"
LAB_METADATA_URI = "https://linux-admin-lab.local/metadata/1"
LAB_METADATA_TAG = f"{{{LAB_METADATA_URI}}}resource"
LAB_PROJECT_ID = "linux-admin-lab"
LAB_METADATA_SCHEMA = "1"
MAX_RESOURCE_NAME_LENGTH = 63
NAME_COMPONENT = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

ET.register_namespace("lal", LAB_METADATA_URI)


class ResourceKind(StrEnum):
    DOMAIN = "domain"
    NETWORK = "network"


class ResourceSafetyError(RuntimeError):
    """Raised when ownership cannot be proven before a mutating operation."""


class ResourceCollisionError(ResourceSafetyError):
    """Raised when a requested resource name already exists."""


class ResourceDriftError(ResourceSafetyError):
    """Raised when libvirt state and the ownership registry disagree."""


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    kind: ResourceKind
    name: str
    session_id: UUID
    resource_id: UUID
    scenario_id: str
    role: str

    def __post_init__(self) -> None:
        if not self.name.startswith(LAB_NAME_PREFIX):
            raise ValueError(f"resource name must start with {LAB_NAME_PREFIX}")
        if len(self.name) > MAX_RESOURCE_NAME_LENGTH:
            raise ValueError("resource name exceeds libvirt project limit")
        for label, value in (("scenario_id", self.scenario_id), ("role", self.role)):
            if not NAME_COMPONENT.fullmatch(value):
                raise ValueError(f"invalid {label}: {value}")


def build_resource_name(scenario_id: str, session_id: UUID, role: str) -> str:
    """Build a deterministic, bounded libvirt name from validated components."""
    for label, value in (("scenario_id", scenario_id), ("role", role)):
        if not NAME_COMPONENT.fullmatch(value):
            raise ValueError(f"invalid {label}: {value}")
    readable = f"{LAB_NAME_PREFIX}{scenario_id}-{session_id.hex[:8]}-{role}"
    if len(readable) <= MAX_RESOURCE_NAME_LENGTH:
        return readable
    digest = hashlib.sha256(readable.encode()).hexdigest()[:10]
    available = MAX_RESOURCE_NAME_LENGTH - len(LAB_NAME_PREFIX) - len(digest) - 1
    return f"{LAB_NAME_PREFIX}{readable[len(LAB_NAME_PREFIX) :][:available]}-{digest}"


def annotate_libvirt_xml(xml: str, identity: ResourceIdentity) -> str:
    """Add immutable ownership metadata to domain or network XML."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"invalid libvirt XML: {exc}") from exc

    expected_root = identity.kind.value
    if root.tag != expected_root:
        raise ValueError(f"expected <{expected_root}> XML, got <{root.tag}>")
    if root.findtext("name") != identity.name:
        raise ValueError("libvirt XML name does not match resource identity")

    metadata = root.find("metadata")
    if metadata is None:
        metadata = ET.SubElement(root, "metadata")
    if metadata.find(LAB_METADATA_TAG) is not None:
        raise ValueError("libvirt XML already contains Linux Admin Lab metadata")

    ET.SubElement(
        metadata,
        LAB_METADATA_TAG,
        {
            "project": LAB_PROJECT_ID,
            "schema": LAB_METADATA_SCHEMA,
            "kind": identity.kind.value,
            "session-id": str(identity.session_id),
            "resource-id": str(identity.resource_id),
            "scenario-id": identity.scenario_id,
            "role": identity.role,
        },
    )
    return ET.tostring(root, encoding="unicode")


def identity_from_libvirt_xml(xml: str) -> ResourceIdentity:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ResourceSafetyError(f"cannot parse resource XML: {exc}") from exc

    metadata = root.find(f"metadata/{LAB_METADATA_TAG}")
    if metadata is None:
        raise ResourceSafetyError("resource has no Linux Admin Lab ownership metadata")
    if metadata.get("project") != LAB_PROJECT_ID:
        raise ResourceSafetyError("resource metadata has the wrong project identifier")
    if metadata.get("schema") != LAB_METADATA_SCHEMA:
        raise ResourceSafetyError("resource metadata uses an unsupported schema")

    try:
        identity = ResourceIdentity(
            kind=ResourceKind(metadata.attrib["kind"]),
            name=root.findtext("name") or "",
            session_id=UUID(metadata.attrib["session-id"]),
            resource_id=UUID(metadata.attrib["resource-id"]),
            scenario_id=metadata.attrib["scenario-id"],
            role=metadata.attrib["role"],
        )
    except (KeyError, ValueError) as exc:
        raise ResourceSafetyError(f"invalid ownership metadata: {exc}") from exc
    return identity


def assert_identity_matches(actual: ResourceIdentity, expected: ResourceIdentity) -> None:
    if actual != expected:
        raise ResourceSafetyError(
            f"resource ownership mismatch: expected {expected.resource_id}, "
            f"found {actual.resource_id}"
        )
