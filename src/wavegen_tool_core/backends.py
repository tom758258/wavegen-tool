"""VISA backend normalization owned by Core."""

from __future__ import annotations

from dataclasses import dataclass

from wavegen_tool_core.errors import UnsupportedBackendError


SYSTEM_BACKEND = "system"
PYVISA_PY_BACKEND = "@py"


@dataclass(frozen=True)
class VisaBackend:
    """Normalized backend metadata and the value passed to PyVISA."""

    name: str
    internal_name: str
    pyvisa_library: str | None


def normalize_backend(value: str | None) -> VisaBackend:
    """Resolve the two supported backend scopes without fallback."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return VisaBackend(SYSTEM_BACKEND, "system_visa", None)
    if not isinstance(value, str):
        raise UnsupportedBackendError("VISA backend must be text.")

    normalized = value.strip()
    if normalized.casefold() == SYSTEM_BACKEND:
        return VisaBackend(SYSTEM_BACKEND, "system_visa", None)
    if normalized == PYVISA_PY_BACKEND:
        return VisaBackend(PYVISA_PY_BACKEND, "pyvisa_py", PYVISA_PY_BACKEND)
    raise UnsupportedBackendError(
        f"Unsupported VISA backend {value!r}; choose 'system' or '@py'."
    )
