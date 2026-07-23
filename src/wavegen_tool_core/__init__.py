"""Core API for safe waveform-generator identification."""

from wavegen_tool_core.backends import (
    PYVISA_PY_BACKEND,
    SYSTEM_BACKEND,
    VisaBackend,
    normalize_backend,
)
from wavegen_tool_core.errors import (
    IdnQueryError,
    MalformedIdnError,
    ResourceManagerError,
    ResourceOpenError,
    UnsupportedBackendError,
    UnsupportedInstrumentError,
    UnsupportedTransportError,
    VisaCleanupError,
    WavegenError,
)
from wavegen_tool_core.identity import (
    CANONICAL_MANUFACTURER,
    CANONICAL_MODEL,
    CANONICAL_MODEL_ID,
    InstrumentIdentity,
    parse_idn,
    resolve_supported_identity,
)
from wavegen_tool_core.transport import classify_transport
from wavegen_tool_core.visa import (
    DEFAULT_TIMEOUT_MS,
    IdentificationResult,
    identify_instrument,
)

__all__ = [
    "CANONICAL_MANUFACTURER",
    "CANONICAL_MODEL",
    "CANONICAL_MODEL_ID",
    "DEFAULT_TIMEOUT_MS",
    "PYVISA_PY_BACKEND",
    "SYSTEM_BACKEND",
    "IdentificationResult",
    "IdnQueryError",
    "InstrumentIdentity",
    "MalformedIdnError",
    "ResourceManagerError",
    "ResourceOpenError",
    "UnsupportedBackendError",
    "UnsupportedInstrumentError",
    "UnsupportedTransportError",
    "VisaBackend",
    "VisaCleanupError",
    "WavegenError",
    "classify_transport",
    "identify_instrument",
    "normalize_backend",
    "parse_idn",
    "resolve_supported_identity",
]
