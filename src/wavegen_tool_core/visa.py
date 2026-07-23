"""Safe VISA lifecycles for live resource listing and read-only identification."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

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
    UnsupportedTransportError,
    VisaCleanupError,
    WavegenError,
)
from wavegen_tool_core.identity import (
    InstrumentIdentity,
    parse_idn,
    resolve_supported_identity,
)
from wavegen_tool_core.transport import (
    ASRL_TRANSPORT,
    TCPIP_TRANSPORT,
    USB_TRANSPORT,
    classify_transport,
    detect_resource_transport,
    normalize_resource,
)


IDN_QUERY = "*IDN?"
DEFAULT_TIMEOUT_MS = 5000
LIVE_VERIFY_TIMEOUT_MS = 1000
SERIAL_TERMINATIONS = ("CR", "LF", "CRLF", "NONE")
_SERIAL_TERMINATION_VALUES = {
    "CR": "\r",
    "LF": "\n",
    "CRLF": "\r\n",
    "NONE": None,
}


class VisaSession(Protocol):
    """Minimum VISA session behavior required for identification."""

    timeout: int
    baud_rate: int
    read_termination: str | None
    write_termination: str | None

    def query(self, command: str) -> str:
        """Return one query response."""

    def close(self) -> None:
        """Close the session."""


class VisaResourceManager(Protocol):
    """Minimum ResourceManager behavior required by the live VISA paths."""

    def list_resources(self) -> Iterable[str]:
        """List resource strings without opening instrument sessions."""

    def open_resource(self, resource_name: str, **kwargs: object) -> VisaSession:
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
class ResourceListEntry:
    """One raw resource with an optional parsed identity summary."""

    resource: str
    manufacturer: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class ResourceListResult:
    """A successful resource listing from one selected VISA backend."""

    backend: str
    resources: tuple[ResourceListEntry, ...]


def normalize_serial_baud_rate(value: int | str) -> int:
    """Return a positive serial baud rate."""

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("serial baud rate must be a positive integer")
    try:
        baud_rate = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("serial baud rate must be a positive integer") from exc
    if baud_rate <= 0:
        raise ValueError("serial baud rate must be a positive integer")
    return baud_rate


def normalize_serial_termination(value: str) -> str | None:
    """Map one explicit serial termination token to its Python value."""

    if not isinstance(value, str):
        raise ValueError("serial termination must be CR, LF, CRLF, or NONE")
    try:
        return _SERIAL_TERMINATION_VALUES[value.strip().upper()]
    except KeyError as exc:
        raise ValueError("serial termination must be CR, LF, CRLF, or NONE") from exc


def create_resource_manager(pyvisa_library: str) -> VisaResourceManager:
    """Create a system or pyvisa-py ResourceManager without fallback."""

    import pyvisa

    return pyvisa.ResourceManager(pyvisa_library)


def list_resources(
    backend: str | None = None,
    *,
    live_only: bool = False,
    serial_baud_rate: int | str | None = None,
    serial_read_termination: str | None = None,
    serial_write_termination: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> ResourceListResult:
    """List raw resources or retain candidates that answer one bounded *IDN? query."""

    backend_selection = normalize_backend(backend)
    normalized_baud_rate = (
        normalize_serial_baud_rate(serial_baud_rate)
        if serial_baud_rate is not None
        else None
    )
    read_termination_provided = serial_read_termination is not None
    normalized_read_termination = (
        normalize_serial_termination(serial_read_termination)
        if read_termination_provided
        else None
    )
    write_termination_provided = serial_write_termination is not None
    normalized_write_termination = (
        normalize_serial_termination(serial_write_termination)
        if write_termination_provided
        else None
    )
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
            resource_names = tuple(manager.list_resources())
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
                    resource_names,
                    serial_baud_rate=normalized_baud_rate,
                    serial_read_termination=normalized_read_termination,
                    serial_read_termination_provided=read_termination_provided,
                    serial_write_termination=normalized_write_termination,
                    serial_write_termination_provided=write_termination_provided,
                )
            else:
                resources = tuple(
                    ResourceListEntry(resource=resource) for resource in resource_names
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
    *,
    serial_baud_rate: int | None,
    serial_read_termination: str | None,
    serial_read_termination_provided: bool,
    serial_write_termination: str | None,
    serial_write_termination_provided: bool,
) -> tuple[tuple[ResourceListEntry, ...], tuple[str, ...]]:
    live_resources: list[ResourceListEntry] = []
    cleanup_errors: list[str] = []

    for resource in resources:
        try:
            transport = detect_resource_transport(resource)
        except UnsupportedTransportError:
            continue
        if not _is_live_discovery_candidate(backend_selection, transport):
            continue

        session: VisaSession | None = None
        try:
            if transport == ASRL_TRANSPORT:
                session = manager.open_resource(
                    resource,
                    open_timeout=LIVE_VERIFY_TIMEOUT_MS,
                )
            else:
                session = manager.open_resource(resource)
            session.timeout = LIVE_VERIFY_TIMEOUT_MS
            if transport == ASRL_TRANSPORT:
                if serial_baud_rate is not None:
                    session.baud_rate = serial_baud_rate
                if serial_read_termination_provided:
                    session.read_termination = serial_read_termination
                if serial_write_termination_provided:
                    session.write_termination = serial_write_termination
            response = session.query(IDN_QUERY)
            if isinstance(response, str) and response.strip():
                try:
                    identity = parse_idn(response)
                except MalformedIdnError:
                    live_resources.append(ResourceListEntry(resource=resource))
                else:
                    live_resources.append(
                        ResourceListEntry(
                            resource=resource,
                            manufacturer=identity.manufacturer,
                            model=identity.model,
                        )
                    )
        except Exception:
            continue
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    cleanup_errors.append("session close failed")

    return tuple(live_resources), tuple(cleanup_errors)


def _is_live_discovery_candidate(
    backend_selection: VisaBackend,
    transport: str,
) -> bool:
    if backend_selection.name == SYSTEM_BACKEND:
        return transport in {USB_TRANSPORT, TCPIP_TRANSPORT, ASRL_TRANSPORT}
    return (
        backend_selection.name == PYVISA_PY_BACKEND
        and transport == TCPIP_TRANSPORT
    )


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
