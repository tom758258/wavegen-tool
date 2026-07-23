"""VISA backend normalization owned by Core."""

from __future__ import annotations

from dataclasses import dataclass

from wavegen_tool_core.errors import UnsupportedBackendError, UnsupportedConnectionScopeError
from wavegen_tool_core.transport import TCPIP_TRANSPORT, USB_TRANSPORT


SYSTEM_BACKEND = "system"
PYVISA_PY_BACKEND = "@py"


@dataclass(frozen=True)
class VisaBackend:
    """Normalized backend metadata and the value passed to PyVISA."""

    name: str
    internal_name: str
    pyvisa_library: str


def normalize_backend(value: str | None) -> VisaBackend:
    """Resolve the two supported backend scopes without fallback."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return VisaBackend(SYSTEM_BACKEND, "system_visa", "@ivi")
    if not isinstance(value, str):
        raise UnsupportedBackendError("VISA backend must be text.")

    normalized = value.strip()
    if normalized.casefold() == SYSTEM_BACKEND:
        return VisaBackend(SYSTEM_BACKEND, "system_visa", "@ivi")
    if normalized == PYVISA_PY_BACKEND:
        return VisaBackend(PYVISA_PY_BACKEND, "pyvisa_py", PYVISA_PY_BACKEND)
    raise UnsupportedBackendError(
        f"Unsupported VISA backend {value!r}; choose 'system' or '@py'."
    )


def validate_backend_transport(backend: VisaBackend, transport: str) -> None:
    """Allow only the backend and transport combinations in the identify scope."""

    if backend.name == SYSTEM_BACKEND and transport in {USB_TRANSPORT, TCPIP_TRANSPORT}:
        return
    if backend.name == PYVISA_PY_BACKEND and transport == TCPIP_TRANSPORT:
        return
    if backend.name == PYVISA_PY_BACKEND and transport == USB_TRANSPORT:
        raise UnsupportedConnectionScopeError(
            "The '@py' backend does not currently support USB resources. "
            "USB resources are supported with the 'system' backend; "
            "'@py' currently accepts TCPIP/LAN resources only.",
            backend=backend.name,
            transport=transport,
        )
    raise UnsupportedConnectionScopeError(
        f"Unsupported VISA connection scope {backend.name!r} + {transport!r}.",
        backend=backend.name,
        transport=transport,
    )
