"""Console entry point for explicit VISA identification and control."""

from __future__ import annotations

import argparse
import json
import sys
from enum import IntEnum
from typing import Any, Sequence

from wavegen_tool_core import (
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
    WavegenError,
    configure_sine,
    configure_square,
    identify_instrument,
    list_resources,
    normalize_serial_baud_rate,
    query_status,
    set_output,
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
    identify_parser.add_argument(
        "--resource",
        required=True,
        help="Explicit USB or TCPIP/LAN VISA resource.",
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
    status_parser.add_argument(
        "--resource",
        required=True,
        help="Explicit USB or TCPIP/LAN VISA resource.",
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

    sine_parser = subparsers.add_parser(
        "configure-sine",
        help="Configure a validated Channel 1 sine waveform with output off.",
    )
    sine_parser.add_argument(
        "--resource",
        required=True,
        help="Explicit USB or TCPIP/LAN VISA resource.",
    )
    sine_parser.add_argument(
        "--backend",
        default="system",
        help="VISA backend name validated by Core (default: system).",
    )
    sine_parser.add_argument("--frequency-hz", required=True, help="Sine frequency in Hz.")
    sine_parser.add_argument(
        "--amplitude-vpp",
        required=True,
        help="Sine amplitude in Vpp.",
    )
    sine_parser.add_argument(
        "--offset-v",
        default="0",
        help="DC offset in volts (default: 0).",
    )
    sine_parser.add_argument(
        "--load",
        choices=("50", "high-z"),
        default="50",
        help="Output load (default: 50).",
    )
    sine_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    square_parser = subparsers.add_parser(
        "configure-square",
        help="Configure a validated Channel 1 square waveform with output off.",
    )
    square_parser.add_argument(
        "--resource",
        required=True,
        help="Explicit USB or TCPIP/LAN VISA resource.",
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
    square_parser.add_argument(
        "--amplitude-vpp",
        required=True,
        help="Square amplitude in Vpp.",
    )
    square_parser.add_argument(
        "--offset-v",
        default="0",
        help="DC offset in volts (default: 0).",
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
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit exactly one JSON object.",
    )

    output_parser = subparsers.add_parser(
        "output",
        help="Explicitly set Channel 1 output on or off.",
    )
    output_parser.add_argument(
        "--resource",
        required=True,
        help="Explicit USB or TCPIP/LAN VISA resource.",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a stable process exit code."""

    args = build_parser().parse_args(argv)
    if args.command == "list-resources":
        return _run_list_resources(args)
    if args.command == "configure-sine":
        return _run_configure_sine(args)
    if args.command == "configure-square":
        return _run_configure_square(args)
    if args.command == "output":
        return _run_output(args)
    if args.command == "status":
        return _run_status(args)
    return _run_identify(args)


def _run_identify(args: argparse.Namespace) -> int:
    try:
        result = identify_instrument(args.resource, args.backend)
    except WavegenError as exc:
        if args.json_output:
            print(json.dumps(_error_payload(exc), separators=(",", ":")))
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(json.dumps(_internal_error_payload(), separators=(",", ":")))
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(json.dumps(_success_payload(result), separators=(",", ":")))
    else:
        print(_human_success(result))
    return int(ExitCode.SUCCESS)


def _run_list_resources(args: argparse.Namespace) -> int:
    try:
        result = list_resources(
            args.backend,
            live_only=args.live_only,
            serial_baud_rate=args.serial_baud_rate,
            serial_read_termination=args.serial_read_termination,
            serial_write_termination=args.serial_write_termination,
        )
    except WavegenError as exc:
        if args.json_output:
            print(json.dumps(_resource_list_error_payload(exc), separators=(",", ":")))
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(json.dumps(_resource_list_internal_error_payload(), separators=(",", ":")))
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(json.dumps(_resource_list_success_payload(result), separators=(",", ":")))
    else:
        print(_human_resource_list_success(result, live_only=args.live_only))
    return int(ExitCode.SUCCESS)


def _run_configure_sine(args: argparse.Namespace) -> int:
    return _run_control(
        args,
        lambda: configure_sine(
            args.resource,
            args.frequency_hz,
            args.amplitude_vpp,
            args.offset_v,
            args.load,
            args.backend,
        ),
    )


def _run_configure_square(args: argparse.Namespace) -> int:
    return _run_control(
        args,
        lambda: configure_square(
            args.resource,
            args.frequency_hz,
            args.amplitude_vpp,
            args.offset_v,
            args.duty_cycle_percent,
            args.load,
            args.backend,
        ),
    )


def _run_output(args: argparse.Namespace) -> int:
    return _run_control(
        args,
        lambda: set_output(args.resource, args.state, args.backend),
    )


def _run_status(args: argparse.Namespace) -> int:
    try:
        result = query_status(args.resource, args.backend)
    except WavegenError as exc:
        if args.json_output:
            print(json.dumps(_status_error_payload(exc), separators=(",", ":")))
        else:
            print(_human_error(exc), file=sys.stderr)
        return int(_exit_code_for_error(exc))
    except Exception:
        if args.json_output:
            print(json.dumps(_status_internal_error_payload(), separators=(",", ":")))
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(json.dumps(_status_success_payload(result), separators=(",", ":")))
    else:
        print(_human_status_success(result))
    return int(ExitCode.SUCCESS)


def _run_control(args: argparse.Namespace, operation: Any) -> int:
    try:
        result = operation()
    except WavegenError as exc:
        if args.json_output:
            print(
                json.dumps(
                    _control_error_payload(args.command, exc),
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
                    _control_internal_error_payload(args.command),
                    separators=(",", ":"),
                )
            )
        else:
            print("Error [internal_error]: unexpected internal failure.", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)

    if args.json_output:
        print(
            json.dumps(
                _control_success_payload(args.command, result),
                separators=(",", ":"),
            )
        )
    else:
        print(_human_control_success(args.command, result))
    return int(ExitCode.SUCCESS)


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
    if action in {"configure-sine", "configure-square"}:
        payload.update(
            frequency_hz=result.frequency_hz,
            amplitude_vpp=result.amplitude_vpp,
            offset_v=result.offset_v,
            load=result.load,
        )
    if action == "configure-square":
        payload["duty_cycle_percent"] = result.duty_cycle_percent
    return payload


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
        "offset_v": result.offset_v,
        "load": result.load,
        "error": None,
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
    elif action == "configure-square":
        heading = "Channel 1 square waveform configured with output off."
    else:
        heading = f"Channel 1 output set to {result.output_state}."
    return "\n".join(
        (
            heading,
            f"Backend: {result.backend}",
            f"Transport: {result.transport}",
            f"Manufacturer: {result.identity.manufacturer}",
            f"Model: {result.identity.model}",
            f"Output state: {result.output_state}",
        )
    )


def _human_status_success(result: Any) -> str:
    return "\n".join(
        (
            f"Instrument: {result.identity.manufacturer} {result.identity.model}",
            f"Backend: {result.backend}",
            f"Transport: {result.transport}",
            f"Channel 1 output: {result.output_state}",
            f"Function: {result.function}",
            f"Frequency: {result.frequency_hz:g} Hz",
            f"Amplitude: {result.amplitude:g} {result.amplitude_unit}",
            f"Offset: {result.offset_v:g} V",
            f"Output-load setting: {result.load}",
        )
    )


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
