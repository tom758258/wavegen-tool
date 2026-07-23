"""Core API for safe waveform-generator identification."""

from wavegen_tool_core.backends import (
    PYVISA_PY_BACKEND,
    SYSTEM_BACKEND,
    VisaBackend,
    normalize_backend,
    validate_backend_transport,
)
from wavegen_tool_core.errors import (
    IdnQueryError,
    MalformedIdnError,
    ResourceDiscoveryError,
    ResourceManagerError,
    ResourceOpenError,
    UnsupportedBackendError,
    UnsupportedConnectionScopeError,
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
    ResourceListResult,
    identify_instrument,
    list_live_resources,
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
    "ResourceDiscoveryError",
    "ResourceListResult",
    "ResourceManagerError",
    "ResourceOpenError",
    "UnsupportedBackendError",
    "UnsupportedConnectionScopeError",
    "UnsupportedInstrumentError",
    "UnsupportedTransportError",
    "VisaBackend",
    "VisaCleanupError",
    "WavegenError",
    "classify_transport",
    "identify_instrument",
    "list_live_resources",
    "normalize_backend",
    "parse_idn",
    "resolve_supported_identity",
    "validate_backend_transport",
]
