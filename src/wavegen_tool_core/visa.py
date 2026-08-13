"""Safe VISA lifecycles for explicit live resource access."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import csv
from dataclasses import dataclass, replace
from io import StringIO
import math
from typing import Protocol

from pyvisa.constants import RENLineOperation

from wavegen_tool_core.backends import (
    PYVISA_PY_BACKEND,
    SYSTEM_BACKEND,
    VisaBackend,
    normalize_backend,
    validate_backend_transport,
)
from wavegen_tool_core.capabilities import (
    WavegenCapabilities,
    capabilities_for_model_id,
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
    InstrumentIdentity,
    ModelInfo,
    SUPPORT_POLICY_MODE_PRODUCT,
    model_info_for_model_id,
    parse_idn,
    registered_model_ids,
    resolve_supported_identity,
)
from wavegen_tool_core.simulator import (
    SimulatedResourceManager,
    SimulatedResourceManagerFactory,
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
RAMP_TRIANGLE_MAX_FREQUENCY_HZ = 200_000.0
FREQUENCY_LIST_MIN_POINTS = 1
FREQUENCY_LIST_MAX_POINTS = 128
FREQUENCY_LIST_MIN_FREQUENCY_HZ = 0.000001
FREQUENCY_LIST_MIN_DWELL_S = 0.000001
FREQUENCY_LIST_MAX_DWELL_S = 1000.0
FM_MAX_DEVIATION_HZ = 15_000_000.0
MODULATION_MIN_FREQUENCY_HZ = 0.000001
FSK_MIN_RATE_HZ = 0.000125
FSK_MAX_RATE_HZ = 1_000_000.0
BPSK_MIN_RATE_HZ = 0.001
BPSK_MAX_RATE_HZ = 1_000_000.0
PWM_MAX_DEVIATION_S = 500_000.0
PULSE_MIN_WIDTH_S = 16e-9
BURST_MIN_COUNT = 1
BURST_MAX_COUNT = 100_000_000
BURST_MIN_PERIOD_S = 1e-6
BURST_MAX_PERIOD_S = 8000.0
# Deliberately follows the SCPI Programming Reference BURSt subsystem (page 225),
# rather than the 126 uHz value elsewhere in Operating Information, to fail closed.
BURST_MIN_CARRIER_RATE_HZ = 0.002001
BURST_MAX_SINE_SQUARE_FREQUENCY_HZ = 6_000_000.0
BURST_PERIOD_MARGIN_S = 1e-6
TRIGGER_MIN_TIMER_S = 1e-6
TRIGGER_MAX_TIMER_S = 8000.0
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
STATUS_PRBS_QUERIES = (
    "SOURce1:FUNCtion:PRBS:BRATe?",
    "SOURce1:VOLTage:UNIT?",
    "SOURce1:VOLTage?",
)

def _validate_channel(
    channel: object,
    capabilities: WavegenCapabilities | None = None,
    model_name: str | None = None,
) -> int:
    if isinstance(channel, bool) or not isinstance(channel, int):
        raise WaveformParameterError("channel must be 1 or 2.")
    if channel not in (1, 2):
        raise WaveformParameterError("channel must be 1 or 2.")
    if capabilities is not None and channel > capabilities.channel_count:
        label = f"Keysight {model_name}" if model_name else "this model"
        raise WaveformParameterError(
            f"Channel {channel} is not supported on {label}."
        )
    return channel


def _check_independent_channel_guard(
    session: VisaSession,
    capabilities: WavegenCapabilities,
    context: IdentificationResult,
) -> None:
    if capabilities.channel_count <= 1:
        return

    try:
        freq_coup = session.query("SOURce1:FREQuency:COUPle:STATe?").strip().upper()
    except Exception as exc:
        raise WaveformVerificationError(
            "Failed to query frequency coupling state.",
            backend=context.backend,
            transport=context.transport,
            identity=context.identity,
        ) from exc
    if freq_coup not in {"0", "OFF"}:
        raise WaveformVerificationError(
            "Independent channel operation rejected: frequency coupling is active.",
            backend=context.backend,
            transport=context.transport,
            identity=context.identity,
        )

    try:
        volt_coup = session.query("SOURce1:VOLTage:COUPle:STATe?").strip().upper()
    except Exception as exc:
        raise WaveformVerificationError(
            "Failed to query voltage coupling state.",
            backend=context.backend,
            transport=context.transport,
            identity=context.identity,
        ) from exc
    if volt_coup not in {"0", "OFF"}:
        raise WaveformVerificationError(
            "Independent channel operation rejected: voltage coupling is active.",
            backend=context.backend,
            transport=context.transport,
            identity=context.identity,
        )

    for trk_cmd in ("SOURce1:TRACk?", "SOURce2:TRACk?"):
        try:
            track_resp = session.query(trk_cmd).strip().upper()
        except Exception as exc:
            raise WaveformVerificationError(
                "Failed to query channel tracking state.",
                backend=context.backend,
                transport=context.transport,
                identity=context.identity,
            ) from exc
        if track_resp != "OFF":
            raise WaveformVerificationError(
                "Independent channel operation rejected: channel tracking is active.",
                backend=context.backend,
                transport=context.transport,
                identity=context.identity,
            )


def _channelize_commands(commands: tuple[str, ...], channel: int) -> tuple[str, ...]:
    """Apply the selected channel to channel-specific commands."""

    if channel == 1:
        return commands
    return tuple(
        command.replace("SOURce1", "SOURce2")
        .replace("OUTPut1", "OUTPut2")
        .replace("TRIGger1", "TRIGger2")
        for command in commands
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

    def control_ren(self, mode: RENLineOperation) -> None:
        """Control the VISA remote-enable line."""

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
    """A successful read-only status readback."""

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
    channel: int = 1
    bit_rate_bps: float | None = None


@dataclass(frozen=True)
class AMConfig:
    """Internal sine amplitude-modulation settings."""

    modulation_frequency_hz: object
    depth_percent: object
    am_type: object = "normal"


@dataclass(frozen=True)
class FMConfig:
    """Internal sine frequency-modulation settings."""

    modulation_frequency_hz: object
    deviation_hz: object


@dataclass(frozen=True)
class PMConfig:
    """Internal sine phase-modulation settings."""

    modulation_frequency_hz: object
    deviation_deg: object


@dataclass(frozen=True)
class FSKConfig:
    """Internal frequency-shift-keying settings."""

    hop_frequency_hz: object
    rate_hz: object


@dataclass(frozen=True)
class BPSKConfig:
    """Internal binary phase-shift-keying settings."""

    phase_shift_deg: object
    rate_hz: object


@dataclass(frozen=True)
class PWMConfig:
    """Internal sine pulse-width-modulation settings."""

    modulation_frequency_hz: object
    deviation_s: object


@dataclass(frozen=True)
class CountedBurstConfig:
    """Triggered counted-burst settings."""

    count: object
    period_s: object = None
    trigger_source: object = "immediate"
    trigger_timer_s: object = None


@dataclass(frozen=True)
class SineConfigurationResult:
    """A successful sine configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class SineDryRunResult:
    """A hardware-free preview of a sine configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class SineSweepConfigurationResult:
    """A successful selected-channel sine frequency sweep configuration."""

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
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class SineSweepDryRunResult:
    """A hardware-free preview of a selected-channel sine frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class SquareSweepConfigurationResult:
    """A successful selected-channel square frequency sweep configuration."""

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
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    duty_cycle_percent: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class SquareSweepDryRunResult:
    """A hardware-free preview of a selected-channel square frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    duty_cycle_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class RampSweepConfigurationResult:
    """A successful selected-channel ramp frequency sweep configuration."""

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
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    symmetry_percent: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class RampSweepDryRunResult:
    """A hardware-free preview of a selected-channel ramp frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    symmetry_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class TriangleSweepConfigurationResult:
    """A successful selected-channel triangle frequency sweep configuration."""

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
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class TriangleSweepDryRunResult:
    """A hardware-free preview of a selected-channel triangle frequency sweep."""

    model: str
    canonical_model_id: str
    start_frequency_hz: float
    stop_frequency_hz: float
    spacing: str
    sweep_time_s: float
    hold_time_s: float
    return_time_s: float
    trigger_source: str
    trigger_timer_s: float | None
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class SquareConfigurationResult:
    """A successful square configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class SquareDryRunResult:
    """A hardware-free preview of a square configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class RampConfigurationResult:
    """A successful ramp configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class RampDryRunResult:
    """A hardware-free preview of a ramp configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class TriangleConfigurationResult:
    """A successful triangle configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class TriangleDryRunResult:
    """A hardware-free preview of a triangle configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    fm: FMConfig | None = None
    pm: PMConfig | None = None
    fsk: FSKConfig | None = None
    bpsk: BPSKConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class PulseConfigurationResult:
    """A successful pulse configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    pwm: PWMConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class PulseDryRunResult:
    """A hardware-free preview of a pulse configuration."""

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
    channel: int = 1
    am: AMConfig | None = None
    pwm: PWMConfig | None = None
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class DcConfigurationResult:
    """A successful DC voltage configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    voltage_v: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class DcDryRunResult:
    """A hardware-free preview of a DC configuration."""

    model: str
    canonical_model_id: str
    voltage_v: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class NoiseConfigurationResult:
    """A successful noise configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    amplitude_vpp: float
    offset_v: float
    bandwidth_hz: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class NoiseDryRunResult:
    """A hardware-free preview of a noise configuration."""

    model: str
    canonical_model_id: str
    amplitude_vpp: float
    offset_v: float
    bandwidth_hz: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class PrbsConfigurationResult:
    """A successful PRBS configuration."""

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
    channel: int = 1
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class PrbsDryRunResult:
    """A hardware-free preview of a PRBS configuration."""

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
    channel: int = 1
    burst: CountedBurstConfig | None = None


@dataclass(frozen=True)
class OutputResult:
    """A successful explicit output-state change."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    output_state: str
    channel: int = 1


@dataclass(frozen=True)
class SineListSweepConfigurationResult:
    """A successful selected-channel sine frequency List Sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class SineListSweepDryRunResult:
    """A hardware-free preview of a selected-channel sine frequency List Sweep."""

    model: str
    canonical_model_id: str
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class SquareListSweepConfigurationResult:
    """A successful selected-channel square frequency List Sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    duty_cycle_percent: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class SquareListSweepDryRunResult:
    """A hardware-free preview of a selected-channel square frequency List Sweep."""

    model: str
    canonical_model_id: str
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    duty_cycle_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class RampListSweepConfigurationResult:
    """A successful selected-channel ramp frequency List Sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    symmetry_percent: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class RampListSweepDryRunResult:
    """A hardware-free preview of a selected-channel ramp frequency List Sweep."""

    model: str
    canonical_model_id: str
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    symmetry_percent: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class TriangleListSweepConfigurationResult:
    """A successful selected-channel triangle frequency List Sweep configuration."""

    resource: str
    backend: str
    transport: str
    identity: InstrumentIdentity
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class TriangleListSweepDryRunResult:
    """A hardware-free preview of a selected-channel triangle frequency List Sweep."""

    model: str
    canonical_model_id: str
    frequencies_hz: tuple[float, ...]
    dwell_s: float
    amplitude_vpp: float
    offset_v: float
    phase_deg: float
    load: str
    commands: tuple[str, ...]
    executed: bool = False
    output_state: str = "off"
    channel: int = 1


@dataclass(frozen=True)
class BusTriggerResult:
    """A successful one-shot instrument-wide bus trigger."""

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
    """A bounded SYSTem:ERRor? drain of an exactly recognized supported instrument."""

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
    """Create the explicitly selected PyVISA ResourceManager without fallback."""

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
            cleanup_errors.extend(
                _close_visa_resources(
                    session,
                    manager,
                    backend=backend_selection.name,
                    transport=transport,
                    close_manager=False,
                )
            )

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
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
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
                    identity = _resolve_runtime_identity(
                        raw_idn,
                        manager=manager,
                        factory=factory,
                        support_policy_mode=support_policy_mode,
                        expected_model_id=expected_model_id,
                    )
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
        cleanup_errors = _close_visa_resources(
            session,
            manager,
            backend=backend_selection.name,
            transport=transport,
        )

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
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> StatusResult:
    """Read selected-channel status from one policy-admitted instrument."""

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
                    identity = _resolve_runtime_identity(
                        raw_idn,
                        manager=manager,
                        factory=factory,
                        support_policy_mode=support_policy_mode,
                        expected_model_id=expected_model_id,
                    )
                except WavegenError as exc:
                    primary_error = exc.attach_context(
                        backend=backend_selection.name,
                        transport=transport,
                    )
                else:
                    capabilities = _capabilities_for_identity(identity)
                    try:
                        selected_channel = _validate_channel(
                            channel,
                            capabilities,
                            identity.model,
                        )
                    except WavegenError as exc:
                        primary_error = exc.attach_context(
                            backend=backend_selection.name,
                            transport=transport,
                            identity=identity,
                        )
                        selected_channel = None
                    if primary_error is not None:
                        selected_channel = None
                    if selected_channel is None:
                        pass
                    else:
                        channel_queries = tuple(
                            command.replace("1", str(selected_channel), 1)
                            for command in STATUS_COMMON_QUERIES
                        )
                        responses: dict[str, str] = {}
                        current_command = "status"
                        try:
                            for command in channel_queries:
                                current_command = command
                                responses[command] = session.query(command)
                            function_query = channel_queries[1]
                            function = _parse_status_function(
                                responses[function_query]
                            )
                            if function == "DC":
                                function_queries = ()
                            elif function in {"NOIS", "NOISE"}:
                                function_queries = STATUS_NOISE_QUERIES
                            elif function == "PRBS":
                                function_queries = STATUS_PRBS_QUERIES
                            else:
                                function_queries = STATUS_FREQUENCY_AMPLITUDE_QUERIES
                            function_queries = tuple(
                                command.replace("1", str(selected_channel), 1)
                                for command in function_queries
                            )
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
                                frequency_query = f"SOURce{selected_channel}:FREQuency?"
                                unit_query = f"SOURce{selected_channel}:VOLTage:UNIT?"
                                amplitude_query = f"SOURce{selected_channel}:VOLTage?"
                                bandwidth_query = (
                                    f"SOURce{selected_channel}:FUNCtion:NOISe:BANDwidth?"
                                )
                                bit_rate_query = (
                                    f"SOURce{selected_channel}:FUNCtion:PRBS:BRATe?"
                                )
                                offset_query = (
                                    f"SOURce{selected_channel}:VOLTage:OFFSet?"
                                )
                                output_query = f"OUTPut{selected_channel}?"
                                load_query = f"OUTPut{selected_channel}:LOAD?"
                                frequency_hz = (
                                    _parse_status_number(
                                        responses[frequency_query],
                                        "frequency",
                                    )
                                    if frequency_query in responses
                                    else None
                                )
                                amplitude_unit = (
                                    _parse_status_unit(responses[unit_query])
                                    if unit_query in responses
                                    else None
                                )
                                amplitude = (
                                    _parse_status_number(
                                        responses[amplitude_query],
                                        "amplitude",
                                    )
                                    if amplitude_query in responses
                                    else None
                                )
                                bandwidth_hz = (
                                    _parse_status_number(
                                        responses[bandwidth_query],
                                        "noise bandwidth",
                                    )
                                    if bandwidth_query in responses
                                    else None
                                )
                                bit_rate_bps = (
                                    _parse_status_number(
                                        responses[bit_rate_query],
                                        "PRBS bit rate",
                                    )
                                    if bit_rate_query in responses
                                    else None
                                )
                                result = StatusResult(
                                    resource=resource_name,
                                    backend=backend_selection.name,
                                    transport=transport,
                                    identity=identity,
                                    output_state=_parse_status_output(
                                        responses[output_query]
                                    ),
                                    function=function,
                                    frequency_hz=frequency_hz,
                                    amplitude=amplitude,
                                    amplitude_unit=amplitude_unit,
                                    bandwidth_hz=bandwidth_hz,
                                    offset_v=_parse_status_number(
                                        responses[offset_query],
                                        "offset",
                                    ),
                                    load=_parse_status_load(responses[load_query]),
                                    channel=selected_channel,
                                    bit_rate_bps=bit_rate_bps,
                                )
                            except StatusQueryError as exc:
                                primary_error = exc.attach_context(
                                    backend=backend_selection.name,
                                    transport=transport,
                                    identity=identity,
                                )
    finally:
        cleanup_errors = _close_visa_resources(
            session,
            manager,
            backend=backend_selection.name,
            transport=transport,
        )

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
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SineConfigurationResult:
    """Validate and configure a selected-channel sine wave while keeping output off."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
        prepared = _prepare_sine(
            frequency_hz,
            amplitude_vpp,
            offset_v,
            load,
            phase_deg,
            capabilities=capabilities,
        )
        normalized_am, am_commands = _prepare_am(
            "sine",
            am,
            capabilities=capabilities,
        )
        normalized_fm, fm_commands = _prepare_fm(
            "sine",
            prepared[0],
            fm,
            capabilities=capabilities,
        )
        normalized_pm, pm_commands = _prepare_pm(
            "sine",
            prepared[0],
            pm,
            capabilities=capabilities,
        )
        normalized_fsk, fsk_commands = _prepare_fsk(
            "sine",
            fsk,
            capabilities=capabilities,
        )
        normalized_bpsk, bpsk_commands = _prepare_bpsk("sine", bpsk)
        normalized_burst, burst_commands = _prepare_counted_burst(
            "sine",
            prepared[0],
            burst,
            am,
            fm,
            pm,
            fsk,
            bpsk,
            ordinary_phase_deg=prepared[4],
        )
        return (
            *prepared[:-1],
            normalized_am,
            normalized_fm,
            normalized_pm,
            normalized_fsk,
            normalized_bpsk,
            normalized_burst,
        ), (
            *prepared[-1],
            *am_commands,
            *fm_commands,
            *pm_commands,
            *fsk_commands,
            *bpsk_commands,
            *burst_commands,
        )

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    (
        frequency,
        amplitude,
        offset,
        normalized_load,
        phase,
        normalized_am,
        normalized_fm,
        normalized_pm,
        normalized_fsk,
        normalized_bpsk,
        normalized_burst,
    ) = prepared
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
        channel=channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
    )


def dry_run_sine(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
) -> SineDryRunResult:
    """Preview a validated Channel 1 sine configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "sine")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )

    _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
    frequency, amplitude, offset, normalized_load, phase, commands = _prepare_sine(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
        capabilities=capabilities,
    )
    normalized_am, am_commands = _prepare_am(
        "sine",
        am,
        capabilities=capabilities,
    )
    normalized_fm, fm_commands = _prepare_fm(
        "sine",
        frequency,
        fm,
        capabilities=capabilities,
    )
    normalized_pm, pm_commands = _prepare_pm(
        "sine",
        frequency,
        pm,
        capabilities=capabilities,
    )
    normalized_fsk, fsk_commands = _prepare_fsk(
        "sine",
        fsk,
        capabilities=capabilities,
    )
    normalized_bpsk, bpsk_commands = _prepare_bpsk("sine", bpsk)
    normalized_burst, burst_commands = _prepare_counted_burst(
        "sine",
        frequency,
        burst,
        am,
        fm,
        pm,
        fsk,
        bpsk,
        ordinary_phase_deg=phase,
    )
    return SineDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        load=normalized_load,
        phase_deg=phase,
        commands=_channelize_commands(
            (
                *commands,
                *am_commands,
                *fm_commands,
                *pm_commands,
                *fsk_commands,
                *bpsk_commands,
                *burst_commands,
            ),
            selected_channel,
        ),
        channel=selected_channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SineSweepConfigurationResult:
    """Validate and configure a selected-channel sine frequency sweep."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        prepared = _prepare_sine_sweep(
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
            trigger_source,
            trigger_timer_s,
            capabilities=capabilities,
        )
        return prepared[:-1], prepared[-1]

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
    ) = prepared
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
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        channel=channel,
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
    *,
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
) -> SineSweepDryRunResult:
    """Preview a validated selected-channel sine sweep without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "sine sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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
        trigger_source,
        trigger_timer_s,
        capabilities=capabilities,
    )
    return SineSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SquareSweepConfigurationResult:
    """Validate and configure a selected-channel square frequency sweep."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        prepared = _prepare_square_sweep(
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
            trigger_source,
            trigger_timer_s,
            capabilities=capabilities,
        )
        return prepared[:-1], prepared[-1]

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
    ) = prepared
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
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
        channel=channel,
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
    *,
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
) -> SquareSweepDryRunResult:
    """Preview a validated selected-channel square sweep without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "square sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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
        trigger_source,
        trigger_timer_s,
        capabilities=capabilities,
    )
    return SquareSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> RampSweepConfigurationResult:
    """Validate and configure a selected-channel ramp frequency sweep."""

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
        normalized_trigger_source,
        normalized_trigger_timer,
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
        trigger_source,
        trigger_timer_s,
    )
    context = _write_to_supported_instrument(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
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
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        symmetry_percent=symmetry,
        load=normalized_load,
        channel=channel,
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
    *,
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
) -> RampSweepDryRunResult:
    """Preview a validated selected-channel ramp sweep without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "ramp sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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
        trigger_source,
        trigger_timer_s,
    )
    return RampSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        symmetry_percent=symmetry,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> TriangleSweepConfigurationResult:
    """Validate and configure a selected-channel triangle frequency sweep."""

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
        normalized_trigger_source,
        normalized_trigger_timer,
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
        trigger_source,
        trigger_timer_s,
    )
    context = _write_to_supported_instrument(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
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
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        channel=channel,
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
    *,
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    channel: int = 1,
) -> TriangleSweepDryRunResult:
    """Preview a validated selected-channel triangle sweep without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "triangle sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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
        trigger_source,
        trigger_timer_s,
    )
    return TriangleSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        start_frequency_hz=start_frequency,
        stop_frequency_hz=stop_frequency,
        spacing=normalized_spacing,
        sweep_time_s=sweep_time,
        hold_time_s=hold_time,
        return_time_s=return_time,
        trigger_source=normalized_trigger_source,
        trigger_timer_s=normalized_trigger_timer,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
    )


def configure_sine_list_sweep(
    resource: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SineListSweepConfigurationResult:
    """Validate and configure a selected-channel sine frequency List Sweep."""

    frequency_values = _normalize_frequency_list(
        frequencies_hz,
        waveform="Sine List Sweep",
    )

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        prepared = _prepare_sine_list_sweep(
            frequency_values,
            dwell_s,
            amplitude_vpp,
            offset_v,
            load,
            phase_deg,
            capabilities=capabilities,
        )
        return prepared[:-1], prepared[-1]

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    frequencies, dwell, amplitude, offset, normalized_load, phase = prepared
    return SineListSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        channel=channel,
    )


def dry_run_sine_list_sweep(
    model: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
) -> SineListSweepDryRunResult:
    """Preview a validated selected-channel sine frequency List Sweep."""

    model_info, capabilities = _require_hardware_free_model(model, "sine List Sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    frequencies, dwell, amplitude, offset, normalized_load, phase, commands = (
        _prepare_sine_list_sweep(
            frequencies_hz,
            dwell_s,
            amplitude_vpp,
            offset_v,
            load,
            phase_deg,
            capabilities=capabilities,
        )
    )
    return SineListSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
    )


def configure_square_list_sweep(
    resource: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    duty_cycle_percent: object = 50,
    *,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SquareListSweepConfigurationResult:
    """Validate and configure a selected-channel square frequency List Sweep."""

    frequency_values = _normalize_frequency_list(
        frequencies_hz,
        waveform="Square List Sweep",
    )

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        prepared = _prepare_square_list_sweep(
            frequency_values,
            dwell_s,
            amplitude_vpp,
            offset_v,
            duty_cycle_percent,
            load,
            phase_deg,
            capabilities=capabilities,
        )
        return prepared[:-1], prepared[-1]

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    frequencies, dwell, amplitude, offset, duty_cycle, normalized_load, phase = prepared
    return SquareListSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
        channel=channel,
    )


def dry_run_square_list_sweep(
    model: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    duty_cycle_percent: object = 50,
    *,
    channel: int = 1,
) -> SquareListSweepDryRunResult:
    """Preview a validated selected-channel square frequency List Sweep."""

    model_info, capabilities = _require_hardware_free_model(model, "square List Sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    (
        frequencies,
        dwell,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        commands,
    ) = _prepare_square_list_sweep(
        frequencies_hz,
        dwell_s,
        amplitude_vpp,
        offset_v,
        duty_cycle_percent,
        load,
        phase_deg,
        capabilities=capabilities,
    )
    return SquareListSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
    )


def configure_ramp_list_sweep(
    resource: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    symmetry_percent: object = 100,
    *,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> RampListSweepConfigurationResult:
    """Validate and configure a selected-channel ramp frequency List Sweep."""

    frequency_values = _normalize_frequency_list(
        frequencies_hz,
        waveform="Ramp List Sweep",
        maximum_frequency_hz=RAMP_TRIANGLE_MAX_FREQUENCY_HZ,
    )

    def prepare_configuration(
        _capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        prepared = _prepare_ramp_list_sweep(
            frequency_values,
            dwell_s,
            amplitude_vpp,
            offset_v,
            symmetry_percent,
            load,
            phase_deg,
        )
        return prepared[:-1], prepared[-1]

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    frequencies, dwell, amplitude, offset, symmetry, normalized_load, phase = prepared
    return RampListSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        symmetry_percent=symmetry,
        load=normalized_load,
        channel=channel,
    )


def dry_run_ramp_list_sweep(
    model: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    symmetry_percent: object = 100,
    *,
    channel: int = 1,
) -> RampListSweepDryRunResult:
    """Preview a validated selected-channel ramp frequency List Sweep."""

    model_info, capabilities = _require_hardware_free_model(model, "ramp List Sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    (
        frequencies,
        dwell,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        commands,
    ) = _prepare_ramp_list_sweep(
        frequencies_hz,
        dwell_s,
        amplitude_vpp,
        offset_v,
        symmetry_percent,
        load,
        phase_deg,
    )
    return RampListSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        symmetry_percent=symmetry,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
    )


def configure_triangle_list_sweep(
    resource: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    backend: str | None = None,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> TriangleListSweepConfigurationResult:
    """Validate and configure a selected-channel triangle frequency List Sweep."""

    frequency_values = _normalize_frequency_list(
        frequencies_hz,
        waveform="Triangle List Sweep",
        maximum_frequency_hz=RAMP_TRIANGLE_MAX_FREQUENCY_HZ,
    )

    def prepare_configuration(
        _capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        prepared = _prepare_triangle_list_sweep(
            frequency_values,
            dwell_s,
            amplitude_vpp,
            offset_v,
            load,
            phase_deg,
        )
        return prepared[:-1], prepared[-1]

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    frequencies, dwell, amplitude, offset, normalized_load, phase = prepared
    return TriangleListSweepConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        channel=channel,
    )


def dry_run_triangle_list_sweep(
    model: str,
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
) -> TriangleListSweepDryRunResult:
    """Preview a validated selected-channel triangle frequency List Sweep."""

    model_info, capabilities = _require_hardware_free_model(model, "triangle List Sweep")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    frequencies, dwell, amplitude, offset, normalized_load, phase, commands = (
        _prepare_triangle_list_sweep(
            frequencies_hz,
            dwell_s,
            amplitude_vpp,
            offset_v,
            load,
            phase_deg,
        )
    )
    return TriangleListSweepDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequencies_hz=frequencies,
        dwell_s=dwell,
        amplitude_vpp=amplitude,
        offset_v=offset,
        phase_deg=phase,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
    )


def _normalize_frequency_list(
    values: object,
    *,
    waveform: str,
    maximum_frequency_hz: float | None = None,
) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise WaveformParameterError(
            f"{waveform} frequencies must be an iterable of finite numbers."
        )
    normalized = tuple(
        _normalize_finite_number(value, "frequency", waveform=waveform)
        for value in values
    )
    if not FREQUENCY_LIST_MIN_POINTS <= len(normalized) <= FREQUENCY_LIST_MAX_POINTS:
        raise WaveformParameterError(
            f"{waveform} must contain between 1 and 128 frequencies."
        )
    maximum = maximum_frequency_hz
    for frequency in normalized:
        if frequency < FREQUENCY_LIST_MIN_FREQUENCY_HZ or (
            maximum is not None and frequency > maximum
        ):
            maximum_text = (
                f" and {_format_scpi_number(maximum)} Hz"
                if maximum is not None
                else ""
            )
            raise WaveformParameterError(
                f"{waveform} frequencies must be between 0.000001 Hz"
                f"{maximum_text}."
            )
    return normalized


def _normalize_list_dwell(value: object, *, waveform: str) -> float:
    dwell = _normalize_finite_number(value, "dwell", waveform=waveform)
    if not FREQUENCY_LIST_MIN_DWELL_S <= dwell <= FREQUENCY_LIST_MAX_DWELL_S:
        raise WaveformParameterError(
            f"{waveform} dwell must be between 0.000001 s and 1000 s."
        )
    return dwell


def _build_list_sweep_commands(
    base_commands: tuple[str, ...],
    frequencies_hz: tuple[float, ...],
    dwell_s: float,
) -> tuple[str, ...]:
    frequency_list = ",".join(_format_scpi_number(value) for value in frequencies_hz)
    return (
        base_commands[0],
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        *base_commands[1:],
        f"SOURce1:LIST:FREQuency {frequency_list}",
        f"SOURce1:LIST:DWELl {_format_scpi_number(dwell_s)}",
        "TRIGger1:SOURce IMMediate",
        "SOURce1:FREQuency:MODE LIST",
    )


def _prepare_sine_list_sweep(
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object,
    load: object,
    phase_deg: object,
    *,
    capabilities: WavegenCapabilities,
) -> tuple[tuple[float, ...], float, float, float, str, float, tuple[str, ...]]:
    waveform = "Sine List Sweep"
    frequencies = _normalize_frequency_list(
        frequencies_hz,
        waveform=waveform,
        maximum_frequency_hz=capabilities.max_sine_square_pulse_noise_frequency_hz,
    )
    dwell = _normalize_list_dwell(dwell_s, waveform=waveform)
    _, amplitude, offset, normalized_load, phase, base_commands = _prepare_sine(
        frequencies[0],
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
        capabilities=capabilities,
        include_cw_mode=False,
    )
    commands = _build_list_sweep_commands(base_commands, frequencies, dwell)
    return frequencies, dwell, amplitude, offset, normalized_load, phase, commands


def _prepare_square_list_sweep(
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object,
    duty_cycle_percent: object,
    load: object,
    phase_deg: object,
    *,
    capabilities: WavegenCapabilities,
) -> tuple[
    tuple[float, ...],
    float,
    float,
    float,
    float,
    str,
    float,
    tuple[str, ...],
]:
    waveform = "Square List Sweep"
    frequencies = _normalize_frequency_list(
        frequencies_hz,
        waveform=waveform,
        maximum_frequency_hz=capabilities.max_sine_square_pulse_noise_frequency_hz,
    )
    dwell = _normalize_list_dwell(dwell_s, waveform=waveform)
    (
        _,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        base_commands,
    ) = _prepare_square(
        frequencies[0],
        amplitude_vpp,
        offset_v,
        duty_cycle_percent,
        load,
        phase_deg,
        capabilities=capabilities,
        include_cw_mode=False,
        duty_cycle_validation_frequency_hz=max(frequencies),
    )
    commands = _build_list_sweep_commands(base_commands, frequencies, dwell)
    return (
        frequencies,
        dwell,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        commands,
    )


def _prepare_ramp_list_sweep(
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object,
    symmetry_percent: object,
    load: object,
    phase_deg: object,
) -> tuple[
    tuple[float, ...],
    float,
    float,
    float,
    float,
    str,
    float,
    tuple[str, ...],
]:
    waveform = "Ramp List Sweep"
    frequencies = _normalize_frequency_list(
        frequencies_hz,
        waveform=waveform,
        maximum_frequency_hz=RAMP_TRIANGLE_MAX_FREQUENCY_HZ,
    )
    dwell = _normalize_list_dwell(dwell_s, waveform=waveform)
    (
        _,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        base_commands,
    ) = _prepare_ramp(
        frequencies[0],
        amplitude_vpp,
        offset_v,
        symmetry_percent,
        load,
        phase_deg,
        include_cw_mode=False,
    )
    commands = _build_list_sweep_commands(base_commands, frequencies, dwell)
    return (
        frequencies,
        dwell,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        commands,
    )


def _prepare_triangle_list_sweep(
    frequencies_hz: object,
    dwell_s: object,
    amplitude_vpp: object,
    offset_v: object,
    load: object,
    phase_deg: object,
) -> tuple[tuple[float, ...], float, float, float, str, float, tuple[str, ...]]:
    waveform = "Triangle List Sweep"
    frequencies = _normalize_frequency_list(
        frequencies_hz,
        waveform=waveform,
        maximum_frequency_hz=RAMP_TRIANGLE_MAX_FREQUENCY_HZ,
    )
    dwell = _normalize_list_dwell(dwell_s, waveform=waveform)
    _, amplitude, offset, normalized_load, phase, base_commands = _prepare_triangle(
        frequencies[0],
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
        include_cw_mode=False,
    )
    commands = _build_list_sweep_commands(base_commands, frequencies, dwell)
    return frequencies, dwell, amplitude, offset, normalized_load, phase, commands


def _require_capabilities_for_model_id(model_id: str) -> WavegenCapabilities:
    capabilities = capabilities_for_model_id(model_id)
    if capabilities is None:
        raise RuntimeError(f"missing capability profile for model ID {model_id!r}")
    return capabilities


def _preflight_capabilities_for_configuration(
    resource_manager_factory: ResourceManagerFactory | None,
) -> WavegenCapabilities:
    if isinstance(resource_manager_factory, SimulatedResourceManagerFactory):
        return _require_capabilities_for_model_id(resource_manager_factory.model_id)

    maximum_frequency_hz = max(
        _require_capabilities_for_model_id(
            model_id
        ).max_sine_square_pulse_noise_frequency_hz
        for model_id in registered_model_ids()
    )
    return WavegenCapabilities(maximum_frequency_hz)


def _capabilities_for_identity(
    identity: InstrumentIdentity,
) -> WavegenCapabilities:
    if identity.canonical_model_id is None:
        raise RuntimeError("resolved identity is missing its canonical model ID")
    return _require_capabilities_for_model_id(identity.canonical_model_id)


def _require_hardware_free_model(
    model: object,
    waveform: str,
) -> tuple[ModelInfo, WavegenCapabilities]:
    model_info = model_info_for_model_id(model) if isinstance(model, str) else None
    if model_info is None:
        raise UnsupportedInstrumentError(
            f"Unsupported {waveform} dry-run model; "
            "expected an exact registered model ID."
        )
    return model_info, _require_capabilities_for_model_id(model_info.model_id)


def _resolve_runtime_identity(
    raw_idn: str,
    *,
    manager: VisaResourceManager,
    factory: ResourceManagerFactory,
    support_policy_mode: str,
    expected_model_id: str | None,
) -> InstrumentIdentity:
    parsed_identity = parse_idn(raw_idn)
    if not (
        isinstance(factory, SimulatedResourceManagerFactory)
        and isinstance(manager, SimulatedResourceManager)
    ):
        identity = resolve_supported_identity(
            parsed_identity,
            support_policy_mode=support_policy_mode,
        )
    else:
        if manager.state is not factory.state:
            raise RuntimeError("simulator factory and manager state do not match")
        model_info = model_info_for_model_id(factory.model_id)
        if model_info is None or parsed_identity.model != model_info.canonical_model:
            raise UnsupportedInstrumentError(
                "Simulator identity does not match its registered model context.",
                identity=parsed_identity,
            )
        identity = replace(
            parsed_identity,
            model=model_info.canonical_model,
            canonical_model_id=model_info.model_id,
            model_supported=True,
        )

    if (
        expected_model_id is not None
        and identity.canonical_model_id != expected_model_id
    ):
        raise UnsupportedInstrumentError(
            "Detected instrument does not match the expected exact model ID "
            f"{expected_model_id!r}.",
            identity=identity,
        )
    return identity


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
    trigger_source: str,
    trigger_timer_s: float | None,
) -> tuple[str, ...]:
    trigger_commands = _build_trigger_commands(trigger_source, trigger_timer_s)
    return (
        f"SOURce1:FREQuency:STARt {_format_scpi_number(start_frequency)}",
        f"SOURce1:FREQuency:STOP {_format_scpi_number(stop_frequency)}",
        f"SOURce1:SWEep:SPACing {spacing_command}",
        f"SOURce1:SWEep:TIME {_format_scpi_number(sweep_time)}",
        f"SOURce1:SWEep:HTIMe {_format_scpi_number(hold_time)}",
        f"SOURce1:SWEep:RTIMe {_format_scpi_number(return_time)}",
        *trigger_commands,
        "SOURce1:FREQuency:MODE SWEep",
    )


def _normalize_trigger_source(value: object, *, waveform: str) -> str:
    if not isinstance(value, str):
        raise WaveformParameterError(
            f"{waveform} trigger source must be immediate, bus, or timer."
        )
    normalized = value.strip().casefold()
    if normalized not in {"immediate", "bus", "timer"}:
        raise WaveformParameterError(
            f"{waveform} trigger source must be immediate, bus, or timer."
        )
    return normalized


def _prepare_trigger(
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    *,
    waveform: str,
    minimum_timer_s: float | None = None,
) -> tuple[str, float | None]:
    source = _normalize_trigger_source(trigger_source, waveform=waveform)
    if source != "timer":
        if trigger_timer_s is not None:
            raise WaveformParameterError(
                f"{waveform} trigger timer must be omitted for {source} trigger source."
            )
        return source, None
    if trigger_timer_s is None:
        raise WaveformParameterError(
            f"{waveform} trigger timer is required for timer trigger source."
        )
    timer = _normalize_finite_number(
        trigger_timer_s,
        "trigger timer",
        waveform=waveform,
    )
    if not TRIGGER_MIN_TIMER_S <= timer <= TRIGGER_MAX_TIMER_S:
        raise WaveformParameterError(
            f"{waveform} trigger timer must be between 0.000001 s and 8000 s."
        )
    if minimum_timer_s is not None and timer < minimum_timer_s:
        raise WaveformParameterError(
            f"{waveform} trigger timer is too short for the configured operation."
        )
    return source, timer


def _build_trigger_commands(
    trigger_source: str,
    trigger_timer_s: float | None,
) -> tuple[str, ...]:
    source_command = {
        "immediate": "IMMediate",
        "bus": "BUS",
        "timer": "TIMer",
    }[trigger_source]
    if trigger_timer_s is None:
        return (f"TRIGger1:SOURce {source_command}",)
    return (
        f"TRIGger1:TIMer {_format_scpi_number(trigger_timer_s)}",
        f"TRIGger1:SOURce {source_command}",
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    *,
    capabilities: WavegenCapabilities,
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
    str,
    float | None,
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
    maximum_supported_frequency = (
        capabilities.max_sine_square_pulse_noise_frequency_hz
    )
    if not 0.000001 <= start_frequency <= maximum_supported_frequency:
        raise WaveformParameterError(
            "Square sweep start frequency must be between "
            "0.000001 Hz and "
            f"{_format_scpi_number(maximum_supported_frequency)} Hz."
        )
    if not 0.000001 <= stop_frequency <= maximum_supported_frequency:
        raise WaveformParameterError(
            "Square sweep stop frequency must be between "
            "0.000001 Hz and "
            f"{_format_scpi_number(maximum_supported_frequency)} Hz."
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
        capabilities=capabilities,
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
    normalized_trigger_source, normalized_trigger_timer = _prepare_trigger(
        trigger_source,
        trigger_timer_s,
        waveform="Square sweep",
        minimum_timer_s=sweep_time + hold_time + return_time,
    )
    commands = (
        base_commands[0],
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        *base_commands[1:],
        *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
        normalized_trigger_source,
        normalized_trigger_timer,
        ),
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
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
    str,
    float | None,
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
    normalized_trigger_source, normalized_trigger_timer = _prepare_trigger(
        trigger_source,
        trigger_timer_s,
        waveform="Ramp sweep",
        minimum_timer_s=sweep_time + hold_time + return_time,
    )
    commands = (
        base_commands[0],
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        *base_commands[1:],
        *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
        normalized_trigger_source,
        normalized_trigger_timer,
        ),
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
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
    str,
    float | None,
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
    normalized_trigger_source, normalized_trigger_timer = _prepare_trigger(
        trigger_source,
        trigger_timer_s,
        waveform="Triangle sweep",
        minimum_timer_s=sweep_time + hold_time + return_time,
    )
    commands = (
        base_commands[0],
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        *base_commands[1:],
        *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
        normalized_trigger_source,
        normalized_trigger_timer,
        ),
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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
    trigger_source: object = "immediate",
    trigger_timer_s: object = None,
    *,
    capabilities: WavegenCapabilities,
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
    str,
    float | None,
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
        capabilities=capabilities,
        include_cw_mode=False,
    )
    stop_frequency = _normalize_finite_number(
        stop_frequency_hz,
        "stop frequency",
        waveform="Sine sweep",
    )
    maximum_supported_frequency = (
        capabilities.max_sine_square_pulse_noise_frequency_hz
    )
    if not 0.000001 <= stop_frequency <= maximum_supported_frequency:
        raise WaveformParameterError(
            "Sine sweep stop frequency must be between "
            "0.000001 Hz and "
            f"{_format_scpi_number(maximum_supported_frequency)} Hz."
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
    normalized_trigger_source, normalized_trigger_timer = _prepare_trigger(
        trigger_source,
        trigger_timer_s,
        waveform="Sine sweep",
        minimum_timer_s=sweep_time + hold_time + return_time,
    )
    commands = (
        base_commands[0],
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        *base_commands[1:],
        *_build_sweep_tail(
        start_frequency,
        stop_frequency,
        spacing_command,
        sweep_time,
        hold_time,
        return_time,
        normalized_trigger_source,
        normalized_trigger_timer,
        ),
    )
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
        normalized_trigger_source,
        normalized_trigger_timer,
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


def _normalize_internal_sine_modulation_frequency(
    value: object,
    *,
    modulation: str,
    capabilities: WavegenCapabilities,
) -> float:
    frequency = _normalize_finite_number(
        value,
        "modulation frequency",
        waveform=modulation,
    )
    maximum_frequency = capabilities.max_sine_square_pulse_noise_frequency_hz
    if not MODULATION_MIN_FREQUENCY_HZ <= frequency <= maximum_frequency:
        raise WaveformParameterError(
            f"{modulation} modulation frequency must be between 0.000001 Hz and "
            f"{_format_scpi_number(maximum_frequency)} Hz."
        )
    return frequency


def _validate_modulation_exclusive(
    am: AMConfig | None,
    fm: FMConfig | None,
    pm: PMConfig | None,
    fsk: FSKConfig | None,
    bpsk: BPSKConfig | None,
    pwm: PWMConfig | None = None,
) -> None:
    if sum(config is not None for config in (am, fm, pm, fsk, bpsk, pwm)) > 1:
        raise WaveformParameterError(
            "AM, FM, PM, FSK, BPSK, and PWM cannot be configured at the same time."
        )


def _prepare_counted_burst(
    carrier: str,
    carrier_rate_hz: float,
    config: CountedBurstConfig | None,
    *modulations: object,
    ordinary_phase_deg: float | None = None,
) -> tuple[CountedBurstConfig | None, tuple[str, ...]]:
    if config is None:
        return None, ()
    if not isinstance(config, CountedBurstConfig):
        raise WaveformParameterError(
            "Burst configuration must use CountedBurstConfig."
        )
    if any(modulation is not None for modulation in modulations):
        raise WaveformParameterError(
            "Counted Burst cannot be configured with AM, FM, PM, FSK, BPSK, or PWM."
        )
    if ordinary_phase_deg is not None and ordinary_phase_deg != 0.0:
        raise WaveformParameterError(
            "Waveform phase must be 0 degrees when Counted Burst is enabled."
        )
    if isinstance(config.count, bool) or not isinstance(config.count, int):
        raise WaveformParameterError(
            "Burst count must be an integer between 1 and 100000000."
        )
    if not BURST_MIN_COUNT <= config.count <= BURST_MAX_COUNT:
        raise WaveformParameterError(
            "Burst count must be an integer between 1 and 100000000."
        )
    if carrier_rate_hz < BURST_MIN_CARRIER_RATE_HZ:
        raise WaveformParameterError(
            "Burst carrier frequency or PRBS bit rate must be at least 0.002001 Hz."
        )
    if (
        carrier in {"sine", "square"}
        and carrier_rate_hz > BURST_MAX_SINE_SQUARE_FREQUENCY_HZ
    ):
        raise WaveformParameterError(
            "Burst Sine and Square carrier frequency must not exceed 6000000 Hz."
        )
    minimum_period = config.count / carrier_rate_hz + BURST_PERIOD_MARGIN_S
    trigger_source = _normalize_trigger_source(
        config.trigger_source,
        waveform="Burst",
    )
    period: float | None = None
    trigger_timer: float | None = None
    if trigger_source == "immediate":
        if config.period_s is None:
            raise WaveformParameterError(
                "Burst period is required for immediate trigger source."
            )
        period = _normalize_finite_number(
            config.period_s,
            "period",
            waveform="Burst",
        )
        if not BURST_MIN_PERIOD_S <= period <= BURST_MAX_PERIOD_S:
            raise WaveformParameterError(
                "Burst period must be between 0.000001 s and 8000 s."
            )
        if period < minimum_period:
            raise WaveformParameterError(
                "Burst period must be at least count divided by carrier frequency or "
                "PRBS bit rate plus 0.000001 s."
            )
        if config.trigger_timer_s is not None:
            raise WaveformParameterError(
                "Burst trigger timer must be omitted for immediate trigger source."
            )
    elif trigger_source == "bus":
        if config.period_s is not None or config.trigger_timer_s is not None:
            raise WaveformParameterError(
                "Burst period and trigger timer must be omitted for bus trigger source."
            )
    else:
        if config.period_s is not None:
            raise WaveformParameterError(
                "Burst period must be omitted for timer trigger source."
            )
        _, trigger_timer = _prepare_trigger(
            trigger_source,
            config.trigger_timer_s,
            waveform="Burst",
            minimum_timer_s=minimum_period,
        )
    normalized = CountedBurstConfig(
        count=config.count,
        period_s=period,
        trigger_source=trigger_source,
        trigger_timer_s=trigger_timer,
    )
    period_commands = (
        (f"SOURce1:BURSt:INTernal:PERiod {_format_scpi_number(period)}",)
        if period is not None
        else ()
    )
    commands = (
        "SOURce1:BURSt:MODE TRIGgered",
        f"SOURce1:BURSt:NCYCles {config.count}",
        *period_commands,
        *(
            (f"TRIGger1:TIMer {_format_scpi_number(trigger_timer)}",)
            if trigger_timer is not None
            else ()
        ),
        "SOURce1:BURSt:PHASe 0",
        _build_trigger_commands(trigger_source, None)[0],
        "SOURce1:BURSt:STATe ON",
    )
    return normalized, commands


def _prepare_am(
    carrier: str,
    config: AMConfig | None,
    *,
    capabilities: WavegenCapabilities,
) -> tuple[AMConfig | None, tuple[str, ...]]:
    if config is None:
        return None, ()
    if not isinstance(config, AMConfig):
        raise WaveformParameterError("AM configuration must use AMConfig.")
    if carrier not in {"sine", "square", "ramp", "triangle", "pulse"}:
        raise WaveformParameterError(
            f"{carrier.upper()} carrier is not supported for AM configuration."
        )

    frequency = _normalize_internal_sine_modulation_frequency(
        config.modulation_frequency_hz,
        modulation="AM",
        capabilities=capabilities,
    )
    depth = _normalize_finite_number(
        config.depth_percent,
        "depth",
        waveform="AM",
    )
    if not isinstance(config.am_type, str):
        raise WaveformParameterError("AM type must be normal or dssc.")
    am_type = config.am_type.strip().casefold()
    if am_type not in {"normal", "dssc"}:
        raise WaveformParameterError("AM type must be normal or dssc.")

    if not 0 <= depth <= 100:
        raise WaveformParameterError("AM depth must be between 0% and 100%.")

    normalized = AMConfig(
        modulation_frequency_hz=frequency,
        depth_percent=depth,
        am_type=am_type,
    )
    commands = (
        "SOURce1:AM:SOURce INTernal",
        f"SOURce1:AM:DSSC {'ON' if am_type == 'dssc' else 'OFF'}",
        "SOURce1:AM:INTernal:FUNCtion SINusoid",
        "SOURce1:AM:INTernal:FREQuency "
        f"{_format_scpi_number(frequency)}",
        f"SOURce1:AM:DEPTh {_format_scpi_number(depth)}",
        "SOURce1:AM:STATe ON",
    )
    return normalized, commands


def _prepare_fm(
    carrier: str,
    carrier_frequency_hz: object,
    config: FMConfig | None,
    *,
    capabilities: WavegenCapabilities,
) -> tuple[FMConfig | None, tuple[str, ...]]:
    if config is None:
        return None, ()
    if not isinstance(config, FMConfig):
        raise WaveformParameterError("FM configuration must use FMConfig.")
    if carrier not in {"sine", "square", "ramp", "triangle"}:
        raise WaveformParameterError(
            f"{carrier.upper()} carrier is not supported for FM configuration."
        )

    carrier_frequency = _normalize_finite_number(
        carrier_frequency_hz,
        "carrier frequency",
        waveform="FM",
    )
    modulation_frequency = _normalize_internal_sine_modulation_frequency(
        config.modulation_frequency_hz,
        modulation="FM",
        capabilities=capabilities,
    )
    deviation = _normalize_finite_number(
        config.deviation_hz,
        "deviation",
        waveform="FM",
    )
    function_maximum = (
        capabilities.max_sine_square_pulse_noise_frequency_hz
        if carrier in {"sine", "square"}
        else RAMP_TRIANGLE_MAX_FREQUENCY_HZ
    )
    maximum_deviation = min(
        FM_MAX_DEVIATION_HZ,
        carrier_frequency,
        function_maximum + 100_000.0 - carrier_frequency,
    )
    if not MODULATION_MIN_FREQUENCY_HZ <= deviation <= maximum_deviation:
        raise WaveformParameterError(
            "FM deviation must be at least 0.000001 Hz and no more "
            "than the minimum of 15000000 Hz, the carrier frequency, and the "
            "selected function maximum plus 100000 Hz minus the carrier frequency."
        )

    normalized = FMConfig(
        modulation_frequency_hz=modulation_frequency,
        deviation_hz=deviation,
    )
    commands = (
        "SOURce1:FM:SOURce INTernal",
        "SOURce1:FM:INTernal:FUNCtion SINusoid",
        "SOURce1:FM:INTernal:FREQuency "
        f"{_format_scpi_number(modulation_frequency)}",
        f"SOURce1:FM:DEViation {_format_scpi_number(deviation)}",
        "SOURce1:FM:STATe ON",
    )
    return normalized, commands


def _prepare_pm(
    carrier: str,
    carrier_frequency_hz: object,
    config: PMConfig | None,
    *,
    capabilities: WavegenCapabilities,
) -> tuple[PMConfig | None, tuple[str, ...]]:
    if config is None:
        return None, ()
    if not isinstance(config, PMConfig):
        raise WaveformParameterError("PM configuration must use PMConfig.")
    if carrier not in {"sine", "square", "ramp", "triangle"}:
        raise WaveformParameterError(
            f"{carrier.upper()} carrier is not supported for PM configuration."
        )

    carrier_frequency = _normalize_finite_number(
        carrier_frequency_hz,
        "carrier frequency",
        waveform="PM",
    )
    modulation_frequency = _normalize_internal_sine_modulation_frequency(
        config.modulation_frequency_hz,
        modulation="PM",
        capabilities=capabilities,
    )
    deviation = _normalize_finite_number(
        config.deviation_deg,
        "deviation",
        waveform="PM",
    )
    if not 0 <= deviation <= 360:
        raise WaveformParameterError(
            "PM deviation must be between 0 and 360 degrees."
        )
    if carrier_frequency <= 20 * modulation_frequency:
        raise WaveformParameterError(
            "PM carrier frequency must be greater than 20 times the modulation frequency."
        )

    normalized = PMConfig(
        modulation_frequency_hz=modulation_frequency,
        deviation_deg=deviation,
    )
    commands = (
        "SOURce1:PM:SOURce INTernal",
        "SOURce1:PM:INTernal:FUNCtion SINusoid",
        "SOURce1:PM:INTernal:FREQuency "
        f"{_format_scpi_number(modulation_frequency)}",
        f"SOURce1:PM:DEViation {_format_scpi_number(deviation)}",
        "SOURce1:PM:STATe ON",
    )
    return normalized, commands


def _prepare_fsk(
    carrier: str,
    config: FSKConfig | None,
    *,
    capabilities: WavegenCapabilities,
) -> tuple[FSKConfig | None, tuple[str, ...]]:
    if config is None:
        return None, ()
    if not isinstance(config, FSKConfig):
        raise WaveformParameterError("FSK configuration must use FSKConfig.")
    if carrier not in {"sine", "square", "ramp", "triangle"}:
        raise WaveformParameterError(
            f"{carrier.upper()} carrier is not supported for FSK configuration."
        )

    hop_frequency = _normalize_finite_number(
        config.hop_frequency_hz,
        "hop frequency",
        waveform="FSK",
    )
    rate = _normalize_finite_number(config.rate_hz, "rate", waveform="FSK")
    maximum_frequency = (
        capabilities.max_sine_square_pulse_noise_frequency_hz
        if carrier in {"sine", "square"}
        else RAMP_TRIANGLE_MAX_FREQUENCY_HZ
    )
    if not MODULATION_MIN_FREQUENCY_HZ <= hop_frequency <= maximum_frequency:
        raise WaveformParameterError(
            "FSK hop frequency must be between 0.000001 Hz and "
            f"{_format_scpi_number(maximum_frequency)} Hz."
        )
    if not FSK_MIN_RATE_HZ <= rate <= FSK_MAX_RATE_HZ:
        raise WaveformParameterError(
            "FSK rate must be between 0.000125 Hz and 1000000 Hz."
        )

    normalized = FSKConfig(hop_frequency_hz=hop_frequency, rate_hz=rate)
    commands = (
        "SOURce1:FSKey:SOURce INTernal",
        f"SOURce1:FSKey:FREQuency {_format_scpi_number(hop_frequency)}",
        f"SOURce1:FSKey:INTernal:RATE {_format_scpi_number(rate)}",
        "SOURce1:FSKey:STATe ON",
    )
    return normalized, commands


def _prepare_bpsk(
    carrier: str,
    config: BPSKConfig | None,
) -> tuple[BPSKConfig | None, tuple[str, ...]]:
    if config is None:
        return None, ()
    if not isinstance(config, BPSKConfig):
        raise WaveformParameterError("BPSK configuration must use BPSKConfig.")
    if carrier not in {"sine", "square", "ramp", "triangle"}:
        raise WaveformParameterError(
            f"{carrier.upper()} carrier is not supported for BPSK configuration."
        )

    phase_shift = _normalize_finite_number(
        config.phase_shift_deg,
        "phase shift",
        waveform="BPSK",
    )
    rate = _normalize_finite_number(config.rate_hz, "rate", waveform="BPSK")
    if not 0 <= phase_shift <= 360:
        raise WaveformParameterError(
            "BPSK phase shift must be between 0 and 360 degrees."
        )
    if not BPSK_MIN_RATE_HZ <= rate <= BPSK_MAX_RATE_HZ:
        raise WaveformParameterError(
            "BPSK rate must be between 0.001 Hz and 1000000 Hz."
        )

    normalized = BPSKConfig(phase_shift_deg=phase_shift, rate_hz=rate)
    commands = (
        "SOURce1:BPSK:SOURce INTernal",
        f"SOURce1:BPSK:PHASe {_format_scpi_number(phase_shift)}",
        f"SOURce1:BPSK:INTernal:RATE {_format_scpi_number(rate)}",
        "SOURce1:BPSK:STATe ON",
    )
    return normalized, commands


def _prepare_pwm(
    carrier_frequency_hz: float,
    pulse_width_s: float,
    leading_edge_s: float,
    trailing_edge_s: float,
    config: PWMConfig | None,
    *,
    capabilities: WavegenCapabilities,
) -> tuple[PWMConfig | None, tuple[str, ...]]:
    if config is None:
        return None, ()
    if not isinstance(config, PWMConfig):
        raise WaveformParameterError("PWM configuration must use PWMConfig.")

    modulation_frequency = _normalize_internal_sine_modulation_frequency(
        config.modulation_frequency_hz,
        modulation="PWM",
        capabilities=capabilities,
    )
    deviation = _normalize_finite_number(
        config.deviation_s,
        "width deviation",
        waveform="PWM",
    )
    if not 0 <= deviation <= PWM_MAX_DEVIATION_S:
        raise WaveformParameterError(
            "PWM width deviation must be between 0 and 500000 seconds."
        )

    period = 1 / carrier_frequency_hz
    edge_allowance = 0.8 * leading_edge_s + 0.8 * trailing_edge_s
    maximum_deviation = min(
        pulse_width_s - PULSE_MIN_WIDTH_S,
        period - pulse_width_s - PULSE_MIN_WIDTH_S,
        pulse_width_s - edge_allowance,
        period - pulse_width_s - edge_allowance,
    )
    if maximum_deviation <= 0 or deviation >= maximum_deviation:
        raise WaveformParameterError(
            "PWM width deviation must be strictly less than the available "
            "pulse width, period, and edge-time margin."
        )

    normalized = PWMConfig(
        modulation_frequency_hz=modulation_frequency,
        deviation_s=deviation,
    )
    commands = (
        "SOURce1:PWM:SOURce INTernal",
        "SOURce1:PWM:INTernal:FUNCtion SINusoid",
        "SOURce1:PWM:INTernal:FREQuency "
        f"{_format_scpi_number(modulation_frequency)}",
        f"SOURce1:PWM:DEViation {_format_scpi_number(deviation)}",
        "SOURce1:PWM:STATe ON",
    )
    return normalized, commands


def _prepare_sine(
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object,
    load: object,
    phase_deg: object,
    *,
    capabilities: WavegenCapabilities,
    include_cw_mode: bool = True,
) -> tuple[float, float, float, str, float, tuple[str, ...]]:
    frequency = _normalize_finite_number(frequency_hz, "frequency")
    amplitude = _normalize_finite_number(amplitude_vpp, "amplitude")
    offset = _normalize_finite_number(offset_v, "offset")
    normalized_load = _normalize_load(load)
    phase = _normalize_phase_deg(phase_deg, waveform="Sine")

    maximum_frequency = capabilities.max_sine_square_pulse_noise_frequency_hz
    if not 0.000001 <= frequency <= maximum_frequency:
        raise WaveformParameterError(
            "Sine frequency must be between 0.000001 Hz and "
            f"{_format_scpi_number(maximum_frequency)} Hz."
        )

    _validate_vpp_levels(amplitude, offset, normalized_load, "Sine")

    load_command = "50" if normalized_load == "50" else "INF"
    static_recovery_commands = (
        (
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
            "SOURce1:BPSK:STATe OFF",
            "SOURce1:PWM:STATe OFF",
            "SOURce1:BURSt:STATe OFF",
            "SOURce1:FREQuency:MODE CW",
        )
        if include_cw_mode
        else ()
    )
    commands = (
        "OUTPut1 OFF",
        *static_recovery_commands,
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
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> SquareConfigurationResult:
    """Validate and configure a Channel 1 square wave while keeping output off."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
        prepared = _prepare_square(
            frequency_hz,
            amplitude_vpp,
            offset_v,
            duty_cycle_percent,
            load,
            phase_deg,
            capabilities=capabilities,
        )
        normalized_fm, fm_commands = _prepare_fm(
            "square",
            prepared[0],
            fm,
            capabilities=capabilities,
        )
        normalized_fsk, fsk_commands = _prepare_fsk(
            "square",
            fsk,
            capabilities=capabilities,
        )
        normalized_bpsk, bpsk_commands = _prepare_bpsk("square", bpsk)
        if normalized_fm is not None or normalized_fsk is not None:
            duty_cycle_validation_frequency = prepared[0]
            if normalized_fm is not None:
                duty_cycle_validation_frequency = (
                    prepared[0] + normalized_fm.deviation_hz
                )
            if normalized_fsk is not None:
                duty_cycle_validation_frequency = max(
                    prepared[0], normalized_fsk.hop_frequency_hz
                )
            prepared = _prepare_square(
                frequency_hz,
                amplitude_vpp,
                offset_v,
                duty_cycle_percent,
                load,
                phase_deg,
                capabilities=capabilities,
                duty_cycle_validation_frequency_hz=duty_cycle_validation_frequency,
            )
        normalized_am, am_commands = _prepare_am(
            "square",
            am,
            capabilities=capabilities,
        )
        normalized_pm, pm_commands = _prepare_pm(
            "square",
            prepared[0],
            pm,
            capabilities=capabilities,
        )
        normalized_burst, burst_commands = _prepare_counted_burst(
            "square",
            prepared[0],
            burst,
            am,
            fm,
            pm,
            fsk,
            bpsk,
            ordinary_phase_deg=prepared[5],
        )
        return (
            *prepared[:-1],
            normalized_am,
            normalized_fm,
            normalized_pm,
            normalized_fsk,
            normalized_bpsk,
            normalized_burst,
        ), (
            *prepared[-1],
            *am_commands,
            *fm_commands,
            *pm_commands,
            *fsk_commands,
            *bpsk_commands,
            *burst_commands,
        )

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    (
        frequency,
        amplitude,
        offset,
        duty_cycle,
        normalized_load,
        phase,
        normalized_am,
        normalized_fm,
        normalized_pm,
        normalized_fsk,
        normalized_bpsk,
        normalized_burst,
    ) = prepared
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
        channel=channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
    )


def dry_run_square(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    duty_cycle_percent: object = 50,
    load: object = 50,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
) -> SquareDryRunResult:
    """Preview a validated Channel 1 square configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "square")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
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
        capabilities=capabilities,
    )
    normalized_fm, fm_commands = _prepare_fm(
        "square",
        frequency,
        fm,
        capabilities=capabilities,
    )
    normalized_fsk, fsk_commands = _prepare_fsk(
        "square",
        fsk,
        capabilities=capabilities,
    )
    normalized_bpsk, bpsk_commands = _prepare_bpsk("square", bpsk)
    if normalized_fm is not None or normalized_fsk is not None:
        duty_cycle_validation_frequency = frequency
        if normalized_fm is not None:
            duty_cycle_validation_frequency = frequency + normalized_fm.deviation_hz
        if normalized_fsk is not None:
            duty_cycle_validation_frequency = max(
                frequency, normalized_fsk.hop_frequency_hz
            )
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
            capabilities=capabilities,
            duty_cycle_validation_frequency_hz=duty_cycle_validation_frequency,
        )
    normalized_am, am_commands = _prepare_am(
        "square",
        am,
        capabilities=capabilities,
    )
    normalized_pm, pm_commands = _prepare_pm(
        "square",
        frequency,
        pm,
        capabilities=capabilities,
    )
    normalized_burst, burst_commands = _prepare_counted_burst(
        "square",
        frequency,
        burst,
        am,
        fm,
        pm,
        fsk,
        bpsk,
        ordinary_phase_deg=phase,
    )
    return SquareDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        duty_cycle_percent=duty_cycle,
        load=normalized_load,
        phase_deg=phase,
        commands=_channelize_commands(
            (
                *commands,
                *am_commands,
                *fm_commands,
                *pm_commands,
                *fsk_commands,
                *bpsk_commands,
                *burst_commands,
            ),
            selected_channel,
        ),
        channel=selected_channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
    )


def _prepare_square(
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object,
    duty_cycle_percent: object,
    load: object,
    phase_deg: object,
    *,
    capabilities: WavegenCapabilities,
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

    maximum_frequency = capabilities.max_sine_square_pulse_noise_frequency_hz
    if not 0.000001 <= frequency <= maximum_frequency:
        raise WaveformParameterError(
            "Square frequency must be between 0.000001 Hz and "
            f"{_format_scpi_number(maximum_frequency)} Hz."
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
    static_recovery_commands = (
        (
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
            "SOURce1:BPSK:STATe OFF",
            "SOURce1:PWM:STATe OFF",
            "SOURce1:BURSt:STATe OFF",
            "SOURce1:FREQuency:MODE CW",
        )
        if include_cw_mode
        else ()
    )
    commands = (
        "OUTPut1 OFF",
        *static_recovery_commands,
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
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> RampConfigurationResult:
    """Validate and configure a selected-channel ramp wave while keeping output off."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
        prepared = _prepare_ramp(
            frequency_hz,
            amplitude_vpp,
            offset_v,
            symmetry_percent,
            load,
            phase_deg,
        )
        normalized_am, am_commands = _prepare_am(
            "ramp",
            am,
            capabilities=capabilities,
        )
        normalized_fm, fm_commands = _prepare_fm(
            "ramp",
            prepared[0],
            fm,
            capabilities=capabilities,
        )
        normalized_pm, pm_commands = _prepare_pm(
            "ramp",
            prepared[0],
            pm,
            capabilities=capabilities,
        )
        normalized_fsk, fsk_commands = _prepare_fsk(
            "ramp",
            fsk,
            capabilities=capabilities,
        )
        normalized_bpsk, bpsk_commands = _prepare_bpsk("ramp", bpsk)
        normalized_burst, burst_commands = _prepare_counted_burst(
            "ramp",
            prepared[0],
            burst,
            am,
            fm,
            pm,
            fsk,
            bpsk,
            ordinary_phase_deg=prepared[5],
        )
        return (
            *prepared[:-1],
            normalized_am,
            normalized_fm,
            normalized_pm,
            normalized_fsk,
            normalized_bpsk,
            normalized_burst,
        ), (
            *prepared[-1],
            *am_commands,
            *fm_commands,
            *pm_commands,
            *fsk_commands,
            *bpsk_commands,
            *burst_commands,
        )

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    (
        frequency,
        amplitude,
        offset,
        symmetry,
        normalized_load,
        phase,
        normalized_am,
        normalized_fm,
        normalized_pm,
        normalized_fsk,
        normalized_bpsk,
        normalized_burst,
    ) = prepared
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
        channel=channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
    )


def dry_run_ramp(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    symmetry_percent: object = 100,
    load: object = 50,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
) -> RampDryRunResult:
    """Preview a validated Channel 1 ramp configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "ramp")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
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
    normalized_am, am_commands = _prepare_am(
        "ramp",
        am,
        capabilities=capabilities,
    )
    normalized_fm, fm_commands = _prepare_fm(
        "ramp",
        frequency,
        fm,
        capabilities=capabilities,
    )
    normalized_pm, pm_commands = _prepare_pm(
        "ramp",
        frequency,
        pm,
        capabilities=capabilities,
    )
    normalized_fsk, fsk_commands = _prepare_fsk(
        "ramp",
        fsk,
        capabilities=capabilities,
    )
    normalized_bpsk, bpsk_commands = _prepare_bpsk("ramp", bpsk)
    normalized_burst, burst_commands = _prepare_counted_burst(
        "ramp",
        frequency,
        burst,
        am,
        fm,
        pm,
        fsk,
        bpsk,
        ordinary_phase_deg=phase,
    )
    return RampDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        symmetry_percent=symmetry,
        load=normalized_load,
        phase_deg=phase,
        commands=_channelize_commands(
            (
                *commands,
                *am_commands,
                *fm_commands,
                *pm_commands,
                *fsk_commands,
                *bpsk_commands,
                *burst_commands,
            ),
            selected_channel,
        ),
        channel=selected_channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
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
    static_recovery_commands = (
        (
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
            "SOURce1:BPSK:STATe OFF",
            "SOURce1:PWM:STATe OFF",
            "SOURce1:BURSt:STATe OFF",
            "SOURce1:FREQuency:MODE CW",
        )
        if include_cw_mode
        else ()
    )
    commands = (
        "OUTPut1 OFF",
        *static_recovery_commands,
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
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> TriangleConfigurationResult:
    """Validate and configure a selected-channel triangle wave while keeping output off."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
        prepared = _prepare_triangle(
            frequency_hz,
            amplitude_vpp,
            offset_v,
            load,
            phase_deg,
        )
        normalized_am, am_commands = _prepare_am(
            "triangle",
            am,
            capabilities=capabilities,
        )
        normalized_fm, fm_commands = _prepare_fm(
            "triangle",
            prepared[0],
            fm,
            capabilities=capabilities,
        )
        normalized_pm, pm_commands = _prepare_pm(
            "triangle",
            prepared[0],
            pm,
            capabilities=capabilities,
        )
        normalized_fsk, fsk_commands = _prepare_fsk(
            "triangle",
            fsk,
            capabilities=capabilities,
        )
        normalized_bpsk, bpsk_commands = _prepare_bpsk("triangle", bpsk)
        normalized_burst, burst_commands = _prepare_counted_burst(
            "triangle",
            prepared[0],
            burst,
            am,
            fm,
            pm,
            fsk,
            bpsk,
            ordinary_phase_deg=prepared[4],
        )
        return (
            *prepared[:-1],
            normalized_am,
            normalized_fm,
            normalized_pm,
            normalized_fsk,
            normalized_bpsk,
            normalized_burst,
        ), (
            *prepared[-1],
            *am_commands,
            *fm_commands,
            *pm_commands,
            *fsk_commands,
            *bpsk_commands,
            *burst_commands,
        )

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    (
        frequency,
        amplitude,
        offset,
        normalized_load,
        phase,
        normalized_am,
        normalized_fm,
        normalized_pm,
        normalized_fsk,
        normalized_bpsk,
        normalized_burst,
    ) = prepared
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
        channel=channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
    )


def dry_run_triangle(
    model: str,
    frequency_hz: object,
    amplitude_vpp: object,
    offset_v: object = 0,
    load: object = 50,
    phase_deg: object = 0.0,
    *,
    channel: int = 1,
    am: AMConfig | None = None,
    fm: FMConfig | None = None,
    pm: PMConfig | None = None,
    fsk: FSKConfig | None = None,
    bpsk: BPSKConfig | None = None,
    burst: CountedBurstConfig | None = None,
) -> TriangleDryRunResult:
    """Preview a validated Channel 1 triangle configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "triangle")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    _validate_modulation_exclusive(am, fm, pm, fsk, bpsk)
    frequency, amplitude, offset, normalized_load, phase, commands = _prepare_triangle(
        frequency_hz,
        amplitude_vpp,
        offset_v,
        load,
        phase_deg,
    )
    normalized_am, am_commands = _prepare_am(
        "triangle",
        am,
        capabilities=capabilities,
    )
    normalized_fm, fm_commands = _prepare_fm(
        "triangle",
        frequency,
        fm,
        capabilities=capabilities,
    )
    normalized_pm, pm_commands = _prepare_pm(
        "triangle",
        frequency,
        pm,
        capabilities=capabilities,
    )
    normalized_fsk, fsk_commands = _prepare_fsk(
        "triangle",
        fsk,
        capabilities=capabilities,
    )
    normalized_bpsk, bpsk_commands = _prepare_bpsk("triangle", bpsk)
    normalized_burst, burst_commands = _prepare_counted_burst(
        "triangle",
        frequency,
        burst,
        am,
        fm,
        pm,
        fsk,
        bpsk,
        ordinary_phase_deg=phase,
    )
    return TriangleDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        load=normalized_load,
        phase_deg=phase,
        commands=_channelize_commands(
            (
                *commands,
                *am_commands,
                *fm_commands,
                *pm_commands,
                *fsk_commands,
                *bpsk_commands,
                *burst_commands,
            ),
            selected_channel,
        ),
        channel=selected_channel,
        am=normalized_am,
        fm=normalized_fm,
        pm=normalized_pm,
        fsk=normalized_fsk,
        bpsk=normalized_bpsk,
        burst=normalized_burst,
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
    static_recovery_commands = (
        (
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
            "SOURce1:BPSK:STATe OFF",
            "SOURce1:PWM:STATe OFF",
            "SOURce1:BURSt:STATe OFF",
            "SOURce1:FREQuency:MODE CW",
        )
        if include_cw_mode
        else ()
    )
    commands = (
        "OUTPut1 OFF",
        *static_recovery_commands,
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
    channel: int = 1,
    am: AMConfig | None = None,
    pwm: PWMConfig | None = None,
    burst: CountedBurstConfig | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> PulseConfigurationResult:
    """Validate and configure a selected-channel pulse wave while keeping output off."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[object, ...]:
        _validate_modulation_exclusive(am, None, None, None, None, pwm)
        prepared = _prepare_pulse(
            frequency_hz,
            amplitude_vpp,
            pulse_width_s,
            offset_v,
            edge_time_s,
            load,
            phase_deg,
            leading_edge_s,
            trailing_edge_s,
            capabilities=capabilities,
        )
        normalized_am, am_commands = _prepare_am(
            "pulse",
            am,
            capabilities=capabilities,
        )
        normalized_pwm, pwm_commands = _prepare_pwm(
            prepared[0],
            prepared[3],
            prepared[5],
            prepared[6],
            pwm,
            capabilities=capabilities,
        )
        normalized_burst, burst_commands = _prepare_counted_burst(
            "pulse",
            prepared[0],
            burst,
            am,
            pwm,
            ordinary_phase_deg=prepared[8],
        )
        return (
            *prepared,
            normalized_am,
            am_commands,
            normalized_pwm,
            pwm_commands,
            normalized_burst,
            burst_commands,
        )

    prepare_configuration(
        _preflight_capabilities_for_configuration(resource_manager_factory)
    )

    def operate(
        session: VisaSession,
        context: IdentificationResult,
    ) -> tuple[object, ...]:
        capabilities = _capabilities_for_identity(context.identity)
        selected_channel = _validate_channel(
            channel,
            capabilities,
            context.identity.model,
        )
        _check_independent_channel_guard(session, capabilities, context)
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
            normalized_am,
            am_commands,
            normalized_pwm,
            pwm_commands,
            normalized_burst,
            burst_commands,
        ) = prepare_configuration(capabilities)
        commands = _channelize_commands(commands, selected_channel)
        am_commands = _channelize_commands(am_commands, selected_channel)
        pwm_commands = _channelize_commands(pwm_commands, selected_channel)
        burst_commands = _channelize_commands(burst_commands, selected_channel)
        source_prefix = f"SOURce{selected_channel}"
        output_prefix = f"OUTPut{selected_channel}"

        def write_pulse_command(command: str, output_state: str | None) -> None:
            try:
                session.write(command)
            except Exception as exc:
                raise VisaWriteError(
                    "Could not apply the requested instrument control write.",
                    output_state=output_state,
                ) from exc

        write_pulse_command(commands[0], None)
        for command in commands[1:17]:
            write_pulse_command(command, "off")

        if edge_time is not None:
            maximum = _query_pulse_verification(
                session,
                f"{source_prefix}:FUNCtion:PULSe:TRANsition? MAXimum",
                "dynamic BOTH edge maximum",
                _parse_pulse_verification_number,
            )
            _validate_pulse_edge_maximum(
                edge_time,
                maximum,
                "BOTH",
                context,
            )
            remaining_commands = commands[17:]
        else:
            leading_maximum = _query_pulse_verification(
                session,
                f"{source_prefix}:FUNCtion:PULSe:TRANsition:LEADing? MAXimum",
                "dynamic leading edge maximum",
                _parse_pulse_verification_number,
            )
            _validate_pulse_edge_maximum(
                leading_edge,
                leading_maximum,
                "leading",
                context,
            )
            write_pulse_command(commands[17], "off")

            trailing_maximum = _query_pulse_verification(
                session,
                f"{source_prefix}:FUNCtion:PULSe:TRANsition:TRAiling? MAXimum",
                "dynamic trailing edge maximum",
                _parse_pulse_verification_number,
            )
            _validate_pulse_edge_maximum(
                trailing_edge,
                trailing_maximum,
                "trailing",
                context,
            )
            remaining_commands = commands[18:]

        for command in remaining_commands:
            write_pulse_command(command, "off")

        output_state = _query_pulse_verification(
            session,
            f"{output_prefix}?",
            "output state",
            _parse_status_output,
        )
        function = _query_pulse_verification(
            session,
            f"{source_prefix}:FUNCtion?",
            "function",
            _parse_status_function,
        )
        readback_frequency = _query_pulse_verification(
            session,
            f"{source_prefix}:FREQuency?",
            "frequency",
            _parse_pulse_verification_number,
        )
        readback_width = _query_pulse_verification(
            session,
            f"{source_prefix}:FUNCtion:PULSe:WIDTh?",
            "pulse width",
            _parse_pulse_verification_number,
        )
        if edge_time is not None:
            readback_edge = _query_pulse_verification(
                session,
                f"{source_prefix}:FUNCtion:PULSe:TRANsition?",
                "BOTH edge",
                _parse_pulse_verification_number,
            )
            readback_leading = readback_edge
            readback_trailing = readback_edge
        else:
            readback_leading = _query_pulse_verification(
                session,
                f"{source_prefix}:FUNCtion:PULSe:TRANsition:LEADing?",
                "leading edge",
                _parse_pulse_verification_number,
            )
            readback_trailing = _query_pulse_verification(
                session,
                f"{source_prefix}:FUNCtion:PULSe:TRANsition:TRAiling?",
                "trailing edge",
                _parse_pulse_verification_number,
            )
        readback_phase = _query_pulse_verification(
            session,
            f"{source_prefix}:PHASe?",
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
        for command in am_commands:
            write_pulse_command(command, "off")
        for command in pwm_commands:
            write_pulse_command(command, "off")
        for command in burst_commands:
            write_pulse_command(command, "off")
        return (
            amplitude,
            offset,
            edge_time,
            normalized_load,
            readback_frequency,
            readback_width,
            readback_leading,
            readback_trailing,
            readback_phase,
            normalized_am,
            normalized_pwm,
            normalized_burst,
        )

    context, readback = _run_on_supported_instrument(
        resource,
        backend,
        operate,
        output_state_after_operation="off",
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    (
        amplitude,
        offset,
        edge_time,
        normalized_load,
        readback_frequency,
        readback_width,
        readback_leading,
        readback_trailing,
        readback_phase,
        normalized_am,
        normalized_pwm,
        normalized_burst,
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
        channel=channel,
        am=normalized_am,
        pwm=normalized_pwm,
        burst=normalized_burst,
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
    *,
    channel: int = 1,
    am: AMConfig | None = None,
    pwm: PWMConfig | None = None,
    burst: CountedBurstConfig | None = None,
) -> PulseDryRunResult:
    """Preview a validated Channel 1 pulse configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "pulse")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    _validate_modulation_exclusive(am, None, None, None, None, pwm)
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
        capabilities=capabilities,
    )
    normalized_am, am_commands = _prepare_am(
        "pulse",
        am,
        capabilities=capabilities,
    )
    normalized_pwm, pwm_commands = _prepare_pwm(
        frequency,
        pulse_width,
        leading_edge,
        trailing_edge,
        pwm,
        capabilities=capabilities,
    )
    normalized_burst, burst_commands = _prepare_counted_burst(
        "pulse",
        frequency,
        burst,
        am,
        pwm,
        ordinary_phase_deg=phase,
    )
    return PulseDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        frequency_hz=frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        pulse_width_s=pulse_width,
        edge_time_s=edge_time,
        load=normalized_load,
        phase_deg=phase,
        leading_edge_s=leading_edge,
        trailing_edge_s=trailing_edge,
        commands=_channelize_commands(
            (*commands, *am_commands, *pwm_commands, *burst_commands), selected_channel
        ),
        channel=selected_channel,
        am=normalized_am,
        pwm=normalized_pwm,
        burst=normalized_burst,
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
    *,
    capabilities: WavegenCapabilities,
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

    maximum_frequency = capabilities.max_sine_square_pulse_noise_frequency_hz
    if not 0.000001 <= frequency <= maximum_frequency:
        raise WaveformParameterError(
            "Pulse frequency must be between 0.000001 Hz and "
            f"{_format_scpi_number(maximum_frequency)} Hz."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Pulse")

    period = 1 / frequency
    edge_margin = 0.625 * (leading_edge + trailing_edge)
    minimum_width = max(PULSE_MIN_WIDTH_S, edge_margin)
    maximum_width = period - max(PULSE_MIN_WIDTH_S, edge_margin)
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
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> DcConfigurationResult:
    """Validate and configure a selected-channel DC voltage while keeping output off."""

    voltage, normalized_load, commands = _prepare_dc(voltage_v, load)
    context = _write_to_supported_instrument(
        resource,
        backend,
        commands,
        output_state_after_writes="off",
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    return DcConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        voltage_v=voltage,
        load=normalized_load,
        channel=channel,
    )


def dry_run_dc(
    model: str,
    voltage_v: object,
    load: object = 50,
    *,
    channel: int = 1,
) -> DcDryRunResult:
    """Preview a validated Channel 1 DC configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "DC")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
    voltage, normalized_load, commands = _prepare_dc(voltage_v, load)
    return DcDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        voltage_v=voltage,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
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
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> NoiseConfigurationResult:
    """Validate and configure a Channel 1 noise wave while keeping output off."""

    def prepare_configuration(
        capabilities: WavegenCapabilities,
    ) -> tuple[tuple[object, ...], tuple[str, ...]]:
        prepared = _prepare_noise(
            amplitude_vpp,
            bandwidth_hz,
            offset_v,
            load,
            capabilities=capabilities,
        )
        return prepared[:-1], prepared[-1]

    context, prepared = _prepare_and_write_to_supported_instrument(
        resource,
        backend,
        prepare_configuration,
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    amplitude, offset, bandwidth, normalized_load = prepared
    return NoiseConfigurationResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        amplitude_vpp=amplitude,
        offset_v=offset,
        bandwidth_hz=bandwidth,
        load=normalized_load,
        channel=channel,
    )


def dry_run_noise(
    model: str,
    amplitude_vpp: object,
    bandwidth_hz: object,
    offset_v: object = 0,
    load: object = 50,
    *,
    channel: int = 1,
) -> NoiseDryRunResult:
    """Preview a validated Channel 1 noise configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "noise")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
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
        capabilities=capabilities,
    )
    return NoiseDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        amplitude_vpp=amplitude,
        offset_v=offset,
        bandwidth_hz=bandwidth,
        load=normalized_load,
        commands=_channelize_commands(commands, selected_channel),
        channel=selected_channel,
    )


def _prepare_noise(
    amplitude_vpp: object,
    bandwidth_hz: object,
    offset_v: object,
    load: object,
    *,
    capabilities: WavegenCapabilities,
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

    maximum_bandwidth = capabilities.max_sine_square_pulse_noise_frequency_hz
    if not 0.001 <= bandwidth <= maximum_bandwidth:
        raise WaveformParameterError(
            "Noise bandwidth must be between 0.001 Hz and "
            f"{_format_scpi_number(maximum_bandwidth)} Hz."
        )
    _validate_vpp_levels(amplitude, offset, normalized_load, "Noise")

    load_command = "50" if normalized_load == "50" else "INF"
    commands = (
        "OUTPut1 OFF",
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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
    channel: int = 1,
    burst: CountedBurstConfig | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> PrbsConfigurationResult:
    """Validate and configure selected-channel PRBS while keeping output off."""

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
    normalized_burst, burst_commands = _prepare_counted_burst(
        "prbs", bit_rate, burst
    )
    context = _write_to_supported_instrument(
        resource,
        backend,
        (*commands, *burst_commands),
        output_state_after_writes="off",
        channel=channel,
        independent_channel_guard=True,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
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
        channel=channel,
        burst=normalized_burst,
    )


def dry_run_prbs(
    model: str,
    bit_rate_bps: object,
    amplitude_vpp: object,
    pattern: object = "PN7",
    offset_v: object = 0,
    edge_time_s: object = 8.4e-9,
    load: object = 50,
    *,
    channel: int = 1,
    burst: CountedBurstConfig | None = None,
) -> PrbsDryRunResult:
    """Preview a validated Channel 1 PRBS configuration without VISA I/O."""

    model_info, capabilities = _require_hardware_free_model(model, "PRBS")
    selected_channel = _validate_channel(
        channel, capabilities, model_info.canonical_model
    )
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
    normalized_burst, burst_commands = _prepare_counted_burst(
        "prbs", bit_rate, burst
    )
    return PrbsDryRunResult(
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        bit_rate_bps=bit_rate,
        amplitude_vpp=amplitude,
        pattern=normalized_pattern,
        offset_v=offset,
        edge_time_s=edge_time,
        load=normalized_load,
        commands=_channelize_commands((*commands, *burst_commands), selected_channel),
        channel=selected_channel,
        burst=normalized_burst,
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
        "SOURce1:AM:STATe OFF",
        "SOURce1:FM:STATe OFF",
        "SOURce1:PM:STATe OFF",
        "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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
    channel: int = 1,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> OutputResult:
    """Explicitly set a policy-admitted instrument's selected-channel output state."""

    if not isinstance(state, str) or state.strip().casefold() not in {"on", "off"}:
        raise WaveformParameterError("Output state must be on or off.")
    normalized_state = state.strip().casefold()
    context = _write_to_supported_instrument(
        resource,
        backend,
        (f"OUTPut1 {normalized_state.upper()}",),
        channel=channel,
        independent_channel_guard=True,
        output_state_after_writes=normalized_state,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    return OutputResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
        output_state=normalized_state,
        channel=channel,
    )


def send_bus_trigger(
    resource: str,
    backend: str | None = None,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> BusTriggerResult:
    """Send one instrument-wide IEEE-488.2 bus trigger without waiting."""

    def write_trigger(
        session: VisaSession,
        _context: IdentificationResult,
    ) -> None:
        session.write("*TRG")

    context, _ = _run_on_supported_instrument(
        resource,
        backend,
        write_trigger,
        resource_manager_factory=resource_manager_factory,
    )
    return BusTriggerResult(
        resource=context.resource,
        backend=context.backend,
        transport=context.transport,
        identity=context.identity,
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


def _run_on_supported_instrument(
    resource: str,
    backend: str | None,
    operation: Callable[[VisaSession, IdentificationResult], object],
    *,
    output_state_after_operation: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
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
                    identity = _resolve_runtime_identity(
                        raw_idn,
                        manager=manager,
                        factory=factory,
                        support_policy_mode=support_policy_mode,
                        expected_model_id=expected_model_id,
                    )
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
        cleanup_errors = _close_visa_resources(
            session,
            manager,
            backend=backend_selection.name,
            transport=transport,
        )

    if primary_error is not None:
        primary_error.attach_cleanup_errors(cleanup_errors)
        if primary_cause is not None:
            raise primary_error from primary_cause
        raise primary_error
    if cleanup_errors:
        if output_state_after_operation == "on":
            message = (
                "The selected output ON command was sent, but VISA cleanup failed; "
                "selected output may remain on: "
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


def _write_to_supported_instrument(
    resource: str,
    backend: str | None,
    commands: tuple[str, ...],
    *,
    output_state_after_writes: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    channel: int = 1,
    independent_channel_guard: bool = False,
) -> IdentificationResult:
    def write_commands(
        session: VisaSession,
        context: IdentificationResult,
    ) -> None:
        capabilities = _capabilities_for_identity(context.identity)
        selected_channel = _validate_channel(
            channel,
            capabilities,
            context.identity.model,
        )
        if independent_channel_guard:
            _check_independent_channel_guard(session, capabilities, context)
        for command in _channelize_commands(commands, selected_channel):
            session.write(command)

    context, _ = _run_on_supported_instrument(
        resource,
        backend,
        write_commands,
        output_state_after_operation=output_state_after_writes,
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    return context


def _prepare_and_write_to_supported_instrument(
    resource: str,
    backend: str | None,
    prepare_configuration: Callable[
        [WavegenCapabilities],
        tuple[tuple[object, ...], tuple[str, ...]],
    ],
    *,
    resource_manager_factory: ResourceManagerFactory | None,
    support_policy_mode: str,
    expected_model_id: str | None,
    channel: int = 1,
    independent_channel_guard: bool = False,
) -> tuple[IdentificationResult, tuple[object, ...]]:
    prepare_configuration(
        _preflight_capabilities_for_configuration(resource_manager_factory)
    )

    def prepare_and_write(
        session: VisaSession,
        context: IdentificationResult,
    ) -> tuple[object, ...]:
        capabilities = _capabilities_for_identity(context.identity)
        selected_channel = _validate_channel(
            channel,
            capabilities,
            context.identity.model,
        )
        if independent_channel_guard:
            _check_independent_channel_guard(session, capabilities, context)
        values, commands = prepare_configuration(capabilities)
        for command in _channelize_commands(commands, selected_channel):
            session.write(command)
        return values

    context, values = _run_on_supported_instrument(
        resource,
        backend,
        prepare_and_write,
        output_state_after_operation="off",
        resource_manager_factory=resource_manager_factory,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
    )
    if not isinstance(values, tuple):  # pragma: no cover - defensive invariant
        raise RuntimeError("configuration completed without prepared values")
    return context, values


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
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> ErrorQueueResult:
    """Drain the SYSTem:ERRor? queue of one policy-admitted instrument."""

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
                    identity = _resolve_runtime_identity(
                        raw_idn,
                        manager=manager,
                        factory=factory,
                        support_policy_mode=support_policy_mode,
                        expected_model_id=expected_model_id,
                    )
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
        cleanup_errors = _close_visa_resources(
            session,
            manager,
            backend=backend_selection.name,
            transport=transport,
        )

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
    *,
    backend: str,
    transport: str,
    close_manager: bool = True,
) -> tuple[str, ...]:
    errors: list[str] = []
    if session is not None:
        if (
            backend == SYSTEM_BACKEND
            and transport == USB_TRANSPORT
            and not isinstance(manager, SimulatedResourceManager)
        ):
            try:
                session.control_ren(RENLineOperation.address_gtl)
            except Exception:
                errors.append("return to local failed")
        try:
            session.close()
        except Exception:
            errors.append("session close failed")
    if close_manager:
        errors.extend(_close_resource_manager(manager))
    return tuple(errors)


def _close_resource_manager(manager: VisaResourceManager) -> tuple[str, ...]:
    try:
        manager.close()
    except Exception:
        return ("ResourceManager close failed",)
    return ()
