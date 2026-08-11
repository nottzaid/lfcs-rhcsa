from __future__ import annotations

import re
from typing import Any

from sysadmin_lab.application.guest_execution import GuestEndpoint

HOST_ADDRESS = re.compile(r"\{\{host\.([a-z][a-z0-9-]*)\.address\}\}")


class UnknownHostPlaceholderError(ValueError):
    pass


def render_host_addresses(value: Any, endpoints: dict[str, GuestEndpoint]) -> Any:
    """Resolve the deliberately small runtime placeholder language in trusted manifests."""

    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            host = match.group(1)
            try:
                return endpoints[host].host
            except KeyError as exc:
                raise UnknownHostPlaceholderError(
                    f"manifest references unknown host {host}"
                ) from exc

        return HOST_ADDRESS.sub(replace, value)
    if isinstance(value, dict):
        return {key: render_host_addresses(item, endpoints) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(render_host_addresses(item, endpoints) for item in value)
    if isinstance(value, list):
        return [render_host_addresses(item, endpoints) for item in value]
    return value
