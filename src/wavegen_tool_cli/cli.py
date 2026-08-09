"""Console entry point for explicit VISA identification and control."""

from __future__ import annotations

import argparse
import json
import sys
from enum import IntEnum
from typing import Any, Sequence

from wavegen_tool_core import (
    ErrorQueueQueryError,
    IdnQueryError,
    MalformedIdnError,
    ResourceDiscoveryError,
    ResourceManagerError,
    ResourceOpenError,
    SERIAL_TERMINATIONS,
    StatusQueryError,
    UnsupportedBackendError,
    UnsupportedConnectionScopeError,
    UnsupportedInstrumentError,
    UnsupportedTransportError,
    VisaCleanupError,
    VisaWriteError,
    WaveformParameterError,
    WaveformVerificationError,
    WavegenError,
    configure_dc,
    configure_noise,
    configure_prbs,
    configure_pulse,
    configure_ramp,
    configure_ramp_sweep,
    configure_sine,
    configure_sine_sweep,
    configure_square,
    configure_square_sweep,
    configure_triangle,
    configure_triangle_sweep,
    dry_run_dc,
    dry_run_noise,
    dry_run_prbs,
    dry_run_pulse,
    dry_run_ramp,
    dry_run_ramp_sweep,
    dry_run_sine,
    dry_run_sine_sweep,
    dry_run_square,
    dry_run_square_sweep,
    dry_run_triangle,
    dry_run_triangle_sweep,
    identify_instrument,
    list_resources,
    normalize_serial_baud_rate,
    query_status,
    read_error_queue,
    resolve_voltage_inputs,
    set_output,
)
from wavegen_tool_core.identity import (
    CANONICAL_MODEL_ID,
    SUPPORT_POLICY_MODE_VALIDATION,
    registered_model_ids,
)
from wavegen_tool_core.simulator import (
    Simulated33521BState,
    SimulatedResourceManagerFactory,
)
from wavegen_tool_cli.worker import run_worker, validate_worker_startup
from wavegen_tool_cli.lifecycle_client import (
    run_send_command,
    run_wait_ready,
    run_worker_status,
    run_worker_stop,
)


class ExitCode(IntEnum):
    """Stable process exit codes for the initial CLI contract."""

    SUCCESS = 0
    CLI_USAGE = 2
    UNSUPPORTED_TRANSPORT = 10
    UNSUPPORTED_CONNECTION_SCOPE = 11
    RESOURCE_MANAGER_ERROR = 20
    RESOURCE_OPEN_ERROR = 21
    IDN_QUERY_ERROR = 22
    MALFORMED_IDN = 23
    UNSUPPORTED_INSTRUMENT = 24
    VISA_CLEANUP_ERROR = 25
    RESOURCE_DISCOVERY_ERROR = 26
    VISA_WRITE_ERROR = 27
    STATUS_QUERY_ERROR = 28
    WAVEFORM_VERIFICATION_ERROR = 29
    ERROR_QUEUE_QUERY_ERROR = 30
    INTERNAL_ERROR = 70


_ERROR_EXIT_CODES: tuple[tuple[type[WavegenError], ExitCode], ...] = (
    (UnsupportedBackendError, ExitCode.CLI_USAGE),
    (UnsupportedTransportError, ExitCode.UNSUPPORTED_TRANSPORT),
    (UnsupportedConnectionScopeError, ExitCode.UNSUPPORTED_CONNECTION_SCOPE),
    (ResourceManagerError, ExitCode.RESOURCE_MANAGER_ERROR),
    (ResourceDiscoveryError, ExitCode.RESOURCE_DISCOVERY_ERROR),
    (ResourceOpenError, ExitCode.RESOURCE_OPEN_ERROR),
    (WaveformParameterError, ExitCode.CLI_USAGE),
    (IdnQueryError, ExitCode.IDN_QUERY_ERROR),
    (MalformedIdnError, ExitCode.MALFORMED_IDN),
    (UnsupportedInstrumentError, ExitCode.UNSUPPORTED_INSTRUMENT),
    (VisaCleanupError, ExitCode.VISA_CLEANUP_ERROR),
    (VisaWriteError, ExitCode.VISA_WRITE_ERROR),
    (StatusQueryError, ExitCode.STATUS_QUERY_ERROR),
    (WaveformVerificationError, ExitCode.WAVEFORM_VERIFICATION_ERROR),
    (ErrorQueueQueryError, ExitCode.ERROR_QUEUE_QUERY_ERROR),
)
_REGISTERED_MODEL_IDS = registered_model_ids()


def _add_simulate_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use the in-memory simulator without hardware VISA I/O.",
    )


def _add_validation_support_policy_argument(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--validation-allow-pending-live-support",
        action="store_true",
        help=argparse.SUPPRESS,
    )


def _add_voltage_input_arguments(
    parser: argparse.ArgumentParser,
    *,
    amplitude_help: str,
) -> None:
    parser.add_argument(
        "--amplitude-vpp",
        default=None,
        help=amplitude_help,
    )
    parser.add_argument(
        "--offset-v",
        default=None,
        help="DC offset in volts (default: 0 in amplitude mode).",
    )
    parser.add_argument(
        "--high-level-v",
        default=None,
        help="High voltage level in volts; use with --low-level-v.",
    )
    parser.add_argument(
        "--low-level-v",
        default=None,
        help="Low voltage level in volts; use with --high-level-v.",
    )


def _normalize_max_reads_argument(value: str) -> int:
    try:
        max_reads = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "max_reads must be an integer between 1 and 100."
        ) from exc
    if not 1 <= max_reads <= 100:
        raise argparse.ArgumentTypeError(
            "max_reads must be an integer between 1 and 100."
        )
    return max_reads


def _normalize_control_port(value: str) -> int:
    try:
        control_port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "control port must be an integer between 0 and 65535."
        ) from exc
    if not 0 <= control_port <= 65535:
        raise argparse.ArgumentTypeError(
            "control port must be an integer between 0 and 65535."
        )
    return control_port


def _normalize_lifecycle_port(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "port must be an integer between 1 and 65535."
        ) from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be an integer between 1 and 65535.")
    return port


def _normalize_positive_milliseconds(value: str) -> int:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer.") from exc
    if milliseconds <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer.")
    return milliseconds


def _add_lifecycle_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--port",
        type=_normalize_lifecycle_port,
        required=True,
        help="Loopback Worker control port (1-65535).",
    )
    parser.add_argument(
        "--timeout-ms",
        type=_normalize_positive_milliseconds,
        default=1000,
        help="Single HTTP request timeout in milliseconds (default: 1000).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser without touching VISA."""

    parser = argparse.ArgumentParser(
        prog="wavegen-tool",
        description="Safely identify and control an explicitly selected waveform generator.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    identify_parser = subparsers.add_parser(
        "identify",
        help="Query and identify one explicit VISA resource.",
    )
    _add_simulate_argument(identify_parser)
    identify_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    identify_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    identify_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Read Channel 1 status without changing the instrument.",
    )
    _add_simulate_argument(status_parser)
    status_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    status_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    read_errors_parser = subparsers.add_parser(
        "read-errors",
        help="Read and drain the instrument system error queue.",
    )
    _add_simulate_argument(read_errors_parser)
    read_errors_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    read_errors_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    read_errors_parser.add_argument(
        "--max-reads",
        default=20,
        type=_normalize_max_reads_argument,
        help="Maximum error-queue queries (default: 20; range: 1-100).",
    )
    read_errors_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    sine_parser = subparsers.add_parser(
        "configure-sine",
        help="Configure a validated Channel 1 sine waveform with output off.",
    )
    _add_simulate_argument(sine_parser)
    _add_validation_support_policy_argument(sine_parser)
    sine_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    sine_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    sine_parser.add_argument("--frequency-hz", required=True, help="Sine frequency in Hz.")
    _add_voltage_input_arguments(
        sine_parser,
        amplitude_help="Sine amplitude in Vpp.",
    )
    sine_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    sine_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    sine_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    sine_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    sine_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    sine_sweep_parser = subparsers.add_parser(
        "configure-sine-sweep",
        help="Configure a validated Channel 1 sine frequency sweep with output off.",
    )
    _add_simulate_argument(sine_sweep_parser)
    _add_validation_support_policy_argument(sine_sweep_parser)
    sine_sweep_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    sine_sweep_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    sine_sweep_parser.add_argument(
        "--start-frequency-hz",
        required=True,
        help="Sine sweep start frequency in Hz.",
    )
    sine_sweep_parser.add_argument(
        "--stop-frequency-hz",
        required=True,
        help="Sine sweep stop frequency in Hz.",
    )
    sine_sweep_parser.add_argument(
        "--spacing",
        required=True,
        help="Sine sweep spacing: linear or logarithmic.",
    )
    sine_sweep_parser.add_argument(
        "--sweep-time-s",
        required=True,
        help="Time to sweep from start to stop in seconds.",
    )
    sine_sweep_parser.add_argument(
        "--hold-time-s",
        default=0,
        help="Time to hold at the stop frequency in seconds (default: 0).",
    )
    sine_sweep_parser.add_argument(
        "--return-time-s",
        default=0,
        help="Time to return to the start frequency in seconds (default: 0).",
    )
    _add_voltage_input_arguments(
        sine_sweep_parser,
        amplitude_help="Sine sweep amplitude in Vpp.",
    )
    sine_sweep_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    sine_sweep_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    sine_sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    sine_sweep_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    sine_sweep_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    square_sweep_parser = subparsers.add_parser(
        "configure-square-sweep",
        help="Configure a validated Channel 1 square frequency sweep with output off.",
    )
    _add_simulate_argument(square_sweep_parser)
    _add_validation_support_policy_argument(square_sweep_parser)
    square_sweep_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    square_sweep_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    square_sweep_parser.add_argument(
        "--start-frequency-hz",
        required=True,
        help="Square sweep start frequency in Hz.",
    )
    square_sweep_parser.add_argument(
        "--stop-frequency-hz",
        required=True,
        help="Square sweep stop frequency in Hz.",
    )
    square_sweep_parser.add_argument(
        "--spacing",
        required=True,
        help="Square sweep spacing: linear or logarithmic.",
    )
    square_sweep_parser.add_argument(
        "--sweep-time-s",
        required=True,
        help="Time to sweep from start to stop in seconds.",
    )
    square_sweep_parser.add_argument(
        "--hold-time-s",
        default=0,
        help="Time to hold at the stop frequency in seconds (default: 0).",
    )
    square_sweep_parser.add_argument(
        "--return-time-s",
        default=0,
        help="Time to return to the start frequency in seconds (default: 0).",
    )
    _add_voltage_input_arguments(
        square_sweep_parser,
        amplitude_help="Square sweep amplitude in Vpp.",
    )
    square_sweep_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    square_sweep_parser.add_argument(
        "--duty-cycle-percent",
        default="50",
        help="Square duty cycle percentage (default: 50).",
    )
    square_sweep_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    square_sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    square_sweep_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    square_sweep_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    ramp_sweep_parser = subparsers.add_parser(
        "configure-ramp-sweep",
        help="Configure a validated Channel 1 ramp frequency sweep with output off.",
    )
    _add_simulate_argument(ramp_sweep_parser)
    _add_validation_support_policy_argument(ramp_sweep_parser)
    ramp_sweep_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    ramp_sweep_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    ramp_sweep_parser.add_argument(
        "--start-frequency-hz",
        required=True,
        help="Ramp sweep start frequency in Hz.",
    )
    ramp_sweep_parser.add_argument(
        "--stop-frequency-hz",
        required=True,
        help="Ramp sweep stop frequency in Hz.",
    )
    ramp_sweep_parser.add_argument(
        "--spacing",
        required=True,
        help="Ramp sweep spacing: linear or logarithmic.",
    )
    ramp_sweep_parser.add_argument(
        "--sweep-time-s",
        required=True,
        help="Time to sweep from start to stop in seconds.",
    )
    ramp_sweep_parser.add_argument(
        "--hold-time-s",
        default=0,
        help="Time to hold at the stop frequency in seconds (default: 0).",
    )
    ramp_sweep_parser.add_argument(
        "--return-time-s",
        default=0,
        help="Time to return to the start frequency in seconds (default: 0).",
    )
    _add_voltage_input_arguments(
        ramp_sweep_parser,
        amplitude_help="Ramp sweep amplitude in Vpp.",
    )
    ramp_sweep_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    ramp_sweep_parser.add_argument(
        "--symmetry-percent",
        default="100",
        help="Ramp symmetry percentage (default: 100).",
    )
    ramp_sweep_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    ramp_sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    ramp_sweep_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    ramp_sweep_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    triangle_sweep_parser = subparsers.add_parser(
        "configure-triangle-sweep",
        help="Configure a validated Channel 1 triangle frequency sweep with output off.",
    )
    _add_simulate_argument(triangle_sweep_parser)
    _add_validation_support_policy_argument(triangle_sweep_parser)
    triangle_sweep_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    triangle_sweep_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    triangle_sweep_parser.add_argument(
        "--start-frequency-hz",
        required=True,
        help="Triangle sweep start frequency in Hz.",
    )
    triangle_sweep_parser.add_argument(
        "--stop-frequency-hz",
        required=True,
        help="Triangle sweep stop frequency in Hz.",
    )
    triangle_sweep_parser.add_argument(
        "--spacing",
        required=True,
        help="Triangle sweep spacing: linear or logarithmic.",
    )
    triangle_sweep_parser.add_argument(
        "--sweep-time-s",
        required=True,
        help="Time to sweep from start to stop in seconds.",
    )
    triangle_sweep_parser.add_argument(
        "--hold-time-s",
        default=0,
        help="Time to hold at the stop frequency in seconds (default: 0).",
    )
    triangle_sweep_parser.add_argument(
        "--return-time-s",
        default=0,
        help="Time to return to the start frequency in seconds (default: 0).",
    )
    _add_voltage_input_arguments(
        triangle_sweep_parser,
        amplitude_help="Triangle sweep amplitude in Vpp.",
    )
    triangle_sweep_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    triangle_sweep_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    triangle_sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    triangle_sweep_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    triangle_sweep_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    square_parser = subparsers.add_parser(
        "configure-square",
        help="Configure a validated Channel 1 square waveform with output off.",
    )
    _add_simulate_argument(square_parser)
    _add_validation_support_policy_argument(square_parser)
    square_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    square_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    square_parser.add_argument(
        "--frequency-hz",
        required=True,
        help="Square frequency in Hz.",
    )
    _add_voltage_input_arguments(
        square_parser,
        amplitude_help="Square amplitude in Vpp.",
    )
    square_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    square_parser.add_argument(
        "--duty-cycle-percent",
        default="50",
        help="Square duty cycle percentage (default: 50).",
    )
    square_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    square_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    square_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    square_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    ramp_parser = subparsers.add_parser(
        "configure-ramp",
        help="Configure a validated Channel 1 ramp waveform with output off.",
    )
    _add_simulate_argument(ramp_parser)
    _add_validation_support_policy_argument(ramp_parser)
    ramp_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    ramp_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    ramp_parser.add_argument(
        "--frequency-hz",
        required=True,
        help="Ramp frequency in Hz.",
    )
    _add_voltage_input_arguments(
        ramp_parser,
        amplitude_help="Ramp amplitude in Vpp.",
    )
    ramp_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    ramp_parser.add_argument(
        "--symmetry-percent",
        default="100",
        help="Ramp symmetry percentage (default: 100).",
    )
    ramp_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    ramp_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    ramp_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    ramp_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    triangle_parser = subparsers.add_parser(
        "configure-triangle",
        help="Configure a validated Channel 1 triangle waveform with output off.",
    )
    _add_simulate_argument(triangle_parser)
    _add_validation_support_policy_argument(triangle_parser)
    triangle_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    triangle_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    triangle_parser.add_argument(
        "--frequency-hz",
        required=True,
        help="Triangle frequency in Hz.",
    )
    _add_voltage_input_arguments(
        triangle_parser,
        amplitude_help="Triangle amplitude in Vpp.",
    )
    triangle_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    triangle_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    triangle_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    triangle_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    triangle_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    pulse_parser = subparsers.add_parser(
        "configure-pulse",
        help="Configure a validated Channel 1 pulse waveform with output off.",
    )
    _add_simulate_argument(pulse_parser)
    _add_validation_support_policy_argument(pulse_parser)
    pulse_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    pulse_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    pulse_parser.add_argument(
        "--frequency-hz",
        required=True,
        help="Pulse frequency in Hz.",
    )
    pulse_parser.add_argument(
        "--pulse-width-s",
        required=True,
        help="Pulse width in seconds.",
    )
    _add_voltage_input_arguments(
        pulse_parser,
        amplitude_help="Pulse amplitude in Vpp.",
    )
    pulse_parser.add_argument(
        "--phase-deg",
        default=0.0,
        type=float,
        help="Phase offset in degrees (default: 0; range: -360 to 360).",
    )
    pulse_parser.add_argument(
        "--edge-time-s",
        default=None,
        help="Shared leading and trailing edge time in seconds.",
    )
    pulse_parser.add_argument(
        "--leading-edge-s",
        default=None,
        help="Independent leading edge time in seconds.",
    )
    pulse_parser.add_argument(
        "--trailing-edge-s",
        default=None,
        help="Independent trailing edge time in seconds.",
    )
    pulse_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    pulse_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    pulse_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    pulse_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    dc_parser = subparsers.add_parser(
        "configure-dc",
        help="Configure a validated Channel 1 DC voltage with output off.",
    )
    _add_simulate_argument(dc_parser)
    _add_validation_support_policy_argument(dc_parser)
    dc_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    dc_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    dc_parser.add_argument(
        "--voltage-v",
        required=True,
        help="DC output voltage in volts.",
    )
    dc_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    dc_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    dc_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    dc_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    noise_parser = subparsers.add_parser(
        "configure-noise",
        help="Configure a validated Channel 1 noise waveform with output off.",
    )
    _add_simulate_argument(noise_parser)
    _add_validation_support_policy_argument(noise_parser)
    noise_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    noise_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    _add_voltage_input_arguments(
        noise_parser,
        amplitude_help="Noise amplitude in Vpp.",
    )
    noise_parser.add_argument(
        "--bandwidth-hz",
        required=True,
        help="Noise bandwidth in Hz.",
    )
    noise_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    noise_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    noise_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    noise_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    prbs_parser = subparsers.add_parser(
        "configure-prbs",
        help="Configure a validated Channel 1 PRBS waveform with output off.",
    )
    _add_simulate_argument(prbs_parser)
    _add_validation_support_policy_argument(prbs_parser)
    prbs_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    prbs_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    prbs_parser.add_argument(
        "--bit-rate-bps",
        required=True,
        help="PRBS bit rate in bits per second.",
    )
    _add_voltage_input_arguments(
        prbs_parser,
        amplitude_help="PRBS amplitude in Vpp.",
    )
    prbs_parser.add_argument(
        "--pattern",
        type=str.upper,
        choices=("PN7", "PN9", "PN11", "PN15", "PN20", "PN23"),
        default="PN7",
        help="PRBS pattern (default: PN7).",
    )
    prbs_parser.add_argument(
        "--edge-time-s",
        default="0.0000000084",
        help="Common rising and falling edge time in seconds (default: 8.4e-9).",
    )
    prbs_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    prbs_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated SCPI without VISA I/O.",
    )
    prbs_parser.add_argument(
        "--model",
        choices=_REGISTERED_MODEL_IDS,
        default=CANONICAL_MODEL_ID,
        help="Target model for dry-run or simulation (default: keysight-33521b).",
    )
    prbs_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    output_parser = subparsers.add_parser(
        "output",
        help="Explicitly set Channel 1 output on or off.",
    )
    _add_simulate_argument(output_parser)
    output_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    output_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    output_parser.add_argument(
        "--state",
        choices=("on", "off"),
        required=True,
        help="Explicit output state.",
    )
    output_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    list_parser = subparsers.add_parser(
        "list-resources",
        help="List resources from one selected VISA backend.",
        allow_abbrev=False,
    )
    _add_simulate_argument(list_parser)
    list_parser.add_argument(
        "--live-only",
        action="store_true",
        help="Keep only eligible resources that answer one bounded liveness query.",
    )
    list_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    list_parser.add_argument(
        "--serial-baud-rate",
        type=normalize_serial_baud_rate,
        help="Positive baud rate applied only to system ASRL live verification.",
    )
    list_parser.add_argument(
        "--serial-read-termination",
        choices=SERIAL_TERMINATIONS,
        help="ASRL read termination: CR, LF, CRLF, or NONE.",
    )
    list_parser.add_argument(
        "--serial-write-termination",
        choices=SERIAL_TERMINATIONS,
        help="ASRL write termination: CR, LF, CRLF, or NONE.",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    send_parser = subparsers.add_parser(
        "send-command",
        help="Submit one command to a local Wavegen Worker.",
        allow_abbrev=False,
    )
    _add_lifecycle_options(send_parser)
    send_parser.add_argument(
        "--command",
        dest="worker_command",
        required=True,
        help="Worker command name.",
    )
    send_parser.add_argument(
        "--arguments-json",
        default="{}",
        help="Command arguments as a JSON object (default: {}).",
    )
    send_parser.add_argument(
        "--context-json",
        required=True,
        help="Worker request context as a JSON object.",
    )
    send_parser.add_argument("--job-id", help="Optional client job identifier.")

    worker_status_parser = subparsers.add_parser(
        "worker-status",
        help="Read local Wavegen Worker lifecycle status.",
        allow_abbrev=False,
    )
    _add_lifecycle_options(worker_status_parser)

    wait_ready_parser = subparsers.add_parser(
        "wait-ready",
        help="Wait for a local Wavegen Worker to become ready.",
        allow_abbrev=False,
    )
    _add_lifecycle_options(wait_ready_parser)
    wait_ready_parser.add_argument(
        "--wait-timeout-ms",
        type=_normalize_positive_milliseconds,
        default=30000,
        help="Overall readiness wait timeout in milliseconds (default: 30000).",
    )
    wait_ready_parser.add_argument(
        "--poll-ms",
        type=_normalize_positive_milliseconds,
        default=200,
        help="Delay between readiness requests in milliseconds (default: 200).",
    )

    worker_stop_parser = subparsers.add_parser(
        "worker-stop",
        help="Request cooperative stop of a local Wavegen Worker.",
        allow_abbrev=False,
    )
    _add_lifecycle_options(worker_stop_parser)

    worker_parser = subparsers.add_parser(
        "worker",
        help="Run the local Wavegen Worker control plane.",
        allow_abbrev=False,
    )
    worker_parser.add_argument(
        "--mode",
        choices=("live", "simulate"),
        required=True,
        help="Worker execution mode.",
    )
    worker_parser.add_argument(
        "--resource",
        help="Explicit USB or TCPIP/LAN VISA resource required for live use.",
    )
    worker_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend validated by Core (default: system).",
    )
    worker_parser.add_argument(
        "--control-port",
        type=_normalize_control_port,
        default=0,
        help="Loopback control port; 0 selects an available port.",
    )
    worker_parser.add_argument(
        "--allow-output-writes",
        action="store_true",
        help="Allow live waveform configuration and output-on requests.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a stable process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "worker":
        return _run_worker(args, parser)
    if args.command == "send-command":
        return run_send_command(args)
    if args.command == "worker-status":
        return run_worker_status(args)
    if args.command == "wait-ready":
        return run_wait_ready(args)
    if args.command == "worker-stop":
        return run_worker_stop(args)
    waveform_commands = {
        "configure-sine",
        "configure-sine-sweep",
        "configure-square-sweep",
        "configure-ramp-sweep",
        "configure-triangle-sweep",
        "configure-square",
        "configure-ramp",
        "configure-triangle",
        "configure-pulse",
        "configure-dc",
        "configure-noise",
        "configure-prbs",
    }
    if (
        args.command in waveform_commands
        and args.dry_run
        and args.simulate
    ):
        parser.error("--dry-run and --simulate cannot be used together")
    if args.simulate and getattr(args, "resource", None) is not None:
        parser.error("--resource cannot be used with --simulate")
    if args.simulate and args.backend.strip().casefold() != "system":
        parser.error("--simulate requires the system backend")
    if (
        args.command in waveform_commands
        and not args.dry_run
        and not args.simulate
        and args.model != CANONICAL_MODEL_ID
        and not args.validation_allow_pending_live_support
    ):
        parser.error(
            "non-default --model selection requires --dry-run or --simulate"
        )
    if (
        args.command in waveform_commands
        and not args.dry_run
        and not args.simulate
        and args.resource is None
    ):
        parser.error("the following arguments are required: --resource")
    if (
        args.command in {"identify", "status", "output", "read-errors"}
        and not args.simulate
        and args.resource is None
    ):
        parser.error("the following arguments are required: --resource")
    if args.command == "list-resources":
        return _run_list_resources(args)
    if args.command == "configure-sine":
        return _run_configure_sine(args)
    if args.command == "configure-sine-sweep":
        return _run_configure_sine_sweep(args)
    if args.command == "configure-square-sweep":
        return _run_configure_square_sweep(args)
    if args.command == "configure-ramp-sweep":
        return _run_configure_ramp_sweep(args)
    if args.command == "configure-triangle-sweep":
        return _run_configure_triangle_sweep(args)
    if args.command == "configure-square":
        return _run_configure_square(args)
    if args.command == "configure-ramp":
        return _run_configure_ramp(args)
    if args.command == "configure-triangle":
        return _run_configure_triangle(args)
    if args.command == "configure-pulse":
        return _run_configure_pulse(args)
    if args.command == "configure-dc":
        return _run_configure_dc(args)
    if args.command == "configure-noise":
        return _run_configure_noise(args)
    if args.command == "configure-prbs":
        return _run_configure_prbs(args)
    if args.command == "output":
        return _run_output(args)
    if args.command == "status":
        return _run_status(args)
    if args.command == "read-errors":
        return _run_read_errors(args)
    return _run_identify(args)


def _run_worker(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    try:
        config = validate_worker_startup(
            mode=args.mode,
            resource=args.resource,
            backend=args.backend,
            control_port=args.control_port,
            allow_output_writes=args.allow_output_writes,
        )
    except (ValueError, WavegenError) as exc:
        parser.error(str(exc))
    return run_worker(config)


def _simulated_target(
    model_id: str = CANONICAL_MODEL_ID,
) -> tuple[str, SimulatedResourceManagerFactory]:
    factory = SimulatedResourceManagerFactory(
        Simulated33521BState(model_id=model_id)
    )
    return factory.resource_name, factory


def _factory_injection(simulated: bool, factory: Any) -> dict[str, Any]:
    if simulated:
        return {"resource_manager_factory": factory}
    return {}


def _validation_live_injection(args: argparse.Namespace) -> dict[str, Any]:
    if (
        args.dry_run
        or args.simulate
        or not args.validation_allow_pending_live_support
    ):
        return {}
    return {
        "support_policy_mode": SUPPORT_POLICY_MODE_VALIDATION,
        "expected_model_id": args.model,
    }


def _resolve_cli_voltage_inputs(
    args: argparse.Namespace,
    waveform: str,
) -> tuple[float, float]:
    return resolve_voltage_inputs(
        args.amplitude_vpp,
        args.offset_v,
        args.high_level_v,
        args.low_level_v,
        args.load,
        waveform,
    )


def _run_identify(args: argparse.Namespace) -> int:
    resource, factory = (
        _simulated_target() if args.simulate else (args.resource, None)
    )
    try:
        result = identify_instrument(
            resource,
            args.backend,
            **_factory_injection(args.simulate, factory),
        )
    except WavegenError as exc:
        if args.json_output:
            payload = _error_payload(exc)
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            print(
                _with_simulation_notice(_human_error(exc), args.simulate),
                file=sys.stderr,
            )
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            payload = _internal_error_payload()
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            message = "Error [internal_error]: unexpected internal failure."
            print(
                _with_simulation_notice(message, args.simulate),
                file=sys.stderr,
            )
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        payload = _success_payload(result)
        print(
            json.dumps(
                _with_simulation_fields(payload, args.simulate),
                separators=(",", ":"),
            )
        )
    else:
        print(_with_simulation_notice(_human_success(result), args.simulate))
    return int(ExitCode.SUCCESS)


def _run_list_resources(args: argparse.Namespace) -> int:
    factory = _simulated_target()[1] if args.simulate else None
    try:
        result = list_resources(
            args.backend,
            live_only=args.live_only,
            serial_baud_rate=args.serial_baud_rate,
            serial_read_termination=args.serial_read_termination,
            serial_write_termination=args.serial_write_termination,
            **_factory_injection(args.simulate, factory),
        )
    except WavegenError as exc:
        if args.json_output:
            payload = _resource_list_error_payload(exc)
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            print(
                _with_simulation_notice(_human_error(exc), args.simulate),
                file=sys.stderr,
            )
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            payload = _resource_list_internal_error_payload()
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            message = "Error [internal_error]: unexpected internal failure."
            print(
                _with_simulation_notice(message, args.simulate),
                file=sys.stderr,
            )
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        payload = _resource_list_success_payload(result)
        print(
            json.dumps(
                _with_simulation_fields(payload, args.simulate),
                separators=(",", ":"),
            )
        )
    else:
        output = _human_resource_list_success(result, live_only=args.live_only)
        print(_with_simulation_notice(output, args.simulate))
    return int(ExitCode.SUCCESS)


def _run_configure_sine(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_sine_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Sine",
        lambda amplitude, offset: configure_sine(
            resource,
            args.frequency_hz,
            amplitude,
            offset,
            args.load,
            args.backend,
            args.phase_deg,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_sine_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Sine")
        result = dry_run_sine(
            args.model,
            args.frequency_hz,
            amplitude,
            offset,
            args.load,
            args.phase_deg,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _sine_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _sine_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _sine_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_sine_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_sine_sweep(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_sine_sweep_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Sine sweep",
        lambda amplitude, offset: configure_sine_sweep(
            resource,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.backend,
            args.phase_deg,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_sine_sweep_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Sine")
        result = dry_run_sine_sweep(
            args.model,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.phase_deg,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _sine_sweep_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _sine_sweep_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _sine_sweep_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_sine_sweep_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_square_sweep(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_square_sweep_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Square",
        lambda amplitude, offset: configure_square_sweep(
            resource,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.backend,
            args.phase_deg,
            duty_cycle_percent=args.duty_cycle_percent,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_square_sweep_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Square")
        result = dry_run_square_sweep(
            args.model,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.phase_deg,
            duty_cycle_percent=args.duty_cycle_percent,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _square_sweep_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _square_sweep_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _square_sweep_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_square_sweep_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_ramp_sweep(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_ramp_sweep_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Ramp",
        lambda amplitude, offset: configure_ramp_sweep(
            resource,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.backend,
            args.phase_deg,
            symmetry_percent=args.symmetry_percent,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_ramp_sweep_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Ramp")
        result = dry_run_ramp_sweep(
            args.model,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.phase_deg,
            symmetry_percent=args.symmetry_percent,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _ramp_sweep_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _ramp_sweep_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _ramp_sweep_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_ramp_sweep_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_triangle_sweep(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_triangle_sweep_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Triangle",
        lambda amplitude, offset: configure_triangle_sweep(
            resource,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.backend,
            args.phase_deg,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_triangle_sweep_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Triangle")
        result = dry_run_triangle_sweep(
            args.model,
            args.start_frequency_hz,
            args.stop_frequency_hz,
            args.spacing,
            args.sweep_time_s,
            amplitude,
            offset,
            args.hold_time_s,
            args.return_time_s,
            args.load,
            args.phase_deg,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _triangle_sweep_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _triangle_sweep_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _triangle_sweep_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_triangle_sweep_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_square(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_square_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Square",
        lambda amplitude, offset: configure_square(
            resource,
            args.frequency_hz,
            amplitude,
            offset,
            args.duty_cycle_percent,
            args.load,
            args.backend,
            args.phase_deg,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_square_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Square")
        result = dry_run_square(
            args.model,
            args.frequency_hz,
            amplitude,
            offset,
            args.duty_cycle_percent,
            args.load,
            args.phase_deg,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _square_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _square_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _square_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_square_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_ramp(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_ramp_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Ramp",
        lambda amplitude, offset: configure_ramp(
            resource,
            args.frequency_hz,
            amplitude,
            offset,
            args.symmetry_percent,
            args.load,
            args.backend,
            args.phase_deg,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_ramp_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Ramp")
        result = dry_run_ramp(
            args.model,
            args.frequency_hz,
            amplitude,
            offset,
            args.symmetry_percent,
            args.load,
            args.phase_deg,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _ramp_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _ramp_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _ramp_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_ramp_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_triangle(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_triangle_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Triangle",
        lambda amplitude, offset: configure_triangle(
            resource,
            args.frequency_hz,
            amplitude,
            offset,
            args.load,
            args.backend,
            args.phase_deg,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_triangle_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Triangle")
        result = dry_run_triangle(
            args.model,
            args.frequency_hz,
            amplitude,
            offset,
            args.load,
            args.phase_deg,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _triangle_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _triangle_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _triangle_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_triangle_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_pulse(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_pulse_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Pulse",
        lambda amplitude, offset: configure_pulse(
            resource,
            args.frequency_hz,
            amplitude,
            args.pulse_width_s,
            offset,
            args.edge_time_s,
            args.load,
            args.backend,
            args.phase_deg,
            args.leading_edge_s,
            args.trailing_edge_s,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_pulse_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Pulse")
        result = dry_run_pulse(
            args.model,
            args.frequency_hz,
            amplitude,
            args.pulse_width_s,
            offset,
            args.edge_time_s,
            args.load,
            args.phase_deg,
            args.leading_edge_s,
            args.trailing_edge_s,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _pulse_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _pulse_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _pulse_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_pulse_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_dc(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_dc_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control(
        args,
        lambda: configure_dc(
            resource,
            args.voltage_v,
            args.load,
            args.backend,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_dc_dry_run(args: argparse.Namespace) -> int:
    try:
        result = dry_run_dc(args.model, args.voltage_v, args.load)
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _dc_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _dc_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _dc_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_dc_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_noise(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_noise_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "Noise",
        lambda amplitude, offset: configure_noise(
            resource,
            amplitude,
            args.bandwidth_hz,
            offset,
            args.load,
            args.backend,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_noise_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "Noise")
        result = dry_run_noise(
            args.model,
            amplitude,
            args.bandwidth_hz,
            offset,
            args.load,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _noise_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _noise_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _noise_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_noise_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_configure_prbs(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_prbs_dry_run(args)
    resource, factory = (
        _simulated_target(args.model) if args.simulate else (args.resource, None)
    )
    return _run_control_with_voltage(
        args,
        "PRBS",
        lambda amplitude, offset: configure_prbs(
            resource,
            args.bit_rate_bps,
            amplitude,
            args.pattern,
            offset,
            args.edge_time_s,
            args.load,
            args.backend,
            **_validation_live_injection(args),
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_prbs_dry_run(args: argparse.Namespace) -> int:
    try:
        amplitude, offset = _resolve_cli_voltage_inputs(args, "PRBS")
        result = dry_run_prbs(
            args.model,
            args.bit_rate_bps,
            amplitude,
            args.pattern,
            offset,
            args.edge_time_s,
            args.load,
        )
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _prbs_dry_run_error_payload(exc),
                    separators=(",", ":"),
                )
            )
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(
                json.dumps(
                    _prbs_dry_run_internal_error_payload(),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _prbs_dry_run_success_payload(result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_prbs_dry_run_success(result))
    return int(ExitCode.SUCCESS)


def _run_output(args: argparse.Namespace) -> int:
    resource, factory = (
        _simulated_target() if args.simulate else (args.resource, None)
    )
    return _run_control(
        args,
        lambda: set_output(
            resource,
            args.state,
            args.backend,
            **_factory_injection(args.simulate, factory),
        ),
    )


def _run_status(args: argparse.Namespace) -> int:
    resource, factory = (
        _simulated_target() if args.simulate else (args.resource, None)
    )
    try:
        result = query_status(
            resource,
            args.backend,
            **_factory_injection(args.simulate, factory),
        )
    except WavegenError as exc:
        if args.json_output:
            payload = _status_error_payload(exc)
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            print(
                _with_simulation_notice(_human_error(exc), args.simulate),
                file=sys.stderr,
            )
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            payload = _status_internal_error_payload()
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            message = "Error [internal_error]: unexpected internal failure."
            print(
                _with_simulation_notice(message, args.simulate),
                file=sys.stderr,
            )
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        payload = _status_success_payload(result)
        print(
            json.dumps(
                _with_simulation_fields(payload, args.simulate),
                separators=(",", ":"),
            )
        )
    else:
        print(
            _with_simulation_notice(
                _human_status_success(result),
                args.simulate,
            )
        )
    return int(ExitCode.SUCCESS)


def _run_read_errors(args: argparse.Namespace) -> int:
    resource, factory = (
        _simulated_target() if args.simulate else (args.resource, None)
    )
    try:
        result = read_error_queue(
            resource,
            args.backend,
            max_reads=args.max_reads,
            **_factory_injection(args.simulate, factory),
        )
    except WavegenError as exc:
        if args.json_output:
            payload = _error_queue_error_payload(exc, args.max_reads)
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            print(
                _with_simulation_notice(_human_error(exc), args.simulate),
                file=sys.stderr,
            )
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            payload = _error_queue_internal_error_payload(args.max_reads)
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            message = "Error [internal_error]: unexpected internal failure."
            print(
                _with_simulation_notice(message, args.simulate),
                file=sys.stderr,
            )
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        payload = _error_queue_success_payload(result)
        print(
            json.dumps(
                _with_simulation_fields(payload, args.simulate),
                separators=(",", ":"),
            )
        )
    else:
        print(
            _with_simulation_notice(
                _human_error_queue_success(result),
                args.simulate,
            )
        )
    return int(ExitCode.SUCCESS)


def _run_control(args: argparse.Namespace, operation: Any) -> int:
    try:
        result = operation()
    except WavegenError as exc:
        if args.json_output:
            payload = _control_error_payload(args.command, exc)
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            print(
                _with_simulation_notice(_human_error(exc), args.simulate),
                file=sys.stderr,
            )
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            payload = _control_internal_error_payload(args.command)
            print(
                json.dumps(
                    _with_simulation_fields(payload, args.simulate),
                    separators=(",", ":"),
                )
            )
        else:
            message = "Error [internal_error]: unexpected internal failure."
            print(
                _with_simulation_notice(message, args.simulate),
                file=sys.stderr,
            )
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        payload = _control_success_payload(args.command, result)
        print(
            json.dumps(
                _with_simulation_fields(payload, args.simulate),
                separators=(",", ":"),
            )
        )
    else:
        print(
            _with_simulation_notice(
                _human_control_success(args.command, result),
                args.simulate,
            )
        )
    return int(ExitCode.SUCCESS)


def _run_control_with_voltage(
    args: argparse.Namespace,
    waveform: str,
    operation: Any,
) -> int:
    return _run_control(
        args,
        lambda: operation(*_resolve_cli_voltage_inputs(args, waveform)),
    )


def _with_simulation_fields(
    payload: dict[str, object],
    simulated: bool,
) -> dict[str, object]:
    if simulated:
        payload.update(mode="simulate", simulated=True)
    return payload


def _with_simulation_notice(output: str, simulated: bool) -> str:
    if not simulated:
        return output
    return "\n".join(
        (
            "Mode: simulate",
            "No hardware VISA I/O was performed; result is from the in-memory simulator.",
            output,
        )
    )


def _success_payload(result: Any) -> dict[str, object]:
    identity = result.identity
    return {
        "success": True,
        "backend": result.backend,
        "transport": result.transport,
        "manufacturer": identity.manufacturer,
        "model": identity.model,
        "serial": identity.serial,
        "firmware": identity.firmware,
        "canonical_model_id": identity.canonical_model_id,
        "model_supported": identity.model_supported,
        "error": None,
    }


def _error_payload(error: WavegenError) -> dict[str, object]:
    identity = error.identity
    return {
        "success": False,
        "backend": error.backend,
        "transport": error.transport,
        "manufacturer": getattr(identity, "manufacturer", None),
        "model": getattr(identity, "model", None),
        "serial": None,
        "firmware": None,
        "canonical_model_id": getattr(identity, "canonical_model_id", None),
        "model_supported": False,
        "error": _error_text(error),
    }


def _internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "backend": None,
        "transport": None,
        "manufacturer": None,
        "model": None,
        "serial": None,
        "firmware": None,
        "canonical_model_id": None,
        "model_supported": False,
        "error": "internal_error: unexpected internal failure",
    }


def _resource_list_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "backend": result.backend,
        "resources": [
            {
                "resource": entry.resource,
                "manufacturer": entry.manufacturer,
                "model": entry.model,
            }
            for entry in result.resources
        ],
        "error": None,
    }


def _resource_list_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "backend": error.backend,
        "resources": [],
        "error": _error_text(error),
    }


def _resource_list_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "backend": None,
        "resources": [],
        "error": "internal_error: unexpected internal failure",
    }


def _control_success_payload(action: str, result: Any) -> dict[str, object]:
    payload = {
        "success": True,
        "action": action,
        "backend": result.backend,
        "transport": result.transport,
        "manufacturer": result.identity.manufacturer,
        "model": result.identity.model,
        "output_state": result.output_state,
        "error": None,
    }
    if action in {
        "configure-sine-sweep",
        "configure-square-sweep",
        "configure-ramp-sweep",
        "configure-triangle-sweep",
    }:
        payload.update(
            start_frequency_hz=result.start_frequency_hz,
            stop_frequency_hz=result.stop_frequency_hz,
            spacing=result.spacing,
            sweep_time_s=result.sweep_time_s,
            hold_time_s=result.hold_time_s,
            return_time_s=result.return_time_s,
            trigger_source=result.trigger_source,
            amplitude_vpp=result.amplitude_vpp,
            offset_v=result.offset_v,
            phase_deg=result.phase_deg,
            load=result.load,
        )
    if action == "configure-square-sweep":
        payload["duty_cycle_percent"] = result.duty_cycle_percent
    if action == "configure-ramp-sweep":
        payload["symmetry_percent"] = result.symmetry_percent
    if action in {
        "configure-sine",
        "configure-square",
        "configure-ramp",
        "configure-triangle",
        "configure-pulse",
    }:
        payload.update(
            frequency_hz=result.frequency_hz,
            amplitude_vpp=result.amplitude_vpp,
            offset_v=result.offset_v,
            phase_deg=result.phase_deg,
            load=result.load,
        )
    if action == "configure-square":
        payload["duty_cycle_percent"] = result.duty_cycle_percent
    if action == "configure-ramp":
        payload["symmetry_percent"] = result.symmetry_percent
    if action == "configure-pulse":
        payload.update(
            pulse_width_s=result.pulse_width_s,
            edge_time_s=result.edge_time_s,
            leading_edge_s=result.leading_edge_s,
            trailing_edge_s=result.trailing_edge_s,
        )
    if action == "configure-dc":
        payload.update(
            voltage_v=result.voltage_v,
            load=result.load,
        )
    if action == "configure-noise":
        payload.update(
            amplitude_vpp=result.amplitude_vpp,
            offset_v=result.offset_v,
            bandwidth_hz=result.bandwidth_hz,
            load=result.load,
        )
    if action == "configure-prbs":
        payload.update(
            bit_rate_bps=result.bit_rate_bps,
            amplitude_vpp=result.amplitude_vpp,
            pattern=result.pattern,
            offset_v=result.offset_v,
            edge_time_s=result.edge_time_s,
            load=result.load,
        )
    return payload


def _sine_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-sine",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "frequency_hz": result.frequency_hz,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _sine_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-sine",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _sine_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-sine",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _sine_sweep_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-sine-sweep",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "start_frequency_hz": result.start_frequency_hz,
        "stop_frequency_hz": result.stop_frequency_hz,
        "spacing": result.spacing,
        "sweep_time_s": result.sweep_time_s,
        "hold_time_s": result.hold_time_s,
        "return_time_s": result.return_time_s,
        "trigger_source": result.trigger_source,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _sine_sweep_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-sine-sweep",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _sine_sweep_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-sine-sweep",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _square_sweep_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-square-sweep",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "start_frequency_hz": result.start_frequency_hz,
        "stop_frequency_hz": result.stop_frequency_hz,
        "spacing": result.spacing,
        "sweep_time_s": result.sweep_time_s,
        "hold_time_s": result.hold_time_s,
        "return_time_s": result.return_time_s,
        "trigger_source": result.trigger_source,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "duty_cycle_percent": result.duty_cycle_percent,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _square_sweep_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-square-sweep",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _square_sweep_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-square-sweep",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _ramp_sweep_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-ramp-sweep",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "start_frequency_hz": result.start_frequency_hz,
        "stop_frequency_hz": result.stop_frequency_hz,
        "spacing": result.spacing,
        "sweep_time_s": result.sweep_time_s,
        "hold_time_s": result.hold_time_s,
        "return_time_s": result.return_time_s,
        "trigger_source": result.trigger_source,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "symmetry_percent": result.symmetry_percent,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _ramp_sweep_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-ramp-sweep",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _ramp_sweep_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-ramp-sweep",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _triangle_sweep_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-triangle-sweep",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "start_frequency_hz": result.start_frequency_hz,
        "stop_frequency_hz": result.stop_frequency_hz,
        "spacing": result.spacing,
        "sweep_time_s": result.sweep_time_s,
        "hold_time_s": result.hold_time_s,
        "return_time_s": result.return_time_s,
        "trigger_source": result.trigger_source,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _triangle_sweep_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-triangle-sweep",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _triangle_sweep_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-triangle-sweep",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _square_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-square",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "frequency_hz": result.frequency_hz,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "duty_cycle_percent": result.duty_cycle_percent,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _square_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-square",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _square_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-square",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _ramp_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-ramp",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "frequency_hz": result.frequency_hz,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "symmetry_percent": result.symmetry_percent,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _ramp_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-ramp",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _ramp_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-ramp",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _triangle_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-triangle",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "frequency_hz": result.frequency_hz,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _triangle_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-triangle",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _triangle_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-triangle",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _pulse_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-pulse",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "frequency_hz": result.frequency_hz,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "phase_deg": result.phase_deg,
        "pulse_width_s": result.pulse_width_s,
        "edge_time_s": result.edge_time_s,
        "leading_edge_s": result.leading_edge_s,
        "trailing_edge_s": result.trailing_edge_s,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _pulse_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-pulse",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _pulse_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-pulse",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _dc_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-dc",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "voltage_v": result.voltage_v,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _dc_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-dc",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _dc_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-dc",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _noise_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-noise",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "amplitude_vpp": result.amplitude_vpp,
        "offset_v": result.offset_v,
        "bandwidth_hz": result.bandwidth_hz,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _noise_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-noise",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _noise_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-noise",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _prbs_dry_run_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "configure-prbs",
        "mode": "dry-run",
        "model": result.model,
        "canonical_model_id": result.canonical_model_id,
        "bit_rate_bps": result.bit_rate_bps,
        "amplitude_vpp": result.amplitude_vpp,
        "pattern": result.pattern,
        "offset_v": result.offset_v,
        "edge_time_s": result.edge_time_s,
        "load": result.load,
        "commands": list(result.commands),
        "executed": result.executed,
        "output_state": result.output_state,
        "error": None,
    }


def _prbs_dry_run_error_payload(error: WavegenError) -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-prbs",
        "mode": "dry-run",
        "error": _error_text(error),
    }


def _prbs_dry_run_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "configure-prbs",
        "mode": "dry-run",
        "error": "internal_error: unexpected internal failure",
    }


def _control_error_payload(action: str, error: WavegenError) -> dict[str, object]:
    identity = error.identity
    return {
        "success": False,
        "action": action,
        "backend": error.backend,
        "transport": error.transport,
        "manufacturer": getattr(identity, "manufacturer", None),
        "model": getattr(identity, "model", None),
        "output_state": error.output_state,
        "error": _error_text(error),
    }


def _control_internal_error_payload(action: str) -> dict[str, object]:
    return {
        "success": False,
        "action": action,
        "backend": None,
        "transport": None,
        "manufacturer": None,
        "model": None,
        "output_state": None,
        "error": "internal_error: unexpected internal failure",
    }


def _status_success_payload(result: Any) -> dict[str, object]:
    return {
        "success": True,
        "action": "status",
        "backend": result.backend,
        "transport": result.transport,
        "manufacturer": result.identity.manufacturer,
        "model": result.identity.model,
        "output_state": result.output_state,
        "function": result.function,
        "frequency_hz": result.frequency_hz,
        "amplitude": result.amplitude,
        "amplitude_unit": result.amplitude_unit,
        "bandwidth_hz": result.bandwidth_hz,
        "offset_v": result.offset_v,
        "load": result.load,
        "error": None,
    }


def _error_queue_success_payload(result: Any) -> dict[str, object]:
    identity = result.identity
    return {
        "success": True,
        "action": "read-errors",
        "backend": result.backend,
        "transport": result.transport,
        "manufacturer": identity.manufacturer,
        "model": identity.model,
        "errors": [
            {
                "code": entry.code,
                "message": entry.message,
                "raw_response": entry.raw_response,
            }
            for entry in result.errors
        ],
        "read_count": result.read_count,
        "max_reads": result.max_reads,
        "has_errors": bool(result.errors),
        "empty_confirmed": result.empty_confirmed,
        "limit_reached": result.limit_reached,
        "error": None,
    }


def _error_queue_error_payload(
    error: WavegenError,
    max_reads: int,
) -> dict[str, object]:
    identity = error.identity
    return {
        "success": False,
        "action": "read-errors",
        "backend": error.backend,
        "transport": error.transport,
        "manufacturer": getattr(identity, "manufacturer", None),
        "model": getattr(identity, "model", None),
        "errors": [],
        "read_count": 0,
        "max_reads": max_reads,
        "has_errors": False,
        "empty_confirmed": False,
        "limit_reached": False,
        "error": _error_text(error),
    }


def _error_queue_internal_error_payload(max_reads: int) -> dict[str, object]:
    return {
        "success": False,
        "action": "read-errors",
        "backend": None,
        "transport": None,
        "manufacturer": None,
        "model": None,
        "errors": [],
        "read_count": 0,
        "max_reads": max_reads,
        "has_errors": False,
        "empty_confirmed": False,
        "limit_reached": False,
        "error": "internal_error: unexpected internal failure",
    }


def _status_error_payload(error: WavegenError) -> dict[str, object]:
    identity = error.identity
    return {
        "success": False,
        "action": "status",
        "backend": error.backend,
        "transport": error.transport,
        "manufacturer": getattr(identity, "manufacturer", None),
        "model": getattr(identity, "model", None),
        "output_state": None,
        "function": None,
        "frequency_hz": None,
        "amplitude": None,
        "amplitude_unit": None,
        "offset_v": None,
        "load": None,
        "error": _error_text(error),
    }


def _status_internal_error_payload() -> dict[str, object]:
    return {
        "success": False,
        "action": "status",
        "backend": None,
        "transport": None,
        "manufacturer": None,
        "model": None,
        "output_state": None,
        "function": None,
        "frequency_hz": None,
        "amplitude": None,
        "amplitude_unit": None,
        "offset_v": None,
        "load": None,
        "error": "internal_error: unexpected internal failure",
    }


def _human_success(result: Any) -> str:
    identity = result.identity
    lines = (
        "Instrument identified as a recognized model.",
        f"Backend: {result.backend}",
        f"Transport: {result.transport}",
        f"Manufacturer: {identity.manufacturer}",
        f"Model: {identity.model}",
        f"Serial: {identity.serial}",
        f"Firmware: {identity.firmware}",
        f"Canonical model ID: {identity.canonical_model_id}",
        "Model recognized: yes",
    )
    return "\n".join(lines)


def _human_resource_list_success(result: Any, *, live_only: bool) -> str:
    if not result.resources:
        label = "No live VISA resources found." if live_only else "No VISA resources found."
        return f"{label}\nBackend: {result.backend}"
    label = "Live VISA resources:" if live_only else "VISA resources:"
    lines = [label, f"Backend: {result.backend}"]
    if not live_only:
        lines.extend(f"- {entry.resource}" for entry in result.resources)
        return "\n".join(lines)
    for entry in result.resources:
        identity = (
            f"{entry.manufacturer} {entry.model}"
            if entry.manufacturer is not None and entry.model is not None
            else "Unknown instrument"
        )
        lines.extend((f"- {identity}", f"  Resource: {entry.resource}"))
    return "\n".join(lines)


def _human_control_success(action: str, result: Any) -> str:
    if action == "configure-sine":
        heading = "Channel 1 sine waveform configured with output off."
    elif action == "configure-sine-sweep":
        heading = "Channel 1 sine frequency sweep configured with output off."
    elif action == "configure-square-sweep":
        heading = "Channel 1 square frequency sweep configured with output off."
    elif action == "configure-ramp-sweep":
        heading = "Channel 1 ramp frequency sweep configured with output off."
    elif action == "configure-triangle-sweep":
        heading = "Channel 1 triangle frequency sweep configured with output off."
    elif action == "configure-square":
        heading = "Channel 1 square waveform configured with output off."
    elif action == "configure-ramp":
        heading = "Channel 1 ramp waveform configured with output off."
    elif action == "configure-triangle":
        heading = "Channel 1 triangle waveform configured with output off."
    elif action == "configure-pulse":
        heading = "Channel 1 pulse waveform configured with output off."
    elif action == "configure-dc":
        heading = "Channel 1 DC voltage configured with output off."
    elif action == "configure-noise":
        heading = "Channel 1 noise waveform configured with output off."
    elif action == "configure-prbs":
        heading = "Channel 1 PRBS waveform configured with output off."
    else:
        heading = f"Channel 1 output set to {result.output_state}."
    lines = [
        heading,
        f"Backend: {result.backend}",
        f"Transport: {result.transport}",
        f"Manufacturer: {result.identity.manufacturer}",
        f"Model: {result.identity.model}",
        f"Output state: {result.output_state}",
    ]
    if action in {
        "configure-sine",
        "configure-sine-sweep",
        "configure-square-sweep",
        "configure-ramp-sweep",
        "configure-triangle-sweep",
        "configure-square",
        "configure-ramp",
        "configure-triangle",
        "configure-pulse",
    }:
        lines.append(f"Phase (degrees): {result.phase_deg}")
    if action == "configure-sine-sweep":
        lines.extend(
            (
                f"Start frequency (Hz): {result.start_frequency_hz}",
                f"Stop frequency (Hz): {result.stop_frequency_hz}",
                f"Spacing: {result.spacing}",
                f"Sweep time (seconds): {result.sweep_time_s}",
                f"Hold time (seconds): {result.hold_time_s}",
                f"Return time (seconds): {result.return_time_s}",
                f"Trigger source: {result.trigger_source}",
            )
        )
    if action in {
        "configure-square-sweep",
        "configure-ramp-sweep",
        "configure-triangle-sweep",
    }:
        lines.extend(
            (
                f"Start frequency (Hz): {result.start_frequency_hz}",
                f"Stop frequency (Hz): {result.stop_frequency_hz}",
                f"Spacing: {result.spacing}",
                f"Sweep time (seconds): {result.sweep_time_s}",
                f"Hold time (seconds): {result.hold_time_s}",
                f"Return time (seconds): {result.return_time_s}",
                f"Trigger source: {result.trigger_source}",
                f"Amplitude (Vpp): {result.amplitude_vpp}",
                f"Offset (V): {result.offset_v}",
                f"Load: {result.load}",
            )
        )
        if action == "configure-square-sweep":
            lines.append(
                f"Duty cycle (percent): {result.duty_cycle_percent}"
            )
        elif action == "configure-ramp-sweep":
            lines.append(
                f"Symmetry (percent): {result.symmetry_percent}"
            )
    if action == "configure-pulse":
        if result.edge_time_s is not None:
            lines.append(f"Edge time (seconds): {result.edge_time_s}")
        else:
            lines.extend(
                (
                    f"Leading edge (seconds): {result.leading_edge_s}",
                    f"Trailing edge (seconds): {result.trailing_edge_s}",
                )
            )
    return "\n".join(lines)


def _human_sine_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 sine dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Phase (degrees): {result.phase_deg}",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_sine_sweep_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 sine sweep dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Start frequency (Hz): {result.start_frequency_hz}",
            f"Stop frequency (Hz): {result.stop_frequency_hz}",
            f"Spacing: {result.spacing}",
            f"Sweep time (seconds): {result.sweep_time_s}",
            f"Hold time (seconds): {result.hold_time_s}",
            f"Return time (seconds): {result.return_time_s}",
            f"Trigger source: {result.trigger_source}",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_frequency_sweep_dry_run_success(
    result: Any,
    waveform: str,
    specific_line: str | None = None,
) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    lines = [
        f"Channel 1 {waveform} sweep dry-run completed; no VISA I/O was performed.",
        f"Target model: {result.model}",
        f"Canonical model ID: {result.canonical_model_id}",
        "Executed: no",
        f"Start frequency (Hz): {result.start_frequency_hz}",
        f"Stop frequency (Hz): {result.stop_frequency_hz}",
        f"Spacing: {result.spacing}",
        f"Sweep time (seconds): {result.sweep_time_s}",
        f"Hold time (seconds): {result.hold_time_s}",
        f"Return time (seconds): {result.return_time_s}",
        f"Trigger source: {result.trigger_source}",
        f"Amplitude (Vpp): {result.amplitude_vpp}",
        f"Offset (V): {result.offset_v}",
        f"Phase (degrees): {result.phase_deg}",
        f"Load: {result.load}",
    ]
    if specific_line is not None:
        lines.append(specific_line)
    lines.extend(
        (
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )
    return "\n".join(lines)


def _human_square_sweep_dry_run_success(result: Any) -> str:
    return _human_frequency_sweep_dry_run_success(
        result,
        "square",
        f"Duty cycle (percent): {result.duty_cycle_percent}",
    )


def _human_ramp_sweep_dry_run_success(result: Any) -> str:
    return _human_frequency_sweep_dry_run_success(
        result,
        "ramp",
        f"Symmetry (percent): {result.symmetry_percent}",
    )


def _human_triangle_sweep_dry_run_success(result: Any) -> str:
    return _human_frequency_sweep_dry_run_success(result, "triangle")


def _human_square_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 square dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Phase (degrees): {result.phase_deg}",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_ramp_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 ramp dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Phase (degrees): {result.phase_deg}",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_triangle_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 triangle dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Phase (degrees): {result.phase_deg}",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_pulse_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    edge_lines = (
        (f"Edge time (seconds): {result.edge_time_s}",)
        if result.edge_time_s is not None
        else (
            f"Leading edge (seconds): {result.leading_edge_s}",
            f"Trailing edge (seconds): {result.trailing_edge_s}",
        )
    )
    return "\n".join(
        (
            "Channel 1 pulse dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Phase (degrees): {result.phase_deg}",
            *edge_lines,
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_dc_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 DC dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_noise_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 noise dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_prbs_dry_run_success(result: Any) -> str:
    commands = "\n".join(f"- {command}" for command in result.commands)
    return "\n".join(
        (
            "Channel 1 PRBS dry-run completed; no VISA I/O was performed.",
            f"Target model: {result.model}",
            f"Canonical model ID: {result.canonical_model_id}",
            "Executed: no",
            f"Planned output state: {result.output_state}",
            "Planned SCPI commands:",
            commands,
        )
    )


def _human_status_success(result: Any) -> str:
    lines = [
        f"Instrument: {result.identity.manufacturer} {result.identity.model}",
        f"Backend: {result.backend}",
        f"Transport: {result.transport}",
        f"Channel 1 output: {result.output_state}",
        f"Function: {result.function}",
    ]
    if result.function == "DC":
        lines.append(f"DC voltage: {result.offset_v:g} V")
    elif result.function in {"NOIS", "NOISE"}:
        lines.extend(
            (
                f"Amplitude: {result.amplitude:g} {result.amplitude_unit}",
                f"Bandwidth: {result.bandwidth_hz:g} Hz",
                f"Offset: {result.offset_v:g} V",
            )
        )
    else:
        lines.extend(
            (
                f"Frequency: {result.frequency_hz:g} Hz",
                f"Amplitude: {result.amplitude:g} {result.amplitude_unit}",
                f"Offset: {result.offset_v:g} V",
            )
        )
    lines.append(f"Output-load setting: {result.load}")
    return "\n".join(lines)


def _human_error_queue_success(result: Any) -> str:
    lines = [f"Instrument: {result.identity.manufacturer} {result.identity.model}"]
    if result.errors:
        lines.append("System error queue:")
        lines.extend(
            f"{index}. {entry.code}: {entry.message}"
            for index, entry in enumerate(result.errors, start=1)
        )
    else:
        lines.append("System error queue: no errors")
    lines.extend(
        (
            f"Reads: {result.read_count}/{result.max_reads}",
            f"Queue empty confirmed: {'yes' if result.empty_confirmed else 'no'}",
            f"Read limit reached: {'yes' if result.limit_reached else 'no'}",
        )
    )
    return "\n".join(lines)


def _human_error(error: WavegenError) -> str:
    return f"Error [{error.code}]: {_error_message(error)}"


def _error_text(error: WavegenError) -> str:
    return f"{error.code}: {_error_message(error)}"


def _error_message(error: WavegenError) -> str:
    message = str(error)
    if error.cleanup_errors:
        message += " Cleanup also failed: " + "; ".join(error.cleanup_errors) + "."
    return message


def _exit_code_for_error(error: WavegenError) -> ExitCode:
    for error_type, exit_code in _ERROR_EXIT_CODES:
        if isinstance(error, error_type):
            return exit_code
    return ExitCode.INTERNAL_ERROR
