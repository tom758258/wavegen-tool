"""Core error types for fail-closed instrument identification."""

from __future__ import annotations

from typing import Any


class WavegenError(Exception):
    """Base class for expected Core failures."""

    code = "wavegen_error"

    def __init__(
        self,
        message: str,
        *,
        backend: str | None = None,
        transport: str | None = None,
        identity: Any = None,
        output_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.backend = backend
        self.transport = transport
        self.identity = identity
        self.output_state = output_state
        self.cleanup_errors: tuple[str, ...] = ()

    def attach_context(
        self,
        *,
        backend: str | None = None,
        transport: str | None = None,
        identity: Any = None,
        output_state: str | None = None,
    ) -> WavegenError:
        """Add available diagnostic context without replacing existing values."""

        if self.backend is None:
            self.backend = backend
        if self.transport is None:
            self.transport = transport
        if self.identity is None:
            self.identity = identity
        if self.output_state is None:
            self.output_state = output_state
        return self

    def attach_cleanup_errors(self, errors: tuple[str, ...]) -> WavegenError:
        """Retain cleanup failures while preserving the primary error."""

        self.cleanup_errors = errors
        return self


class UnsupportedBackendError(WavegenError):
    """The requested VISA backend is not supported."""

    code = "unsupported_backend"


class UnsupportedTransportError(WavegenError):
    """The resource transport is absent, unknown, or unsupported."""

    code = "unsupported_transport"


class UnsupportedConnectionScopeError(WavegenError):
    """The selected backend and transport combination is unsupported."""

    code = "unsupported_connection_scope"


class ResourceManagerError(WavegenError):
    """PyVISA could not create a ResourceManager."""

    code = "resource_manager_error"


class ResourceDiscoveryError(WavegenError):
    """The selected ResourceManager could not list VISA resources."""

    code = "resource_discovery_error"


class ResourceOpenError(WavegenError):
    """The explicit VISA resource could not be opened."""

    code = "resource_open_error"


class IdnQueryError(WavegenError):
    """The only permitted identification query failed."""

    code = "idn_query_error"


class StatusQueryError(WavegenError):
    """A status query failed or returned a malformed response."""

    code = "status_query_error"


class WaveformVerificationError(WavegenError):
    """A waveform limit or readback verification failed."""

    code = "waveform_verification_error"


class WaveformParameterError(WavegenError):
    """A waveform parameter cannot be safely applied."""

    code = "waveform_parameter_error"


class VisaWriteError(WavegenError):
    """A validated control write failed."""

    code = "visa_write_error"


class MalformedIdnError(WavegenError):
    """The identification response is not exactly four usable fields."""

    code = "malformed_idn"


class UnsupportedInstrumentError(WavegenError):
    """The parsed manufacturer and model are not explicitly supported."""

    code = "unsupported_instrument"


class VisaCleanupError(WavegenError):
    """A VISA cleanup step could not be completed."""

    code = "visa_cleanup_error"


class ErrorQueueQueryError(WavegenError):
    """A SYSTem:ERRor? query failed, was empty, or returned a malformed response."""

    code = "error_queue_query_error"
