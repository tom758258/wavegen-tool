"""Safe VISA lifecycles for explicit live resource access."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import csv
from dataclasses import dataclass
from io import StringIO
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
    ErrorQueueQueryError,
    IdnQueryError,
    MalformedIdnError,
    ResourceDiscoveryError,
    ResourceManagerError,
    ResourceOpenError,
    StatusQueryError,
    UnsupportedInstrumentError,
    UnsupportedTransportError,
    VisaCleanupError,
    VisaWriteError,
    WaveformParameterError,
    WaveformVerificationError,
    WavegenError,
)
from wavegen_tool_core.identity import (
    CANONICAL_MODEL,
    CANONICAL_MODEL_ID,
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
SYSTEM_ERROR_QUERY = "SYSTem:ERRor?"
DEFAULT_ERROR_QUEUE_MAX_READS = 20
ERROR_QUEUE_MAX_READS_MIN = 1
ERROR_QUEUE_MAX_READS_MAX = 100
ERROR_QUEUE_NO_ERROR_CODE = 0
STATUS_QUERIES = (
    "OUTPut1?",
    "SOURce1:FUNCtion?",
    "SOURce1:VOLTage:OFFSet?",
    "OUTPut1:LOAD?",
)
STATUS_COMMON_QUERIES = STATUS_QUERIES
STATUS_FREQUENCY_AMPLITUDE_QUERIES = (
    "SOURce1:FREQuency?",
    "SOURce1:VOLTage:UNIT?",
    "SOURce1:VOLTage?",
)
STATUS_NOISE_QUERIES = (
    "SOURce1:VOLTage:UNIT?",
    "SOURce1:VOLTage?",
    "SOURce1:FUNCtion:NOISe:BANDwidth?",
)
PULSE_TIMING_REL_TOLERANCE = 0.0
PULSE_TIMING_ABS_TOLERANCE_S = 100e-12
PULSE_FREQUENCY_ABS_TOLERANCE_HZ = 1e-6
DEFAULT_TIMEOUT_MS = 5000
LIVE_VERIFY_TIMEOUT_MS = 1000
SINE_SWEEP_LINEAR_MAX_TIME_S = 8000.0
SINE_SWEEP_LOGARITHMIC_MAX_TIME_S = 500.0
SINE_SWEEP_HOLD_RETURN_MAX_TIME_S = 3600.0
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
    frequency_hz: float | None
    amplitude: float | None
    amplitude_unit: str | None
    bandwidth_hz: float | None
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
    phase_deg: float = 0.0


@dataclass(frozen=True)
class SineDryRunResult:
    """A hardware-free preview of a Channel 1 sine configuration."""

    model: str
    canonical_model_id: str
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    phase_deg: float = 0.0


@dataclass(frozen=True)
class SineSweepConfigurationResult:
    """A successful Channel 1 sine frequency sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class SineSweepDryRunResult:
    """A hardware-free preview of a Channel 1 sine frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"


@dataclass(frozen=True)
class SquareSweepConfigurationResult:
    """A successful Channel 1 square frequency sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    duty_cycle_percent: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class SquareSweepDryRunResult:
    """A hardware-free preview of a Channel 1 square frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    duty_cycle_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"


@dataclass(frozen=True)
class RampSweepConfigurationResult:
    """A successful Channel 1 ramp frequency sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    symmetry_percent: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class RampSweepDryRunResult:
    """A hardware-free preview of a Channel 1 ramp frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    symmetry_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"


@dataclass(frozen=True)
class TriangleSweepConfigurationResult:
    """A successful Channel 1 triangle frequency sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    output_state: str = "off"


@dataclass(frozen=True)
class TriangleSweepDryRunResult:
    """A hardware-free preview of a Channel 1 triangle frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
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
    phase_deg: float = 0.0


@dataclass(frozen=True)
class SquareDryRunResult:
    """A hardware-free preview of a Channel 1 square configuration."""

    model: str
    canonical_model_id: str
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    duty_cycle_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    phase_deg: float = 0.0


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
    phase_deg: float = 0.0


@dataclass(frozen=True)
class RampDryRunResult:
    """A hardware-free preview of a Channel 1 ramp configuration."""

    model: str
    canonical_model_id: str
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    symmetry_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    phase_deg: float = 0.0


@dataclass(frozen=True)
class TriangleConfigurationResult:
    """A successful Channel 1 triangle configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    load: str
    output_state: str = "off"
    phase_deg: float = 0.0


@dataclass(frozen=True)
class TriangleDryRunResult:
    """A hardware-free preview of a Channel 1 triangle configuration."""

    model: str
    canonical_model_id: str
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    phase_deg: float = 0.0


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
    edge_time_s: float | None
    load: str
    output_state: str = "off"
    phase_deg: float = 0.0
    leading_edge_s: float | None = None
    trailing_edge_s: float | None = None


@dataclass(frozen=True)
class PulseDryRunResult:
    """A hardware-free preview of a Channel 1 pulse configuration."""

    model: str
    canonical_model_id: str
    frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    pulse_width_s: float
    edge_time_s: float | None
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    phase_deg: float = 0.0
    leading_edge_s: float | None = None
    trailing_edge_s: float | None = None


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
class DcDryRunResult:
    """A hardware-free preview of a Channel 1 DC configuration."""

    model: str
    canonical_model_id: str
    voltage_v: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
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
class NoiseDryRunResult:
    """A hardware-free preview of a Channel 1 noise configuration."""

    model: str
    canonical_model_id: str
    amplitude_vpp: float
    offset_v: float
    bandwidth_hz: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
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
class PrbsDryRunResult:
    """A hardware-free preview of a Channel 1 PRBS configuration."""

    model: str
    canonical_model_id: str
    bit_rate_bps: float
    amplitude_vpp: float
    pattern: str
    offset_v: float
    edge_time_s: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
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


@dataclass(frozen=True)
class SystemErrorEntry:
    """One parsed System Error queue entry."""

    code: int
    message: str
    raw_response: str

    @property
    def is_no_error(self) -> bool:
        """Return True for the Keysight `+0,"No error"` sentinel."""

        return self.code == 0


@dataclass(frozen=True)
class ErrorQueueResult:
    """A bounded SYSTem:ERRor? drain of an exactly recognized 33521B."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    errors: tuple[SystemErrorEntry, ...]
    read_count: int
    max_reads: int
    empty_confirmed: bool
    limit_reached: bool


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
                    current_command = "status"
                    try:
                        for command in STATUS_COMMON_QUERIES:
                            current_command = command
                            responses[command] = session.query(command)
                        function = _parse_status_function(
                            responses["SOURce1:FUNCtion?"]
                        )
                        if function == "DC":
                            function_queries = ()
                        elif function in {"NOIS", "NOISE"}:
                            function_queries = STATUS_NOISE_QUERIES
                        else:
                            function_queries = STATUS_FREQUENCY_AMPLITUDE_QUERIES
                        for command in function_queries:
                            current_command = command
                            responses[command] = session.query(command)
                    except Exception as exc:
                        primary_error = StatusQueryError(
                            f"Status query {current_command} failed or timed out.",
                            backend=backend_selection.name,
                            transport=transport,
                            identity=identity,
                        )
                        primary_cause = exc
                    if primary_error is None:
                        try:
                            frequency_hz = (
                                _parse_status_number(
                                    responses["SOURce1:FREQuency?"],
                                    "frequency",
                                )
                                if "SOURce1:FREQuency?" in responses
                                else None
                            )
                            amplitude_unit = (
                                _parse_status_unit(
                                    responses["SOURce1:VOLTage:UNIT?"]
                                )
                                if "SOURce1:VOLTage:UNIT?" in responses
                                else None
                            )
                            amplitude = (
                                _parse_status_number(
                                    responses["SOURce1:VOLTage?"],
                                    "amplitude",
                                )
                                if "SOURce1:VOLTage?" in responses
                                else None
                            )
                            bandwidth_hz = (
                                _parse_status_number(
                                    responses["SOURce1:FUNCtion:NOISe:BANDwidth?"],
                                    "noise bandwidth",
                                )
                                if "SOURce1:FUNCtion:NOISe:BANDwidth?" in responses
                                else None
                            )
                            result = StatusResult(
                                resource=resource_name,
                                backend=backend_selection.name,
                                transport=transport,
                                identity=identity,
                                output_state=_parse_status_output(
                                    responses["OUTPut1?"]
                                ),
                                function=function,
                                frequency_hz=frequency_hz,
                                amplitude=amplitude,
                                amplitude_unit=amplitude_unit,
                                bandwidth_hz=bandwidth_hz,
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
    phase_deg: object = 0.0,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SineConfigurationResult:
    """Validate and configure a Channel 1 sine wave while keeping output off."""

    frequency, amplitude, offset, normalized_load, phase, commands = _prepare_sine(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
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
        phase_deg=phase,
    )


def dry_run_sine(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
) -> SineDryRunResult:
    """Preview a validated Channel 1 sine configuration without VISA I/O."""

    _validate_dry_run_model(model, "sine")

    frequency, amplitude, offset, normalized_load, phase, commands = _prepare_sine(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    return SineDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        load=normalized_load,
        phase_deg=phase,
        commands=commands,
    )


def configure_sine_sweep(
    resource: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SineSweepConfigurationResult:
    """Validate and configure a Channel 1 sine frequency sweep."""

    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        commands,
    ) = _prepare_sine_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return SineSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
    )


def dry_run_sine_sweep(
    model: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
) -> SineSweepDryRunResult:
    """Preview a validated Channel 1 sine sweep without VISA I/O."""

    _validate_dry_run_model(model, "sine sweep")
    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        commands,
    ) = _prepare_sine_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    return SineSweepDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        commands=commands,
    )


def configure_square_sweep(
    resource: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    duty_cycle_percent: object = 50,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SquareSweepConfigurationResult:
    """Validate and configure a Channel 1 square frequency sweep."""

    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        duty_cycle,
        commands,
    ) = _prepare_square_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        duty_cycle_percent,
        load,
        phase_deg,
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return SquareSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
    )


def dry_run_square_sweep(
    model: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    duty_cycle_percent: object = 50,
) -> SquareSweepDryRunResult:
    """Preview a validated Channel 1 square sweep without VISA I/O."""

    _validate_dry_run_model(model, "square sweep")
    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        duty_cycle,
        commands,
    ) = _prepare_square_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        duty_cycle_percent,
        load,
        phase_deg,
    )
    return SquareSweepDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
        commands=commands,
    )


def configure_ramp_sweep(
    resource: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    symmetry_percent: object = 100,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> RampSweepConfigurationResult:
    """Validate and configure a Channel 1 ramp frequency sweep."""

    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        symmetry,
        commands,
    ) = _prepare_ramp_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        symmetry_percent,
        load,
        phase_deg,
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return RampSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        symmetry_percent=symmetry,
        load=normalized_load,
    )


def dry_run_ramp_sweep(
    model: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    symmetry_percent: object = 100,
) -> RampSweepDryRunResult:
    """Preview a validated Channel 1 ramp sweep without VISA I/O."""

    _validate_dry_run_model(model, "ramp sweep")
    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        symmetry,
        commands,
    ) = _prepare_ramp_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        symmetry_percent,
        load,
        phase_deg,
    )
    return RampSweepDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        symmetry_percent=symmetry,
        load=normalized_load,
        commands=commands,
    )


def configure_triangle_sweep(
    resource: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> TriangleSweepConfigurationResult:
    """Validate and configure a Channel 1 triangle frequency sweep."""

    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        commands,
    ) = _prepare_triangle_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return TriangleSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
    )


def dry_run_triangle_sweep(
    model: str,
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    hold_time_s: object = 0,
    return_time_s: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
) -> TriangleSweepDryRunResult:
    """Preview a validated Channel 1 triangle sweep without VISA I/O."""

    _validate_dry_run_model(model, "triangle sweep")
    (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        commands,
    ) = _prepare_triangle_sweep(
        start_frequency_hz,
        stop_frequency_hz,
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    return TriangleSweepDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source="immediate",
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        commands=commands,
    )


def _validate_dry_run_model(model: object, waveform: str) -> None:
    if (
        not isinstance(model, str)
        or model.strip().casefold() != CANONICAL_MODEL_ID
    ):
        raise UnsupportedInstrumentError(
            f"Unsupported {waveform} dry-run model; "
            "expected 'keysight-33521b'."
        )


def _prepare_sweep_timing(
    spacing: object,
    sweep_time_s: object,
    hold_time_s: object,
    return_time_s: object,
    *,
    waveform: str,
) -> tuple[str, float, float, float, str]:
    normalized_spacing = _normalize_sweep_spacing(spacing, waveform=waveform)
    sweep_time = _normalize_finite_number(
        sweep_time_s,
        "sweep time",
        waveform=waveform,
    )
    maximum_sweep_time = (
        SINE_SWEEP_LINEAR_MAX_TIME_S
        if normalized_spacing == "linear"
        else SINE_SWEEP_LOGARITHMIC_MAX_TIME_S
    )
    if not 0.001 <= sweep_time <= maximum_sweep_time:
        raise WaveformParameterError(
            f"{waveform} time must be between 0.001 s and "
            f"{_format_scpi_number(maximum_sweep_time)} s for "
            f"{normalized_spacing} spacing."
        )

    hold_time = _normalize_finite_number(
        hold_time_s,
        "hold time",
        waveform=waveform,
    )
    return_time = _normalize_finite_number(
        return_time_s,
        "return time",
        waveform=waveform,
    )
    if not 0 <= hold_time <= SINE_SWEEP_HOLD_RETURN_MAX_TIME_S:
        raise WaveformParameterError(
            f"{waveform} hold time must be between 0 s and 3600 s."
        )
    if not 0 <= return_time <= SINE_SWEEP_HOLD_RETURN_MAX_TIME_S:
        raise WaveformParameterError(
            f"{waveform} return time must be between 0 s and 3600 s."
        )
    total_time_s = sweep_time + hold_time + return_time
    if total_time_s > maximum_sweep_time:
        raise WaveformParameterError(
            f"{waveform} total time must not exceed "
            f"{_format_scpi_number(maximum_sweep_time)} s for "
            f"{normalized_spacing} spacing."
        )

    spacing_command = "LINear" if normalized_spacing == "linear" else "LOGarithmic"
    return (
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        spacing_command,
    )


def _build_sweep_tail(
    start_frequency: float,
    stop_frequency: float,
    spacing_command: str,
    sweep_time: float,
    hold_time: float,
    return_time: float,
) -> tuple[str, ...]:
    return (
        f"SOURce1:FREQuency:STARt {_format_scpi_number(start_frequency)}",
        f"SOURce1:FREQuency:STOP {_format_scpi_number(stop_frequency)}",
        f"SOURce1:SWEep:SPACing {spacing_command}",
        f"SOURce1:SWEep:TIME {_format_scpi_number(sweep_time)}",
        f"SOURce1:SWEep:HTIMe {_format_scpi_number(hold_time)}",
        f"SOURce1:SWEep:RTIMe {_format_scpi_number(return_time)}",
        "TRIGger1:SOURce IMMediate",
        "SOURce1:FREQuency:MODE SWEep",
    )


def _prepare_square_sweep(
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    hold_time_s: object,
    return_time_s: object,
    amplitude_vpp: object,
    offset_v: object,
    duty_cycle_percent: object,
    load: object,
    phase_deg: object,
) -> tuple[
    float,
    float,
    str,
    float,
    float,
    float,
    float,
    float,
    str,
    float,
    float,
    tuple[str, ...],
]:
    start_frequency = _normalize_finite_number(
        start_frequency_hz,
        "start frequency",
        waveform="Square sweep",
    )
    stop_frequency = _normalize_finite_number(
        stop_frequency_hz,
        "stop frequency",
        waveform="Square sweep",
    )
    if not 0.000001 <= start_frequency <= 30_000_000:
        raise WaveformParameterError(
            "Square sweep start frequency must be between "
            "0.000001 Hz and 30000000 Hz."
        )
    if not 0.000001 <= stop_frequency <= 30_000_000:
        raise WaveformParameterError(
            "Square sweep stop frequency must be between "
            "0.000001 Hz and 30000000 Hz."
        )
    if start_frequency == stop_frequency:
        raise WaveformParameterError(
            "Square sweep start and stop frequencies must not be equal."
        )

    maximum_frequency = max(start_frequency, stop_frequency)
    (
        _,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        base_commands,
    ) = _prepare_square(
        start_frequency,
        amplitude_vpp,
        offset_v,
        duty_cycle_percent,
        load,
        phase_deg,
        include_cw_mode=False,
        duty_cycle_validation_frequency_hz=maximum_frequency,
    )
    (
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        spacing_command,
    ) = _prepare_sweep_timing(
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        waveform="Square sweep",
    )
    commands = (*base_commands, *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
    ))
    return (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        duty_cycle,
        commands,
    )


def _prepare_ramp_sweep(
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    hold_time_s: object,
    return_time_s: object,
    amplitude_vpp: object,
    offset_v: object,
    symmetry_percent: object,
    load: object,
    phase_deg: object,
) -> tuple[
    float,
    float,
    str,
    float,
    float,
    float,
    float,
    float,
    str,
    float,
    float,
    tuple[str, ...],
]:
    start_frequency = _normalize_finite_number(
        start_frequency_hz,
        "start frequency",
        waveform="Ramp sweep",
    )
    stop_frequency = _normalize_finite_number(
        stop_frequency_hz,
        "stop frequency",
        waveform="Ramp sweep",
    )
    if not 0.000001 <= start_frequency <= 200_000:
        raise WaveformParameterError(
            "Ramp sweep start frequency must be between "
            "0.000001 Hz and 200000 Hz."
        )
    if not 0.000001 <= stop_frequency <= 200_000:
        raise WaveformParameterError(
            "Ramp sweep stop frequency must be between "
            "0.000001 Hz and 200000 Hz."
        )
    if start_frequency == stop_frequency:
        raise WaveformParameterError(
            "Ramp sweep start and stop frequencies must not be equal."
        )

    (
        _,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        base_commands,
    ) = _prepare_ramp(
        start_frequency,
        amplitude_vpp,
        offset_v,
        symmetry_percent,
        load,
        phase_deg,
        include_cw_mode=False,
    )
    (
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        spacing_command,
    ) = _prepare_sweep_timing(
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        waveform="Ramp sweep",
    )
    commands = (*base_commands, *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
    ))
    return (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        symmetry,
        commands,
    )


def _prepare_triangle_sweep(
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    hold_time_s: object,
    return_time_s: object,
    amplitude_vpp: object,
    offset_v: object,
    load: object,
    phase_deg: object,
) -> tuple[
    float,
    float,
    str,
    float,
    float,
    float,
    float,
    float,
    str,
    float,
    tuple[str, ...],
]:
    start_frequency = _normalize_finite_number(
        start_frequency_hz,
        "start frequency",
        waveform="Triangle sweep",
    )
    stop_frequency = _normalize_finite_number(
        stop_frequency_hz,
        "stop frequency",
        waveform="Triangle sweep",
    )
    if not 0.000001 <= start_frequency <= 200_000:
        raise WaveformParameterError(
            "Triangle sweep start frequency must be between "
            "0.000001 Hz and 200000 Hz."
        )
    if not 0.000001 <= stop_frequency <= 200_000:
        raise WaveformParameterError(
            "Triangle sweep stop frequency must be between "
            "0.000001 Hz and 200000 Hz."
        )
    if start_frequency == stop_frequency:
        raise WaveformParameterError(
            "Triangle sweep start and stop frequencies must not be equal."
        )

    (
        _,
        amplitude,
        offset,
        normalized_load,
        phase,
        base_commands,
    ) = _prepare_triangle(
        start_frequency,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
        include_cw_mode=False,
    )
    (
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        spacing_command,
    ) = _prepare_sweep_timing(
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        waveform="Triangle sweep",
    )
    commands = (*base_commands, *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
    ))
    return (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        commands,
    )


def _prepare_sine_sweep(
    start_frequency_hz: object,
    stop_frequency_hz: object,
    spacing: object,
    sweep_time_s: object,
    hold_time_s: object,
    return_time_s: object,
    amplitude_vpp: object,
    offset_v: object,
    load: object,
    phase_deg: object,
) -> tuple[
    float,
    float,
    str,
    float,
    float,
    float,
    float,
    float,
    str,
    float,
    tuple[str, ...],
]:
    (
        start_frequency,
        amplitude,
        offset,
        normalized_load,
        phase,
        base_commands,
    ) = _prepare_sine(
        start_frequency_hz,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
        include_cw_mode=False,
    )
    stop_frequency = _normalize_finite_number(
        stop_frequency_hz,
        "stop frequency",
        waveform="Sine sweep",
    )
    if not 0.000001 <= stop_frequency <= 30_000_000:
        raise WaveformParameterError(
            "Sine sweep stop frequency must be between "
            "0.000001 Hz and 30000000 Hz."
        )
    if start_frequency == stop_frequency:
        raise WaveformParameterError(
            "Sine sweep start and stop frequencies must not be equal."
        )

    (
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        spacing_command,
    ) = _prepare_sweep_timing(
        spacing,
        sweep_time_s,
        hold_time_s,
        return_time_s,
        waveform="Sine sweep",
    )
    commands = (*base_commands, *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
    ))
    return (
        start_frequency,
        stop_frequency,
        normalized_spacing,
        sweep_time,
        hold_time,
        return_time,
        amplitude,
        offset,
        normalized_load,
        phase,
        commands,
    )


def _normalize_sweep_spacing(value: object, *, waveform: str) -> str:
    if not isinstance(value, str):
        raise WaveformParameterError(
            f"{waveform} spacing must be linear or logarithmic."
        )
    normalized = value.strip().casefold()
    if normalized not in {"linear", "logarithmic"}:
        raise WaveformParameterError(
            f"{waveform} spacing must be linear or logarithmic."
        )
    return normalized


def _normalize_sine_sweep_spacing(value: object) -> str:
    return _normalize_sweep_spacing(value, waveform="Sine sweep")


def _prepare_sine(
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object,
    load: object,
    phase_deg: object,
    include_cw_mode: bool = True,
) -> tuple[float, float, float, str, float, tuple[str, ...]]:
    frequency = _normalize_finite_number(frequency_hz, "frequency")
    amplitude = _normalize_finite_number(amplitude_vpp, "amplitude")
    offset = _normalize_finite_number(offset_v, "offset")
    normalized_load = _normalize_load(load)
    phase = _normalize_phase_deg(phase_deg, waveform="Sine")

    if not 0.000001 <= frequency <= 30_000_000:
        raise WaveformParameterError(
            "Sine frequency must be between 0.000001 Hz and 30000000 Hz."
        )

    _validate_vpp_levels(amplitude, offset, normalized_load, "Sine")

    load_command = "50" if normalized_load == "50" else "INF"
    frequency_mode_command = (
        ("SOURce1:FREQuency:MODE CW",) if include_cw_mode else ()
    )
    commands = (
        "OUTPut1 OFF",
        *frequency_mode_command,
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SIN",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
        "UNIT:ANGLe DEGree",
        f"SOURce1:PHASe {_format_scpi_number(phase)}",
    )
    return frequency, amplitude, offset, normalized_load, phase, commands


def configure_square(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    duty_cycle_percent: object = 50,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SquareConfigurationResult:
    """Validate and configure a Channel 1 square wave while keeping output off."""

    (
        frequency,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        commands,
    ) = _prepare_square(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        duty_cycle_percent,
        load,
        phase_deg,
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
        phase_deg=phase,
    )


def dry_run_square(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    duty_cycle_percent: object = 50,
    load: object = 50,
    phase_deg: object = 0.0,
) -> SquareDryRunResult:
    """Preview a validated Channel 1 square configuration without VISA I/O."""

    _validate_dry_run_model(model, "square")
    (
        frequency,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        commands,
    ) = _prepare_square(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        duty_cycle_percent,
        load,
        phase_deg,
    )
    return SquareDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
        phase_deg=phase,
        commands=commands,
    )


def _prepare_square(
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object,
    duty_cycle_percent: object,
    load: object,
    phase_deg: object,
    *,
    include_cw_mode: bool = True,
    duty_cycle_validation_frequency_hz: float | None = None,
) -> tuple[float, float, float, float, str, float, tuple[str, ...]]:
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
    phase = _normalize_phase_deg(phase_deg, waveform="Square")

    if not 0.000001 <= frequency <= 30_000_000:
        raise WaveformParameterError(
            "Square frequency must be between 0.000001 Hz and 30000000 Hz."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Square")

    duty_frequency = (
        frequency
        if duty_cycle_validation_frequency_hz is None
        else duty_cycle_validation_frequency_hz
    )
    minimum_duty = max(0.01, 100 * 16e-9 * duty_frequency)
    maximum_duty = min(99.99, 100 * (1 - 16e-9 * duty_frequency))
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
            f"{_format_scpi_number(duty_frequency)} Hz."
        )

    load_command = "50" if normalized_load == "50" else "INF"
    frequency_mode_command = (
        ("SOURce1:FREQuency:MODE CW",) if include_cw_mode else ()
    )
    commands = (
        "OUTPut1 OFF",
        *frequency_mode_command,
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SQUare",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        "SOURce1:FUNCtion:SQUare:DCYCle "
        f"{_format_scpi_number(duty_cycle)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
        "UNIT:ANGLe DEGree",
        f"SOURce1:PHASe {_format_scpi_number(phase)}",
    )
    return (
        frequency,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        commands,
    )


def configure_ramp(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    symmetry_percent: object = 100,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> RampConfigurationResult:
    """Validate and configure a Channel 1 ramp wave while keeping output off."""

    (
        frequency,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        commands,
    ) = _prepare_ramp(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        symmetry_percent,
        load,
        phase_deg,
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
        phase_deg=phase,
    )


def dry_run_ramp(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    symmetry_percent: object = 100,
    load: object = 50,
    phase_deg: object = 0.0,
) -> RampDryRunResult:
    """Preview a validated Channel 1 ramp configuration without VISA I/O."""

    _validate_dry_run_model(model, "ramp")
    (
        frequency,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        commands,
    ) = _prepare_ramp(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        symmetry_percent,
        load,
        phase_deg,
    )
    return RampDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        symmetry_percent=symmetry,
        load=normalized_load,
        phase_deg=phase,
        commands=commands,
    )


def _prepare_ramp(
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object,
    symmetry_percent: object,
    load: object,
    phase_deg: object,
    *,
    include_cw_mode: bool = True,
) -> tuple[float, float, float, float, str, float, tuple[str, ...]]:
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
    phase = _normalize_phase_deg(phase_deg, waveform="Ramp")

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
    frequency_mode_command = (
        ("SOURce1:FREQuency:MODE CW",) if include_cw_mode else ()
    )
    commands = (
        "OUTPut1 OFF",
        *frequency_mode_command,
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FREQuency MINimum",
        "SOURce1:FUNCtion RAMP",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        "SOURce1:FUNCtion:RAMP:SYMMetry "
        f"{_format_scpi_number(symmetry)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
        "UNIT:ANGLe DEGree",
        f"SOURce1:PHASe {_format_scpi_number(phase)}",
    )
    return (
        frequency,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        commands,
    )


def configure_triangle(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> TriangleConfigurationResult:
    """Validate and configure a Channel 1 triangle wave while keeping output off."""

    frequency, amplitude, offset, normalized_load, phase, commands = _prepare_triangle(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    context = _write_to_supported_33521b(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        resource_manager_factory=resource_manager_factory,
    )
    return TriangleConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        load=normalized_load,
        phase_deg=phase,
    )


def dry_run_triangle(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
) -> TriangleDryRunResult:
    """Preview a validated Channel 1 triangle configuration without VISA I/O."""

    _validate_dry_run_model(model, "triangle")
    frequency, amplitude, offset, normalized_load, phase, commands = _prepare_triangle(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    return TriangleDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        load=normalized_load,
        phase_deg=phase,
        commands=commands,
    )


def _prepare_triangle(
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object,
    load: object,
    phase_deg: object,
    *,
    include_cw_mode: bool = True,
) -> tuple[float, float, float, str, float, tuple[str, ...]]:
    frequency = _normalize_finite_number(
        frequency_hz,
        "frequency",
        waveform="Triangle",
    )
    amplitude = _normalize_finite_number(
        amplitude_vpp,
        "amplitude",
        waveform="Triangle",
    )
    offset = _normalize_finite_number(offset_v, "offset", waveform="Triangle")
    normalized_load = _normalize_load(load, waveform="Triangle")
    phase = _normalize_phase_deg(phase_deg, waveform="Triangle")

    if not 0.000001 <= frequency <= 200_000:
        raise WaveformParameterError(
            "Triangle frequency must be between 0.000001 Hz and 200000 Hz."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Triangle")

    load_command = "50" if normalized_load == "50" else "INF"
    frequency_mode_command = (
        ("SOURce1:FREQuency:MODE CW",) if include_cw_mode else ()
    )
    commands = (
        "OUTPut1 OFF",
        *frequency_mode_command,
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FREQuency MINimum",
        "SOURce1:FUNCtion TRIangle",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
        "UNIT:ANGLe DEGree",
        f"SOURce1:PHASe {_format_scpi_number(phase)}",
    )
    return frequency, amplitude, offset, normalized_load, phase, commands


def configure_pulse(
    resource: str,
    frequency_hz: object,
    amplitude_vpp: object,
    pulse_width_s: object,
    offset_v: object = 0,
    edge_time_s: object = None,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    leading_edge_s: object = None,
    trailing_edge_s: object = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> PulseConfigurationResult:
    """Validate and configure a Channel 1 pulse wave while keeping output off."""

    (
        frequency,
        amplitude,
        offset,
        pulse_width,
        edge_time,
        leading_edge,
        trailing_edge,
        normalized_load,
        phase,
        commands,
    ) = _prepare_pulse(
        frequency_hz,
        amplitude_vpp,
        pulse_width_s,
        offset_v,
        edge_time_s,
        load,
        phase_deg,
        leading_edge_s,
        trailing_edge_s,
    )

    def operate(
        session: VisaSession,
        context: IdentificationResult,
    ) -> tuple[float, float, float, float, float]:
        def write_pulse_command(command: str, output_state: str | None) -> None:
            try:
                session.write(command)
            except Exception as exc:
                raise VisaWriteError(
                    "Could not apply the requested instrument control write.",
                    output_state=output_state,
                ) from exc

        write_pulse_command(commands[0], None)
        for command in commands[1:10]:
            write_pulse_command(command, "off")

        if edge_time is not None:
            maximum = _query_pulse_verification(
                session,
                "SOURce1:FUNCtion:PULSe:TRANsition? MAXimum",
                "dynamic BOTH edge maximum",
                _parse_pulse_verification_number,
            )
            _validate_pulse_edge_maximum(
                edge_time,
                maximum,
                "BOTH",
                context,
            )
            remaining_commands = commands[10:]
        else:
            leading_maximum = _query_pulse_verification(
                session,
                "SOURce1:FUNCtion:PULSe:TRANsition:LEADing? MAXimum",
                "dynamic leading edge maximum",
                _parse_pulse_verification_number,
            )
            _validate_pulse_edge_maximum(
                leading_edge,
                leading_maximum,
                "leading",
                context,
            )
            write_pulse_command(commands[10], "off")

            trailing_maximum = _query_pulse_verification(
                session,
                "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling? MAXimum",
                "dynamic trailing edge maximum",
                _parse_pulse_verification_number,
            )
            _validate_pulse_edge_maximum(
                trailing_edge,
                trailing_maximum,
                "trailing",
                context,
            )
            remaining_commands = commands[11:]

        for command in remaining_commands:
            write_pulse_command(command, "off")

        output_state = _query_pulse_verification(
            session,
            "OUTPut1?",
            "output state",
            _parse_status_output,
        )
        function = _query_pulse_verification(
            session,
            "SOURce1:FUNCtion?",
            "function",
            _parse_status_function,
        )
        readback_frequency = _query_pulse_verification(
            session,
            "SOURce1:FREQuency?",
            "frequency",
            _parse_pulse_verification_number,
        )
        readback_width = _query_pulse_verification(
            session,
            "SOURce1:FUNCtion:PULSe:WIDTh?",
            "pulse width",
            _parse_pulse_verification_number,
        )
        if edge_time is not None:
            readback_edge = _query_pulse_verification(
                session,
                "SOURce1:FUNCtion:PULSe:TRANsition?",
                "BOTH edge",
                _parse_pulse_verification_number,
            )
            readback_leading = readback_edge
            readback_trailing = readback_edge
        else:
            readback_leading = _query_pulse_verification(
                session,
                "SOURce1:FUNCtion:PULSe:TRANsition:LEADing?",
                "leading edge",
                _parse_pulse_verification_number,
            )
            readback_trailing = _query_pulse_verification(
                session,
                "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling?",
                "trailing edge",
                _parse_pulse_verification_number,
            )
        readback_phase = _query_pulse_verification(
            session,
            "SOURce1:PHASe?",
            "phase",
            _parse_phase_verification_number,
        )

        if output_state != "off":
            raise WaveformVerificationError(
                f"Pulse readback reported output state {output_state!r}; expected 'off'.",
                backend=context.backend,
                transport=context.transport,
                identity=context.identity,
                output_state=output_state,
            )
        if function not in {"PULS", "PULSE"}:
            raise WaveformVerificationError(
                f"Pulse readback reported function {function!r}; expected PULS or PULSE.",
                backend=context.backend,
                transport=context.transport,
                identity=context.identity,
                output_state="off",
            )
        _verify_pulse_readback(
            "frequency",
            frequency,
            readback_frequency,
            rel_tolerance=0.0,
            abs_tolerance=PULSE_FREQUENCY_ABS_TOLERANCE_HZ,
            context=context,
        )
        _verify_pulse_readback(
            "pulse width",
            pulse_width,
            readback_width,
            rel_tolerance=PULSE_TIMING_REL_TOLERANCE,
            abs_tolerance=PULSE_TIMING_ABS_TOLERANCE_S,
            context=context,
        )
        if edge_time is not None:
            _verify_pulse_readback(
                "BOTH edge",
                edge_time,
                readback_leading,
                rel_tolerance=PULSE_TIMING_REL_TOLERANCE,
                abs_tolerance=PULSE_TIMING_ABS_TOLERANCE_S,
                context=context,
            )
        else:
            _verify_pulse_readback(
                "leading edge",
                leading_edge,
                readback_leading,
                rel_tolerance=PULSE_TIMING_REL_TOLERANCE,
                abs_tolerance=PULSE_TIMING_ABS_TOLERANCE_S,
                context=context,
            )
            _verify_pulse_readback(
                "trailing edge",
                trailing_edge,
                readback_trailing,
                rel_tolerance=PULSE_TIMING_REL_TOLERANCE,
                abs_tolerance=PULSE_TIMING_ABS_TOLERANCE_S,
                context=context,
            )
        _verify_pulse_readback(
            "phase",
            phase,
            readback_phase,
            rel_tolerance=0.0,
            abs_tolerance=0.0,
            context=context,
        )
        return (
            readback_frequency,
            readback_width,
            readback_leading,
            readback_trailing,
            readback_phase,
        )

    context, readback = _run_on_supported_33521b(
        resource,
        backend,
        operate,
        output_state_after_operation="off",
        resource_manager_factory=resource_manager_factory,
    )
    (
        readback_frequency,
        readback_width,
        readback_leading,
        readback_trailing,
        readback_phase,
    ) = readback
    shared_edge = readback_leading if edge_time is not None else None
    return PulseConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequency_hz=readback_frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        pulse_width_s=readback_width,
        edge_time_s=shared_edge,
        load=normalized_load,
        phase_deg=readback_phase,
        leading_edge_s=readback_leading,
        trailing_edge_s=readback_trailing,
    )


def dry_run_pulse(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    pulse_width_s: object,
    offset_v: object = 0,
    edge_time_s: object = None,
    load: object = 50,
    phase_deg: object = 0.0,
    leading_edge_s: object = None,
    trailing_edge_s: object = None,
) -> PulseDryRunResult:
    """Preview a validated Channel 1 pulse configuration without VISA I/O."""

    _validate_dry_run_model(model, "pulse")
    (
        frequency,
        amplitude,
        offset,
        pulse_width,
        edge_time,
        leading_edge,
        trailing_edge,
        normalized_load,
        phase,
        commands,
    ) = _prepare_pulse(
        frequency_hz,
        amplitude_vpp,
        pulse_width_s,
        offset_v,
        edge_time_s,
        load,
        phase_deg,
        leading_edge_s,
        trailing_edge_s,
    )
    return PulseDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        pulse_width_s=pulse_width,
        edge_time_s=edge_time,
        load=normalized_load,
        phase_deg=phase,
        leading_edge_s=leading_edge,
        trailing_edge_s=trailing_edge,
        commands=commands,
    )


def _prepare_pulse(
    frequency_hz: object,
    amplitude_vpp: object,
    pulse_width_s: object,
    offset_v: object,
    edge_time_s: object,
    load: object,
    phase_deg: object,
    leading_edge_s: object,
    trailing_edge_s: object,
) -> tuple[
    float,
    float,
    float,
    float,
    float | None,
    float,
    float,
    str,
    float,
    tuple[str, ...],
]:
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
    edge_time, leading_edge, trailing_edge = _normalize_pulse_edges(
        edge_time_s,
        leading_edge_s,
        trailing_edge_s,
    )
    normalized_load = _normalize_load(load, waveform="Pulse")
    phase = _normalize_phase_deg(phase_deg, waveform="Pulse")

    if not 0.000001 <= frequency <= 30_000_000:
        raise WaveformParameterError(
            "Pulse frequency must be between 0.000001 Hz and 30000000 Hz."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Pulse")

    period = 1 / frequency
    edge_margin = 0.625 * (leading_edge + trailing_edge)
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
    edge_commands = (
        (
            f"SOURce1:FUNCtion:PULSe:TRANsition:LEADing "
            f"{_format_scpi_number(leading_edge)}",
            f"SOURce1:FUNCtion:PULSe:TRANsition:TRAiling "
            f"{_format_scpi_number(trailing_edge)}",
        )
        if edge_time is None
        else (
            "SOURce1:FUNCtion:PULSe:TRANsition:BOTH "
            f"{_format_scpi_number(edge_time)}",
        )
    )
    commands = (
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        f"OUTPut1:LOAD {load_command}",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion:PULSe:HOLD WIDTh",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH MINimum",
        "SOURce1:FUNCtion:PULSe:WIDTh MINimum",
        "SOURce1:FUNCtion PULSe",
        f"SOURce1:FREQuency {_format_scpi_number(frequency)}",
        "SOURce1:FUNCtion:PULSe:WIDTh "
        f"{_format_scpi_number(pulse_width)}",
        *edge_commands,
        f"SOURce1:VOLTage {_format_scpi_number(amplitude)}",
        f"SOURce1:VOLTage:OFFSet {_format_scpi_number(offset)}",
        "UNIT:ANGLe DEGree",
        f"SOURce1:PHASe {_format_scpi_number(phase)}",
    )
    return (
        frequency,
        amplitude,
        offset,
        pulse_width,
        edge_time,
        leading_edge,
        trailing_edge,
        normalized_load,
        phase,
        commands,
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

    voltage, normalized_load, commands = _prepare_dc(voltage_v, load)
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


def dry_run_dc(
    model: str,
    voltage_v: object,
    load: object = 50,
) -> DcDryRunResult:
    """Preview a validated Channel 1 DC configuration without VISA I/O."""

    _validate_dry_run_model(model, "DC")
    voltage, normalized_load, commands = _prepare_dc(voltage_v, load)
    return DcDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        voltage_v=voltage,
        load=normalized_load,
        commands=commands,
    )


def _prepare_dc(
    voltage_v: object,
    load: object,
) -> tuple[float, str, tuple[str, ...]]:
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
    return voltage, normalized_load, commands


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

    (
        amplitude,
        offset,
        bandwidth,
        normalized_load,
        commands,
    ) = _prepare_noise(
        amplitude_vpp,
        bandwidth_hz,
        offset_v,
        load,
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


def dry_run_noise(
    model: str,
    amplitude_vpp: object,
    bandwidth_hz: object,
    offset_v: object = 0,
    load: object = 50,
) -> NoiseDryRunResult:
    """Preview a validated Channel 1 noise configuration without VISA I/O."""

    _validate_dry_run_model(model, "noise")
    (
        amplitude,
        offset,
        bandwidth,
        normalized_load,
        commands,
    ) = _prepare_noise(
        amplitude_vpp,
        bandwidth_hz,
        offset_v,
        load,
    )
    return NoiseDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        amplitude_vpp=amplitude,
        offset_v=offset,
        bandwidth_hz=bandwidth,
        load=normalized_load,
        commands=commands,
    )


def _prepare_noise(
    amplitude_vpp: object,
    bandwidth_hz: object,
    offset_v: object,
    load: object,
) -> tuple[float, float, float, str, tuple[str, ...]]:
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
    return (
        amplitude,
        offset,
        bandwidth,
        normalized_load,
        commands,
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

    (
        bit_rate,
        amplitude,
        normalized_pattern,
        offset,
        edge_time,
        normalized_load,
        commands,
    ) = _prepare_prbs(
        bit_rate_bps,
        amplitude_vpp,
        pattern,
        offset_v,
        edge_time_s,
        load,
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


def dry_run_prbs(
    model: str,
    bit_rate_bps: object,
    amplitude_vpp: object,
    pattern: object = "PN7",
    offset_v: object = 0,
    edge_time_s: object = 8.4e-9,
    load: object = 50,
) -> PrbsDryRunResult:
    """Preview a validated Channel 1 PRBS configuration without VISA I/O."""

    _validate_dry_run_model(model, "PRBS")
    (
        bit_rate,
        amplitude,
        normalized_pattern,
        offset,
        edge_time,
        normalized_load,
        commands,
    ) = _prepare_prbs(
        bit_rate_bps,
        amplitude_vpp,
        pattern,
        offset_v,
        edge_time_s,
        load,
    )
    return PrbsDryRunResult(
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        bit_rate_bps=bit_rate,
        amplitude_vpp=amplitude,
        pattern=normalized_pattern,
        offset_v=offset,
        edge_time_s=edge_time,
        load=normalized_load,
        commands=commands,
    )


def _prepare_prbs(
    bit_rate_bps: object,
    amplitude_vpp: object,
    pattern: object,
    offset_v: object,
    edge_time_s: object,
    load: object,
) -> tuple[float, float, str, float, float, str, tuple[str, ...]]:
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
    return (
        bit_rate,
        amplitude,
        normalized_pattern,
        offset,
        edge_time,
        normalized_load,
        commands,
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


def _normalize_phase_deg(value: object, *, waveform: str) -> float:
    phase = _normalize_finite_number(value, "phase", waveform=waveform)
    if not -360.0 <= phase <= 360.0:
        raise WaveformParameterError(
            f"{waveform} phase must be between -360 and 360 degrees."
        )
    return phase


def resolve_voltage_inputs(
    amplitude_vpp: object,
    offset_v: object | None,
    high_level_v: object | None,
    low_level_v: object | None,
    load: object,
    waveform: str,
) -> tuple[float, float]:
    """Resolve one waveform voltage representation to amplitude and offset."""

    has_high = high_level_v is not None
    has_low = low_level_v is not None
    if has_high != has_low:
        raise WaveformParameterError(
            f"{waveform} high_level_v and low_level_v must be provided together."
        )

    if has_high:
        if amplitude_vpp is not None or offset_v is not None:
            raise WaveformParameterError(
                f"{waveform} high/low voltage cannot be combined with "
                "amplitude_vpp or offset_v."
            )
        high = _normalize_finite_number(
            high_level_v,
            "high level",
            waveform=waveform,
        )
        low = _normalize_finite_number(
            low_level_v,
            "low level",
            waveform=waveform,
        )
        if high <= low:
            raise WaveformParameterError(
                f"{waveform} high level must be greater than low level."
            )
        amplitude = high - low
        offset = (high + low) / 2
    else:
        if amplitude_vpp is None:
            raise WaveformParameterError(
                f"{waveform} requires amplitude_vpp or a complete high/low pair."
            )
        amplitude = _normalize_finite_number(
            amplitude_vpp,
            "amplitude",
            waveform=waveform,
        )
        offset = (
            0.0
            if offset_v is None
            else _normalize_finite_number(offset_v, "offset", waveform=waveform)
        )

    normalized_load = _normalize_load(load, waveform=waveform)
    _validate_vpp_levels(amplitude, offset, normalized_load, waveform)
    return amplitude, offset


def _normalize_pulse_edges(
    edge_time_s: object,
    leading_edge_s: object,
    trailing_edge_s: object,
) -> tuple[float | None, float, float]:
    has_independent_edge = (
        leading_edge_s is not None or trailing_edge_s is not None
    )
    if has_independent_edge:
        if edge_time_s is not None:
            raise WaveformParameterError(
                "Pulse edge_time_s cannot be combined with leading_edge_s "
                "or trailing_edge_s."
            )
        if leading_edge_s is None or trailing_edge_s is None:
            raise WaveformParameterError(
                "Pulse leading_edge_s and trailing_edge_s must be provided together."
            )
        leading = _normalize_finite_number(
            leading_edge_s,
            "leading edge time",
            waveform="Pulse",
        )
        trailing = _normalize_finite_number(
            trailing_edge_s,
            "trailing edge time",
            waveform="Pulse",
        )
        _validate_pulse_edge_range(leading, "leading")
        _validate_pulse_edge_range(trailing, "trailing")
        return None, leading, trailing

    edge = 10e-9 if edge_time_s is None else _normalize_finite_number(
        edge_time_s,
        "edge time",
        waveform="Pulse",
    )
    _validate_pulse_edge_range(edge, "")
    return edge, edge, edge


def _validate_pulse_edge_range(edge_time: float, label: str) -> None:
    if 8.4e-9 <= edge_time <= 1e-6:
        return
    edge_label = f" {label}" if label else ""
    raise WaveformParameterError(
        f"Pulse{edge_label} edge time must be between "
        "0.0000000084 s and 0.000001 s."
    )


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


def _query_pulse_verification(
    session: VisaSession,
    command: str,
    field: str,
    parser: Callable[[object], object],
) -> object:
    try:
        response = session.query(command)
    except Exception as exc:
        raise WaveformVerificationError(
            f"Pulse verification query {command} failed or timed out.",
            output_state="off",
        ) from exc
    try:
        return parser(response)
    except Exception as exc:
        raise WaveformVerificationError(
            f"Malformed Pulse verification response for {field}.",
            output_state="off",
        ) from exc


def _validate_pulse_edge_maximum(
    requested: float,
    maximum: float,
    label: str,
    context: IdentificationResult,
) -> None:
    if requested <= maximum or math.isclose(
        requested,
        maximum,
        rel_tol=PULSE_TIMING_REL_TOLERANCE,
        abs_tol=PULSE_TIMING_ABS_TOLERANCE_S,
    ):
        return
    raise WaveformVerificationError(
        f"Requested {label} edge time "
        f"{_format_scpi_number(requested)} s exceeds instrument maximum "
        f"{_format_scpi_number(maximum)} s.",
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        output_state="off",
    )


def _parse_pulse_verification_number(response: object) -> float:
    if not isinstance(response, str) or not response.strip():
        raise ValueError("response must be a non-empty string")
    try:
        value = float(response.strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("response must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("response must be a finite positive number")
    return value


def _parse_phase_verification_number(response: object) -> float:
    if not isinstance(response, str) or not response.strip():
        raise ValueError("response must be a non-empty string")
    try:
        value = float(response.strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("response must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError("response must be a finite number")
    return value


def _verify_pulse_readback(
    field: str,
    requested: float,
    actual: float,
    *,
    rel_tolerance: float,
    abs_tolerance: float,
    context: IdentificationResult,
) -> None:
    if math.isclose(
        requested,
        actual,
        rel_tol=rel_tolerance,
        abs_tol=abs_tolerance,
    ):
        return
    raise WaveformVerificationError(
        f"Pulse readback mismatch for {field}: requested "
        f"{_format_scpi_number(requested)}, actual {_format_scpi_number(actual)}.",
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        output_state="off",
    )


def _run_on_supported_33521b(
    resource: str,
    backend: str | None,
    operation: Callable[[VisaSession, IdentificationResult], object],
    *,
    output_state_after_operation: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None,
) -> tuple[IdentificationResult, object]:
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
    context: IdentificationResult | None = None
    operation_result: object | None = None
    operation_completed = False
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
                    context = IdentificationResult(
                        resource=resource_name,
                        backend=backend_selection.name,
                        transport=transport,
                        identity=identity,
                    )
                    try:
                        operation_result = operation(session, context)
                        operation_completed = True
                    except WavegenError as exc:
                        primary_error = exc.attach_context(
                            backend=backend_selection.name,
                            transport=transport,
                            identity=identity,
                        )
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
        if output_state_after_operation == "on":
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
            output_state=output_state_after_operation,
        )
    if context is None or not operation_completed:  # pragma: no cover - defensive invariant
        raise RuntimeError("instrument control completed without an identity or error")
    return context, operation_result


def _write_to_supported_33521b(
    resource: str,
    backend: str | None,
    commands: tuple[str, ...],
    *,
    output_state_after_writes: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None,
) -> IdentificationResult:
    def write_commands(
        session: VisaSession,
        _context: IdentificationResult,
    ) -> None:
        for command in commands:
            session.write(command)

    context, _ = _run_on_supported_33521b(
        resource,
        backend,
        write_commands,
        output_state_after_operation=output_state_after_writes,
        resource_manager_factory=resource_manager_factory,
    )
    return context


def _normalize_error_queue_max_reads(value: object) -> int:
    """Return a validated 1..100 SYSTem:ERRor? read cap."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_reads must be an integer between 1 and 100.")
    if not ERROR_QUEUE_MAX_READS_MIN <= value <= ERROR_QUEUE_MAX_READS_MAX:
        raise ValueError("max_reads must be an integer between 1 and 100.")
    return int(value)


def _parse_error_queue_entry(raw_response: str) -> SystemErrorEntry:
    """Parse one SYSTem:ERRor? response into code and message."""

    if not isinstance(raw_response, str) or not raw_response.strip():
        raise ErrorQueueQueryError(
            "SYSTem:ERRor? returned an empty response."
        )

    try:
        rows = list(csv.reader(StringIO(raw_response), strict=True))
    except csv.Error as exc:
        raise ErrorQueueQueryError(
            "Malformed SYSTem:ERRor? response: invalid CSV."
        ) from exc
    if len(rows) != 1 or len(rows[0]) != 2:
        raise ErrorQueueQueryError(
            "Malformed SYSTem:ERRor? response: expected code and message."
        )

    code_field, message = rows[0]
    code_field = code_field.strip()
    sign = code_field[:1]
    digits = code_field[1:] if sign in {"+", "-"} else code_field
    if not digits or not digits.isascii() or not digits.isdecimal():
        raise ErrorQueueQueryError(
            "Malformed SYSTem:ERRor? response: missing numeric code."
        )
    try:
        code = int(code_field)
    except ValueError as exc:
        raise ErrorQueueQueryError(
            "Malformed SYSTem:ERRor? response: non-integer code."
        ) from exc
    return SystemErrorEntry(
        code=code,
        message=message,
        raw_response=raw_response,
    )


def read_error_queue(
    resource: str,
    backend: str | None = None,
    *,
    max_reads: int = DEFAULT_ERROR_QUEUE_MAX_READS,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> ErrorQueueResult:
    """Drain the SYSTem:ERRor? queue of one exactly recognized 33521B."""

    normalized_max_reads = _normalize_error_queue_max_reads(max_reads)
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
    result: ErrorQueueResult | None = None
    primary_error: WavegenError | None = None
    primary_cause: Exception | None = None
    current_command = "status"
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
                    errors: list[SystemErrorEntry] = []
                    read_count = 0
                    empty_confirmed = False
                    limit_reached = False
                    try:
                        for _ in range(normalized_max_reads):
                            current_command = SYSTEM_ERROR_QUERY
                            raw_response = session.query(current_command)
                            read_count += 1
                            entry = _parse_error_queue_entry(raw_response)
                            if entry.code == ERROR_QUEUE_NO_ERROR_CODE:
                                empty_confirmed = True
                                break
                            errors.append(entry)
                        else:
                            limit_reached = True
                    except ErrorQueueQueryError as exc:
                        primary_error = exc.attach_context(
                            backend=backend_selection.name,
                            transport=transport,
                            identity=identity,
                        )
                    except Exception as exc:
                        primary_error = ErrorQueueQueryError(
                            f"SYSTem:ERRor? query {current_command} failed or timed out.",
                            backend=backend_selection.name,
                            transport=transport,
                            identity=identity,
                        )
                        primary_cause = exc
                    if primary_error is None:
                        result = ErrorQueueResult(
                            resource=resource_name,
                            backend=backend_selection.name,
                            transport=transport,
                            identity=identity,
                            errors=tuple(errors),
                            read_count=read_count,
                            max_reads=normalized_max_reads,
                            empty_confirmed=empty_confirmed,
                            limit_reached=limit_reached,
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
        raise RuntimeError("error queue query completed without a result or error")
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
