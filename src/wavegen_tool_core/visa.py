"""Safe VISA lifecycles for live resource listing and read-only identification."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from wavegen_tool_core.backends import (
    VisaBackend,
    normalize_backend,
    validate_backend_transport,
)
from wavegen_tool_core.errors import (
    IdnQueryError,
    ResourceDiscoveryError,
    ResourceManagerError,
    ResourceOpenError,
    UnsupportedConnectionScopeError,
    UnsupportedTransportError,
    VisaCleanupError,
    WavegenError,
)
from wavegen_tool_core.identity import (
    InstrumentIdentity,
    parse_idn,
    resolve_supported_identity,
)
from wavegen_tool_core.transport import classify_transport, normalize_resource


IDN_QUERY = "*IDN?"
DEFAULT_TIMEOUT_MS = 5000
LIVE_VERIFY_TIMEOUT_MS = 1000


class VisaSession(Protocol):
    """Minimum VISA session behavior required for identification."""

    timeout: int

    def query(self, command: str) -> str:
        """Return one query response."""

    def close(self) -> None:
        """Close the session."""


class VisaResourceManager(Protocol):
    """Minimum ResourceManager behavior required by the live VISA paths."""

    def list_resources(self) -> Iterable[str]:
        """List resource strings without opening instrument sessions."""

    def open_resource(self, resource_name: str) -> VisaSession:
        """Open one explicit VISA resource."""

    def close(self) -> None:
        """Close the ResourceManager."""


ResourceManagerFactory = Callable[[str], VisaResourceManager]


@dataclass(frozen=True)
class IdentificationResult:
    """A successful recognized-model identification."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity


@dataclass(frozen=True)
class ResourceListResult:
    """A successful resource listing from one selected VISA backend."""

    backend: str
    resources: tuple[str, ...]


def create_resource_manager(pyvisa_library: str) -> VisaResourceManager:
    """Create a system or pyvisa-py ResourceManager without fallback."""

    import pyvisa

    return pyvisa.ResourceManager(pyvisa_library)


def list_resources(
    backend: str | None = None,
    *,
    live_only: bool = False,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> ResourceListResult:
    """List raw resources or retain candidates that answer one bounded *IDN? query."""

    backend_selection = normalize_backend(backend)
    factory = resource_manager_factory or create_resource_manager
    try:
        manager = factory(backend_selection.pyvisa_library)
    except Exception as exc:
        error = ResourceManagerError(
            "Could not create the requested VISA ResourceManager.",
            backend=backend_selection.name,
        )
        raise error from exc

    result: ResourceListResult | None = None
    primary_error: ResourceDiscoveryError | None = None
    primary_cause: Exception | None = None
    session_cleanup_errors: tuple[str, ...] = ()
    try:
        try:
            resources = tuple(manager.list_resources())
        except Exception as exc:
            primary_error = ResourceDiscoveryError(
                "Could not list VISA resources.",
                backend=backend_selection.name,
            )
            primary_cause = exc
        else:
            if live_only:
                resources, session_cleanup_errors = _filter_live_resources(
                    manager,
                    backend_selection,
                    resources,
                )
            result = ResourceListResult(
                backend=backend_selection.name,
                resources=resources,
            )
    finally:
        cleanup_errors = session_cleanup_errors + _close_resource_manager(manager)

    if primary_error is not None:
        primary_error.attach_cleanup_errors(cleanup_errors)
        raise primary_error from primary_cause
    if cleanup_errors:
        raise VisaCleanupError(
            "VISA cleanup failed: " + "; ".join(cleanup_errors) + ".",
            backend=backend_selection.name,
        )
    if result is None:  # pragma: no cover - defensive invariant
        raise RuntimeError("resource listing completed without a result or error")
    return result


def _filter_live_resources(
    manager: VisaResourceManager,
    backend_selection: VisaBackend,
    resources: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    live_resources: list[str] = []
    cleanup_errors: list[str] = []

    for resource in resources:
        try:
            transport = classify_transport(resource)
            validate_backend_transport(backend_selection, transport)
        except (UnsupportedTransportError, UnsupportedConnectionScopeError):
            continue

        session: VisaSession | None = None
        try:
            session = manager.open_resource(resource)
            session.timeout = LIVE_VERIFY_TIMEOUT_MS
            response = session.query(IDN_QUERY)
            if isinstance(response, str) and response.strip():
                live_resources.append(resource)
        except Exception:
            continue
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    cleanup_errors.append("session close failed")

    return tuple(live_resources), tuple(cleanup_errors)


def identify_instrument(
    resource: str,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> IdentificationResult:
    """Open one resource, send only *IDN?, resolve exact model recognition, and close."""

    backend_selection = normalize_backend(backend)
    resource_name = normalize_resource(resource)
    try:
        transport = classify_transport(resource_name)
    except WavegenError as exc:
        raise exc.attach_context(backend=backend_selection.name)
    validate_backend_transport(backend_selection, transport)

    factory = resource_manager_factory or create_resource_manager
    try:
        manager = factory(backend_selection.pyvisa_library)
    except Exception as exc:
        error = ResourceManagerError(
            "Could not create the requested VISA ResourceManager.",
            backend=backend_selection.name,
            transport=transport,
        )
        raise error from exc

    session: VisaSession | None = None
    result: IdentificationResult | None = None
    primary_error: WavegenError | None = None
    primary_cause: Exception | None = None

    try:
        try:
            session = manager.open_resource(resource_name)
            session.timeout = DEFAULT_TIMEOUT_MS
        except Exception as exc:
            primary_error = ResourceOpenError(
                "Could not open the explicit VISA resource.",
                backend=backend_selection.name,
                transport=transport,
            )
            primary_cause = exc
        else:
            try:
                raw_idn = session.query(IDN_QUERY)
            except Exception as exc:
                primary_error = IdnQueryError(
                    "The instrument identification query failed or timed out.",
                    backend=backend_selection.name,
                    transport=transport,
                )
                primary_cause = exc
            else:
                try:
                    identity = resolve_supported_identity(parse_idn(raw_idn))
                except WavegenError as exc:
                    primary_error = exc.attach_context(
                        backend=backend_selection.name,
                        transport=transport,
                    )
                else:
                    result = IdentificationResult(
                        resource=resource_name,
                        backend=backend_selection.name,
                        transport=transport,
                        identity=identity,
                    )
    finally:
        cleanup_errors = _close_visa_resources(session, manager)

    if primary_error is not None:
        primary_error.attach_cleanup_errors(cleanup_errors)
        if primary_cause is not None:
            raise primary_error from primary_cause
        raise primary_error
    if cleanup_errors:
        raise VisaCleanupError(
            "VISA cleanup failed: " + "; ".join(cleanup_errors) + ".",
            backend=backend_selection.name,
            transport=transport,
            identity=result.identity if result is not None else None,
        )
    if result is None:  # pragma: no cover - defensive invariant
        raise RuntimeError("identification completed without a result or error")
    return result


def _close_visa_resources(
    session: VisaSession | None,
    manager: VisaResourceManager,
) -> tuple[str, ...]:
    errors: list[str] = []
    if session is not None:
        try:
            session.close()
        except Exception:
            errors.append("session close failed")
    errors.extend(_close_resource_manager(manager))
    return tuple(errors)


def _close_resource_manager(manager: VisaResourceManager) -> tuple[str, ...]:
    try:
        manager.close()
    except Exception:
        return ("ResourceManager close failed",)
    return ()
