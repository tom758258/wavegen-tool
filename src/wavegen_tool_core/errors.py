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
    ) -> None:
        super().__init__(message)
        self.backend = backend
        self.transport = transport
        self.identity = identity
        self.cleanup_errors: tuple[str, ...] = ()

    def attach_context(
        self,
        *,
        backend: str | None = None,
        transport: str | None = None,
        identity: Any = None,
    ) -> WavegenError:
        """Add available diagnostic context without replacing existing values."""

        if self.backend is None:
            self.backend = backend
        if self.transport is None:
            self.transport = transport
        if self.identity is None:
            self.identity = identity
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


class ResourceManagerError(WavegenError):
    """PyVISA could not create a ResourceManager."""

    code = "resource_manager_error"


class ResourceOpenError(WavegenError):
    """The explicit VISA resource could not be opened."""

    code = "resource_open_error"


class IdnQueryError(WavegenError):
    """The only permitted identification query failed."""

    code = "idn_query_error"


class MalformedIdnError(WavegenError):
    """The identification response is not exactly four usable fields."""

    code = "malformed_idn"


class UnsupportedInstrumentError(WavegenError):
    """The parsed manufacturer and model are not explicitly supported."""

    code = "unsupported_instrument"


class VisaCleanupError(WavegenError):
    """A session or ResourceManager could not be closed."""

    code = "visa_cleanup_error"
