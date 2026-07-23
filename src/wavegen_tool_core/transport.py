"""Conservative VISA resource transport classification."""

from __future__ import annotations

import re

from wavegen_tool_core.errors import UnsupportedTransportError


USB_TRANSPORT = "usb"
TCPIP_TRANSPORT = "tcpip"

_RESOURCE_PREFIX = re.compile(r"^([A-Za-z]+)\d*::")
_KNOWN_UNSUPPORTED = frozenset({"gpib", "asrl", "pxi", "vxi"})


def normalize_resource(resource: str) -> str:
    """Return an explicit non-empty VISA resource string."""

    if not isinstance(resource, str) or not resource.strip():
        raise UnsupportedTransportError("VISA resource must not be empty.")
    return resource.strip()


def classify_transport(resource: str) -> str:
    """Accept only USB and TCPIP/LAN resources and fail closed otherwise."""

    normalized = normalize_resource(resource)
    match = _RESOURCE_PREFIX.match(normalized)
    prefix = match.group(1).casefold() if match else "unknown"

    if prefix == USB_TRANSPORT:
        return USB_TRANSPORT
    if prefix == TCPIP_TRANSPORT:
        return TCPIP_TRANSPORT

    detected = prefix if prefix in _KNOWN_UNSUPPORTED else "unknown"
    raise UnsupportedTransportError(
        f"Unsupported VISA resource transport {detected!r}; only USB and TCPIP/LAN are supported.",
        transport=detected,
    )
