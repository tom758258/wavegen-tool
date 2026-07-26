"""Safe VISA lifecycles for explicit live resource access."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import math
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
    StatusQueryError,
    UnsupportedTransportError,
    VisaCleanupError,
    VisaWriteError,
    WaveformParameterError,
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
STATUS_QUERIES = (
    "OUTPut1?",
    "SOURce1:FUNCtion?",
    "SOURce1:FREQuency?",
    "SOURce1:VOLTage:UNIT?",
    "SOURce1:VOLTage?",
    "SOURce1:VOLTage:OFFSet?",
    "OUTPut1:LOAD?",
)
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
    """Minimum VISA session behavior required by the live VISA paths."""

    timeout: int
    baud_rate: int
    read_termination: str | None
    write_termination: str | None

    def query(self, command: str) -> str:
        """Return one query response."""

    def write(self, command: str) -> object:
        """Send one state-changing command."""

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
class StatusResult:
    """A successful read-only Channel 1 status readback."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    output_state: str
    function: str
    frequency_hz: float
    amplitude: float
    amplitude_unit: str
    offset_v: float
    load: str


@dataclass(frozen=True)
class SineConfigurationResult:
    """A successful Channel 1 sine configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class SquareConfigurationResult:
    """A successful Channel 1 square configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    duty_cycle_percent: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class RampConfigurationResult:
    """A successful Channel 1 ramp configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    symmetry_percent: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class PulseConfigurationResult:
    """A successful Channel 1 pulse configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    pulse_width_s: float
    edge_time_s: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class DcConfigurationResult:
    """A successful Channel 1 DC voltage configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    voltage_v: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class NoiseConfigurationResult:
    """A successful Channel 1 noise configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    amplitude_vpp: float
    offset_v: float
    bandwidth_hz: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class PrbsConfigurationResult:
    """A successful Channel 1 PRBS configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    bit_rate_bps: float
    amplitude_vpp: float
    pattern: str
    offset_v: float
    edge_time_s: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class OutputResult:
    """A successful explicit Channel 1 output-state change."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    output_state: str


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


def query_status(
    resource: str,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> StatusResult:
    """Read Channel 1 status from one exactly recognized 33521B."""

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
        raise ResourceManagerError(
            "Could not create the requested VISA ResourceManager.",
            backend=backend_selection.name,
            transport=transport,
        ) from exc

    session: VisaSession | None = None
    identity: InstrumentIdentity | None = None
    result: StatusResult | None = None
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
                    responses: dict[str, str] = {}
                    for command in STATUS_QUERIES:
                        try:
                            responses[command] = session.query(command)
                        except Exception as exc:
                            primary_error = StatusQueryError(
                                f"Status query {command} failed or timed out.",
                                backend=backend_selection.name,
                                transport=transport,
                                identity=identity,
                            )
                            primary_cause = exc
                            break
                    if primary_error is None:
                        try:
                            result = StatusResult(
                                resource=resource_name,
                                backend=backend_selection.name,
                                transport=transport,
                                identity=identity,
                                output_state=_parse_status_output(
                                    responses["OUTPut1?"]
                                ),
                                function=_parse_status_function(
                                    responses["SOURce1:FUNCtion?"]
                                ),
                                frequency_hz=_parse_status_number(
                                    responses["SOURce1:FREQuency?"],
                                    "frequency",
                                ),
                                amplitude_unit=_parse_status_unit(
                                    responses["SOURce1:VOLTage:UNIT?"]
                                ),
                                amplitude=_parse_status_number(
                                    responses["SOURce1:VOLTage?"],
                                    "amplitude",
                                ),
                                offset_v=_parse_status_number(
                                    responses["SOURce1:VOLTage:OFFSet?"],
                                    "offset",
                                ),
                                load=_parse_status_load(
                                    responses["OUTPut1:LOAD?"]
                                ),
                            )
                        except StatusQueryError as exc:
                            primary_error = exc.attach_context(
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
            identity=identity,
        )
    if result is None:  # pragma: no cover - defensive invariant
        raise RuntimeError("status query completed without a result or error")
    return result


def _parse_status_text(response: object, field: str) -> str:
    if not isinstance(response, str) or not response.strip():
        raise StatusQueryError(f"Malformed status response for {field}.")
    return response.strip()


def _parse_status_output(response: object) -> str:
    value = _parse_status_text(response, "output state")
    try:
        return {"0": "off", "1": "on"}[value]
    except KeyError as exc:
        raise StatusQueryError(
            "Malformed status response for output state."
        ) from exc


def _parse_status_function(response: object) -> str:
    return _parse_status_text(response, "function").upper()


def _parse_status_number(response: object, field: str) -> float:
    value = _parse_status_text(response, field)
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise StatusQueryError(f"Malformed status response for {field}.") from exc
    if not math.isfinite(number):
        raise StatusQueryError(f"Malformed status response for {field}.")
    return number


def _parse_status_unit(response: object) -> str:
    unit = _parse_status_text(response, "amplitude unit").upper()
    if unit not in {"VPP", "VRMS", "DBM"}:
        raise StatusQueryError("Malformed status response for amplitude unit.")
    return unit


def _parse_status_load(response: object) -> str:
    load = _parse_status_number(response, "output-load setting")
    if load >= 9e37:
        return "high-z"
    return _format_scpi_number(load)


def configure_sine(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SineConfigurationResult:
    """Validate and configure a Channel 1 sine wave while keeping output off."""

    frequency = _normalize_finite_number(frequency_hz, "frequency")
    amplitude = _normalize_finite_number(amplitude_vpp, "amplitude")
    offset = _normalize_finite_number(offset_v, "offset")
    normalized_load = _normalize_load(load)

    if not 0.000001 <= frequency <= 30_000_000:
        raise WaveformParameterError(
            "Sine frequency must be between 0.000001 Hz and 30000000 Hz."
        )

    _validate_vpp_levels(amplitude, offset, normalized_load, "Sine")

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SIN",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return SineConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        load=normalized_load,
    )


def configure_square(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    duty_cycle_percent: object = 50,
    load: object = 50,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SquareConfigurationResult:
    """Validate and configure a Channel 1 square wave while keeping output off."""

    frequency = _normalize_finite_number(
        frequency_hz,
        "frequency",
        waveform="Square",
    )
    amplitude = _normalize_finite_number(
        amplitude_vpp,
        "amplitude",
        waveform="Square",
    )
    offset = _normalize_finite_number(offset_v, "offset", waveform="Square")
    duty_cycle = _normalize_finite_number(
        duty_cycle_percent,
        "duty cycle",
        waveform="Square",
    )
    normalized_load = _normalize_load(load, waveform="Square")

    if not 0.000001 <= frequency <= 30_000_000:
        raise WaveformParameterError(
            "Square frequency must be between 0.000001 Hz and 30000000 Hz."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Square")

    minimum_duty = max(0.01, 100 * 16e-9 * frequency)
    maximum_duty = min(99.99, 100 * (1 - 16e-9 * frequency))
    below_minimum = duty_cycle < minimum_duty and not math.isclose(
        duty_cycle,
        minimum_duty,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    above_maximum = duty_cycle > maximum_duty and not math.isclose(
        duty_cycle,
        maximum_duty,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    if below_minimum or above_maximum:
        raise WaveformParameterError(
            "Square duty cycle must be between "
            f"{_format_scpi_number(minimum_duty)}% and "
            f"{_format_scpi_number(maximum_duty)}% at "
            f"{_format_scpi_number(frequency)} Hz."
        )

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SQUare",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        "SOURce1:FUNCtion:SQUare:DCYCle "
        f"{_format_scpi_number(duty_cycle)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return SquareConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
    )


def configure_ramp(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    symmetry_percent: object = 100,
    load: object = 50,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> RampConfigurationResult:
    """Validate and configure a Channel 1 ramp wave while keeping output off."""

    frequency = _normalize_finite_number(
        frequency_hz,
        "frequency",
        waveform="Ramp",
    )
    amplitude = _normalize_finite_number(
        amplitude_vpp,
        "amplitude",
        waveform="Ramp",
    )
    offset = _normalize_finite_number(offset_v, "offset", waveform="Ramp")
    symmetry = _normalize_finite_number(
        symmetry_percent,
        "symmetry",
        waveform="Ramp",
    )
    normalized_load = _normalize_load(load, waveform="Ramp")

    if not 0.000001 <= frequency <= 200_000:
        raise WaveformParameterError(
            "Ramp frequency must be between 0.000001 Hz and 200000 Hz."
        )
    if not 0 <= symmetry <= 100:
        raise WaveformParameterError(
            "Ramp symmetry must be between 0% and 100%."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Ramp")

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion RAMP",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        "SOURce1:FUNCtion:RAMP:SYMMetry "
        f"{_format_scpi_number(symmetry)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return RampConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        symmetry_percent=symmetry,
        load=normalized_load,
    )


def configure_pulse(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    pulse_width_s: object,
    offset_v: object = 0,
    edge_time_s: object = 10e-9,
    load: object = 50,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> PulseConfigurationResult:
    """Validate and configure a Channel 1 pulse wave while keeping output off."""

    frequency = _normalize_finite_number(
        frequency_hz,
        "frequency",
        waveform="Pulse",
    )
    amplitude = _normalize_finite_number(
        amplitude_vpp,
        "amplitude",
        waveform="Pulse",
    )
    pulse_width = _normalize_finite_number(
        pulse_width_s,
        "width",
        waveform="Pulse",
    )
    offset = _normalize_finite_number(offset_v, "offset", waveform="Pulse")
    edge_time = _normalize_finite_number(
        edge_time_s,
        "edge time",
        waveform="Pulse",
    )
    normalized_load = _normalize_load(load, waveform="Pulse")

    if not 0.000001 <= frequency <= 30_000_000:
        raise WaveformParameterError(
            "Pulse frequency must be between 0.000001 Hz and 30000000 Hz."
        )
    if not 8.4e-9 <= edge_time <= 1e-6:
        raise WaveformParameterError(
            "Pulse edge time must be between 0.0000000084 s and 0.000001 s."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Pulse")

    period = 1 / frequency
    edge_margin = 1.25 * edge_time
    minimum_width = max(16e-9, edge_margin)
    maximum_width = period - max(16e-9, edge_margin)
    invalid_width_window = (
        minimum_width > maximum_width
        and not math.isclose(
            minimum_width,
            maximum_width,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    )
    if invalid_width_window:
        raise WaveformParameterError(
            "Pulse frequency and edge time do not allow a valid pulse width."
        )
    below_minimum = pulse_width < minimum_width and not math.isclose(
        pulse_width,
        minimum_width,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    above_maximum = pulse_width > maximum_width and not math.isclose(
        pulse_width,
        maximum_width,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    if below_minimum or above_maximum:
        raise WaveformParameterError(
            "Pulse width must be between "
            f"{_format_scpi_number(minimum_width)} s and "
            f"{_format_scpi_number(maximum_width)} s at "
            f"{_format_scpi_number(frequency)} Hz."
        )

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion PULSe",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        "SOURce1:FUNCtion:PULSe:WIDTh "
        f"{_format_scpi_number(pulse_width)}",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH "
        f"{_format_scpi_number(edge_time)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return PulseConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        pulse_width_s=pulse_width,
        edge_time_s=edge_time,
        load=normalized_load,
    )


def configure_dc(
    resource: str,
    voltage_v: object,
    load: object = 50,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> DcConfigurationResult:
    """Validate and configure a Channel 1 DC voltage while keeping output off."""

    voltage = _normalize_finite_number(voltage_v, "voltage", waveform="DC")
    normalized_load = _normalize_load(load, waveform="DC")
    voltage_limit = 5.0 if normalized_load == "50" else 10.0
    if not -voltage_limit <= voltage <= voltage_limit:
        raise WaveformParameterError(
            f"DC voltage for {normalized_load} load must be between "
            f"{-voltage_limit:g} V and {voltage_limit:g} V."
        )

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:FUNCtion DC",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(voltage)}",
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return DcConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        voltage_v=voltage,
        load=normalized_load,
    )


def configure_noise(
    resource: str,
    amplitude_vpp: object,
    bandwidth_hz: object,
    offset_v: object = 0,
    load: object = 50,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> NoiseConfigurationResult:
    """Validate and configure a Channel 1 noise wave while keeping output off."""

    amplitude = _normalize_finite_number(
        amplitude_vpp,
        "amplitude",
        waveform="Noise",
    )
    bandwidth = _normalize_finite_number(
        bandwidth_hz,
        "bandwidth",
        waveform="Noise",
    )
    offset = _normalize_finite_number(offset_v, "offset", waveform="Noise")
    normalized_load = _normalize_load(load, waveform="Noise")

    if not 0.001 <= bandwidth <= 30_000_000:
        raise WaveformParameterError(
            "Noise bandwidth must be between 0.001 Hz and 30000000 Hz."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Noise")

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion NOISe",
        "SOURce1:FUNCtion:NOISe:BANDwidth "
        f"{_format_scpi_number(bandwidth)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return NoiseConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        amplitude_vpp=amplitude,
        offset_v=offset,
        bandwidth_hz=bandwidth,
        load=normalized_load,
    )


def configure_prbs(
    resource: str,
    bit_rate_bps: object,
    amplitude_vpp: object,
    pattern: object = "PN7",
    offset_v: object = 0,
    edge_time_s: object = 8.4e-9,
    load: object = 50,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> PrbsConfigurationResult:
    """Validate and configure Channel 1 PRBS while keeping output off."""

    bit_rate = _normalize_finite_number(
        bit_rate_bps,
        "bit rate",
        waveform="PRBS",
    )
    amplitude = _normalize_finite_number(
        amplitude_vpp,
        "amplitude",
        waveform="PRBS",
    )
    offset = _normalize_finite_number(offset_v, "offset", waveform="PRBS")
    edge_time = _normalize_finite_number(
        edge_time_s,
        "edge time",
        waveform="PRBS",
    )
    normalized_load = _normalize_load(load, waveform="PRBS")

    if not 0.001 <= bit_rate <= 50_000_000:
        raise WaveformParameterError(
            "PRBS bit rate must be between 0.001 bit/s and 50000000 bit/s."
        )
    if not isinstance(pattern, str):
        raise WaveformParameterError(
            "PRBS pattern must be PN7, PN9, PN11, PN15, PN20, or PN23."
        )
    normalized_pattern = pattern.strip().upper()
    if normalized_pattern not in {"PN7", "PN9", "PN11", "PN15", "PN20", "PN23"}:
        raise WaveformParameterError(
            "PRBS pattern must be PN7, PN9, PN11, PN15, PN20, or PN23."
        )
    if not 8.4e-9 <= edge_time <= 1e-6:
        raise WaveformParameterError(
            "PRBS edge time must be between 8.4e-9 s and 1e-6 s."
        )
    if edge_time > 1 / bit_rate:
        raise WaveformParameterError(
            "PRBS edge time must fit within the selected bit period."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "PRBS")

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion PRBS",
        f"SOURce1:FUNCtion:PRBS:BRATe {_format_scpi_number(bit_rate)}",
        f"SOURce1:FUNCtion:PRBS:DATA {normalized_pattern}",
        "SOURce1:FUNCtion:PRBS:TRANsition:BOTH "
        f"{_format_scpi_number(edge_time)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return PrbsConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        bit_rate_bps=bit_rate,
        amplitude_vpp=amplitude,
        pattern=normalized_pattern,
        offset_v=offset,
        edge_time_s=edge_time,
        load=normalized_load,
    )


def set_output(
    resource: str,
    state: str,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> OutputResult:
    """Explicitly set the recognized 33521B Channel 1 output state."""

    if not isinstance(state, str) or state.strip().casefold() not in {"on", "off"}:
        raise WaveformParameterError("Output state must be on or off.")
    normalized_state = state.strip().casefold()
    context = _write_to_supported_33521b(
        resource,
        backend,
        (f"OUTPut1 {normalized_state.upper()}",),
        output_state_after_writes=normalized_state,
        resource_manager_factory=resource_manager_factory,
    )
    return OutputResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        output_state=normalized_state,
    )


def _normalize_finite_number(
    value: object,
    label: str,
    *,
    waveform: str = "Sine",
) -> float:
    if isinstance(value, bool):
        raise WaveformParameterError(
            f"{waveform} {label} must be a finite number."
        )
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise WaveformParameterError(
            f"{waveform} {label} must be a finite number."
        ) from exc
    if not math.isfinite(normalized):
        raise WaveformParameterError(
            f"{waveform} {label} must be a finite number."
        )
    return normalized


def _normalize_load(value: object, *, waveform: str = "Sine") -> str:
    if isinstance(value, bool):
        raise WaveformParameterError(f"{waveform} load must be 50 or high-z.")
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"50", "high-z"}:
            return normalized
    elif value == 50:
        return "50"
    raise WaveformParameterError(f"{waveform} load must be 50 or high-z.")


def _validate_vpp_levels(
    amplitude: float,
    offset: float,
    load: str,
    waveform: str,
) -> None:
    if load == "50":
        amplitude_min, amplitude_max, voltage_limit = 0.001, 10.0, 5.0
    else:
        amplitude_min, amplitude_max, voltage_limit = 0.002, 20.0, 10.0
    if not amplitude_min <= amplitude <= amplitude_max:
        raise WaveformParameterError(
            f"{waveform} amplitude for {load} load must be between "
            f"{amplitude_min:g} Vpp and {amplitude_max:g} Vpp."
        )
    if abs(offset) + amplitude / 2 > voltage_limit:
        raise WaveformParameterError(
            f"{waveform} amplitude and offset exceed the "
            f"{voltage_limit:g} V peak limit for {load} load."
        )


def _format_scpi_number(value: float) -> str:
    return format(value, ".15g")


def _write_to_supported_33521b(
    resource: str,
    backend: str | None,
    commands: tuple[str, ...],
    *,
    output_state_after_writes: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None,
) -> IdentificationResult:
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
        raise ResourceManagerError(
            "Could not create the requested VISA ResourceManager.",
            backend=backend_selection.name,
            transport=transport,
        ) from exc

    session: VisaSession | None = None
    identity: InstrumentIdentity | None = None
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
                    try:
                        for command in commands:
                            session.write(command)
                    except Exception as exc:
                        primary_error = VisaWriteError(
                            "Could not apply the requested instrument control write.",
                            backend=backend_selection.name,
                            transport=transport,
                            identity=identity,
                        )
                        primary_cause = exc
    finally:
        cleanup_errors = _close_visa_resources(session, manager)

    if primary_error is not None:
        primary_error.attach_cleanup_errors(cleanup_errors)
        if primary_cause is not None:
            raise primary_error from primary_cause
        raise primary_error
    if cleanup_errors:
        if output_state_after_writes == "on":
            message = (
                "The Channel 1 output ON command was sent, but VISA cleanup failed; "
                "Channel 1 output may remain on: "
                + "; ".join(cleanup_errors)
                + "."
            )
        else:
            message = "VISA cleanup failed: " + "; ".join(cleanup_errors) + "."
        raise VisaCleanupError(
            message,
            backend=backend_selection.name,
            transport=transport,
            identity=identity,
            output_state=output_state_after_writes,
        )
    if identity is None:  # pragma: no cover - defensive invariant
        raise RuntimeError("instrument control completed without an identity or error")
    return IdentificationResult(
        resource=resource_name,
        backend=backend_selection.name,
        transport=transport,
        identity=identity,
    )


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
