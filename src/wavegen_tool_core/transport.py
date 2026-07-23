"""Conservative VISA resource transport classification."""

from __future__ import annotations

import re

from wavegen_tool_core.errors import UnsupportedTransportError


USB_TRANSPORT = "usb"
TCPIP_TRANSPORT = "tcpip"
ASRL_TRANSPORT = "asrl"

_RESOURCE_PREFIX = re.compile(r"^([A-Za-z]+)\d*::")
_KNOWN_TRANSPORTS = frozenset({"gpib", ASRL_TRANSPORT, "pxi", "vxi"})


def normalize_resource(resource: str) -> str:
    """Return an explicit non-empty VISA resource string."""

    if not isinstance(resource, str) or not resource.strip():
        raise UnsupportedTransportError("VISA resource must not be empty.")
    return resource.strip()


def detect_resource_transport(resource: str) -> str:
    """Detect a known VISA resource prefix without applying admission policy."""

    normalized = normalize_resource(resource)
    match = _RESOURCE_PREFIX.match(normalized)
    prefix = match.group(1).casefold() if match else "unknown"
    if prefix in {USB_TRANSPORT, TCPIP_TRANSPORT, *_KNOWN_TRANSPORTS}:
        return prefix
    return "unknown"


def classify_transport(resource: str) -> str:
    """Accept only USB and TCPIP/LAN resources and fail closed otherwise."""

    transport = detect_resource_transport(resource)

    if transport == USB_TRANSPORT:
        return USB_TRANSPORT
    if transport == TCPIP_TRANSPORT:
        return TCPIP_TRANSPORT

    raise UnsupportedTransportError(
        f"Unsupported VISA resource transport {transport!r}; only USB and TCPIP/LAN are supported.",
        transport=transport,
    )
