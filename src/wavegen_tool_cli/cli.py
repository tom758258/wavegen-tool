"""Console entry point for the read-only identify milestone."""

from __future__ import annotations

import argparse
import json
import sys
from enum import IntEnum
from typing import Any, Sequence

from wavegen_tool_core import (
    IdnQueryError,
    MalformedIdnError,
    ResourceManagerError,
    ResourceOpenError,
    UnsupportedBackendError,
    UnsupportedConnectionScopeError,
    UnsupportedInstrumentError,
    UnsupportedTransportError,
    VisaCleanupError,
    WavegenError,
    identify_instrument,
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
    INTERNAL_ERROR = 70


_ERROR_EXIT_CODES: tuple[tuple[type[WavegenError], ExitCode], ...] = (
    (UnsupportedBackendError, ExitCode.CLI_USAGE),
    (UnsupportedTransportError, ExitCode.UNSUPPORTED_TRANSPORT),
    (UnsupportedConnectionScopeError, ExitCode.UNSUPPORTED_CONNECTION_SCOPE),
    (ResourceManagerError, ExitCode.RESOURCE_MANAGER_ERROR),
    (ResourceOpenError, ExitCode.RESOURCE_OPEN_ERROR),
    (IdnQueryError, ExitCode.IDN_QUERY_ERROR),
    (MalformedIdnError, ExitCode.MALFORMED_IDN),
    (UnsupportedInstrumentError, ExitCode.UNSUPPORTED_INSTRUMENT),
    (VisaCleanupError, ExitCode.VISA_CLEANUP_ERROR),
)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser without touching VISA."""

    parser = argparse.ArgumentParser(
        prog="wavegen-tool",
        description="Safely identify an explicitly selected waveform generator.",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a stable process exit code."""

    args = build_parser().parse_args(argv)
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
