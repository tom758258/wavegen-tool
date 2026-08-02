"""Pure Wavegen Worker command request admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from wavegen_tool_core.errors import WaveformParameterError
from wavegen_tool_core.identity import CANONICAL_MODEL_ID
from wavegen_tool_core.visa import (
    dry_run_dc,
    dry_run_noise,
    dry_run_prbs,
    dry_run_pulse,
    dry_run_ramp,
    dry_run_sine,
    dry_run_square,
    dry_run_triangle,
)

__all__ = [
    "ValidatedWorkerCommand",
    "WorkerRequestValidationError",
    "validate_worker_command_request",
]


class WorkerRequestValidationError(ValueError):
    """A Worker command request failed admission validation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedWorkerCommand:
    """A validated command envelope ready for a later command runner."""

    schema_version: int
    command: str
    arguments: dict[str, object]
    job_id: str | None
    context: dict[str, object]


_ALLOWED_TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "command", "arguments", "job_id", "context"}
)
_ALLOWED_CONTEXT_FIELDS = frozenset(
    {"mode", "expected_model_id", "planning_model_id"}
)
_REQUEST_MODES = frozenset({"live", "simulate", "dry_run"})
_WORKER_MODES = frozenset({"live", "simulate"})
_SUPPORTED_COMMANDS = frozenset(
    {
        "identify",
        "status",
        "read-errors",
        "configure-sine",
        "configure-square",
        "configure-ramp",
        "configure-triangle",
        "configure-pulse",
        "configure-dc",
        "configure-noise",
        "configure-prbs",
        "output",
    }
)
_CONFIGURE_COMMANDS = frozenset(
    {
        "configure-sine",
        "configure-square",
        "configure-ramp",
        "configure-triangle",
        "configure-pulse",
        "configure-dc",
        "configure-noise",
        "configure-prbs",
    }
)
_DRY_RUN_COMMANDS = _CONFIGURE_COMMANDS

_ARGUMENT_FIELDS: dict[str, frozenset[str]] = {
    "identify": frozenset(),
    "status": frozenset(),
    "read-errors": frozenset({"max_reads"}),
    "configure-sine": frozenset(
        {"frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load"}
    ),
    "configure-square": frozenset(
        {
            "frequency_hz",
            "amplitude_vpp",
            "offset_v",
            "phase_deg",
            "duty_cycle_percent",
            "load",
        }
    ),
    "configure-ramp": frozenset(
        {
            "frequency_hz",
            "amplitude_vpp",
            "offset_v",
            "phase_deg",
            "symmetry_percent",
            "load",
        }
    ),
    "configure-triangle": frozenset(
        {"frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load"}
    ),
    "configure-pulse": frozenset(
        {
            "frequency_hz",
            "amplitude_vpp",
            "pulse_width_s",
            "offset_v",
            "edge_time_s",
            "phase_deg",
            "load",
        }
    ),
    "configure-dc": frozenset({"voltage_v", "load"}),
    "configure-noise": frozenset(
        {"amplitude_vpp", "bandwidth_hz", "offset_v", "load"}
    ),
    "configure-prbs": frozenset(
        {"bit_rate_bps", "amplitude_vpp", "pattern", "offset_v", "edge_time_s", "load"}
    ),
    "output": frozenset({"enabled", "confirm_output"}),
}
_REQUIRED_ARGUMENT_FIELDS: dict[str, frozenset[str]] = {
    "read-errors": frozenset(),
    "configure-sine": frozenset({"frequency_hz", "amplitude_vpp"}),
    "configure-square": frozenset({"frequency_hz", "amplitude_vpp"}),
    "configure-ramp": frozenset({"frequency_hz", "amplitude_vpp"}),
    "configure-triangle": frozenset({"frequency_hz", "amplitude_vpp"}),
    "configure-pulse": frozenset(
        {"frequency_hz", "amplitude_vpp", "pulse_width_s"}
    ),
    "configure-dc": frozenset({"voltage_v"}),
    "configure-noise": frozenset({"amplitude_vpp", "bandwidth_hz"}),
    "configure-prbs": frozenset({"bit_rate_bps", "amplitude_vpp"}),
    "output": frozenset({"enabled"}),
}
_DEFAULT_ARGUMENTS: dict[str, dict[str, object]] = {
    "read-errors": {"max_reads": 20},
    "configure-sine": {"offset_v": 0, "phase_deg": 0.0, "load": "50"},
    "configure-square": {
        "offset_v": 0,
        "phase_deg": 0.0,
        "duty_cycle_percent": 50,
        "load": "50",
    },
    "configure-ramp": {
        "offset_v": 0,
        "phase_deg": 0.0,
        "symmetry_percent": 100,
        "load": "50",
    },
    "configure-triangle": {"offset_v": 0, "phase_deg": 0.0, "load": "50"},
    "configure-pulse": {
        "offset_v": 0,
        "phase_deg": 0.0,
        "edge_time_s": 1e-8,
        "load": "50",
    },
    "configure-dc": {"load": "50"},
    "configure-noise": {"offset_v": 0, "load": "50"},
    "configure-prbs": {
        "pattern": "PN7",
        "offset_v": 0,
        "edge_time_s": 8.4e-9,
        "load": "50",
    },
}


def validate_worker_command_request(
    payload: object,
    *,
    worker_mode: str,
    allow_output_writes: bool,
) -> ValidatedWorkerCommand:
    """Validate a Worker command without performing device or simulator I/O."""

    if not isinstance(payload, Mapping):
        raise WorkerRequestValidationError(
            "invalid_request", "Worker command payload must be an object."
        )
    if type(allow_output_writes) is not bool:
        raise WorkerRequestValidationError(
            "invalid_request", "allow_output_writes must be a boolean."
        )

    unknown_fields = _unknown_fields(payload, _ALLOWED_TOP_LEVEL_FIELDS)
    if unknown_fields:
        raise WorkerRequestValidationError(
            "invalid_request",
            f"Unknown top-level field(s): {_format_fields(unknown_fields)}.",
        )

    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 2:
        raise WorkerRequestValidationError(
            "invalid_request", "schema_version must be the exact integer 2."
        )

    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise WorkerRequestValidationError(
            "invalid_request", "command must be a non-empty string."
        )

    raw_arguments = payload.get("arguments", {})
    if not isinstance(raw_arguments, Mapping):
        raise WorkerRequestValidationError(
            "invalid_request", "arguments must be an object."
        )
    arguments = dict(raw_arguments)

    if "job_id" in payload and not isinstance(payload["job_id"], str):
        raise WorkerRequestValidationError(
            "invalid_request", "job_id must be a string when provided."
        )
    job_id = payload.get("job_id")

    raw_context = payload.get("context")
    if not isinstance(raw_context, Mapping):
        raise WorkerRequestValidationError(
            "invalid_context", "context must be an object."
        )
    context, request_mode = _validate_context(raw_context, worker_mode)

    if command not in _SUPPORTED_COMMANDS:
        raise WorkerRequestValidationError(
            "unsupported_command", f"Unsupported Worker command: {command}."
        )
    if request_mode == "dry_run" and command not in _DRY_RUN_COMMANDS:
        raise WorkerRequestValidationError(
            "unsupported_command",
            "dry_run context only supports configure-* commands.",
        )

    _validate_arguments(command, arguments)
    _validate_live_write_safety(
        request_mode,
        command,
        arguments,
        allow_output_writes=allow_output_writes,
    )

    if command in _CONFIGURE_COMMANDS:
        planning_model_id = (
            CANONICAL_MODEL_ID
            if request_mode == "live"
            else context["planning_model_id"]
        )
        _validate_waveform_arguments(command, arguments, planning_model_id)

    return ValidatedWorkerCommand(
        schema_version=schema_version,
        command=command,
        arguments=arguments,
        job_id=job_id,
        context=context,
    )


def _validate_context(
    raw_context: Mapping[object, object],
    worker_mode: str,
) -> tuple[dict[str, object], str]:
    context = dict(raw_context)
    unknown_fields = _unknown_fields(context, _ALLOWED_CONTEXT_FIELDS)
    if unknown_fields:
        raise WorkerRequestValidationError(
            "invalid_context",
            f"Unknown context field(s): {_format_fields(unknown_fields)}.",
        )

    request_mode = context.get("mode")
    if not isinstance(request_mode, str) or request_mode not in _REQUEST_MODES:
        raise WorkerRequestValidationError(
            "invalid_context",
            "context.mode must be live, simulate, or dry_run.",
        )

    if request_mode == "live":
        if "planning_model_id" in context:
            raise WorkerRequestValidationError(
                "invalid_context",
                "live context must not include planning_model_id.",
            )
        if "expected_model_id" in context and (
            not isinstance(context["expected_model_id"], str)
            or context["expected_model_id"] != CANONICAL_MODEL_ID
        ):
            raise WorkerRequestValidationError(
                "invalid_context",
                "expected_model_id must be keysight-33521b when provided.",
            )
    else:
        if "expected_model_id" in context:
            raise WorkerRequestValidationError(
                "invalid_context",
                f"{request_mode} context must not include expected_model_id.",
            )
        if (
            "planning_model_id" not in context
            or not isinstance(context["planning_model_id"], str)
            or context["planning_model_id"] != CANONICAL_MODEL_ID
        ):
            raise WorkerRequestValidationError(
                "invalid_context",
                f"{request_mode} context requires planning_model_id "
                "keysight-33521b.",
            )

    if not isinstance(worker_mode, str) or worker_mode not in _WORKER_MODES:
        raise WorkerRequestValidationError(
            "invalid_context", "worker_mode must be live or simulate."
        )
    allowed_request_modes = (
        frozenset({"live", "dry_run"})
        if worker_mode == "live"
        else frozenset({"simulate", "dry_run"})
    )
    if request_mode not in allowed_request_modes:
        raise WorkerRequestValidationError(
            "invalid_context",
            f"{worker_mode} Worker cannot accept {request_mode} requests.",
        )

    return context, request_mode


def _validate_arguments(command: str, arguments: dict[str, object]) -> None:
    unknown_fields = _unknown_fields(arguments, _ARGUMENT_FIELDS[command])
    if unknown_fields:
        raise WorkerRequestValidationError(
            "invalid_arguments",
            f"Unknown argument field(s) for {command}: "
            f"{_format_fields(unknown_fields)}.",
        )

    missing_fields = [
        field for field in _REQUIRED_ARGUMENT_FIELDS.get(command, frozenset()) if field not in arguments
    ]
    if missing_fields:
        raise WorkerRequestValidationError(
            "invalid_arguments",
            f"Missing required argument field(s) for {command}: "
            f"{_format_fields(missing_fields)}.",
        )

    for field, default in _DEFAULT_ARGUMENTS.get(command, {}).items():
        arguments.setdefault(field, default)

    if command == "read-errors":
        max_reads = arguments["max_reads"]
        if isinstance(max_reads, bool) or not isinstance(max_reads, int) or not 1 <= max_reads <= 100:
            raise WorkerRequestValidationError(
                "invalid_arguments", "read-errors max_reads must be an integer from 1 to 100."
            )
    elif command == "output":
        if type(arguments["enabled"]) is not bool:
            raise WorkerRequestValidationError(
                "invalid_arguments", "output enabled must be a boolean."
            )
        if "confirm_output" in arguments and type(arguments["confirm_output"]) is not bool:
            raise WorkerRequestValidationError(
                "invalid_arguments", "output confirm_output must be a boolean."
            )


def _validate_live_write_safety(
    request_mode: str,
    command: str,
    arguments: Mapping[str, object],
    *,
    allow_output_writes: bool,
) -> None:
    if request_mode != "live":
        return

    if command in _CONFIGURE_COMMANDS and not allow_output_writes:
        raise WorkerRequestValidationError(
            "live_write_not_allowed",
            f"{command} requires allow_output_writes=True in live context.",
        )

    if command != "output" or arguments["enabled"] is False:
        return
    if not allow_output_writes:
        raise WorkerRequestValidationError(
            "live_write_not_allowed",
            "live output enable requires allow_output_writes=True.",
        )
    if arguments.get("confirm_output") is not True:
        raise WorkerRequestValidationError(
            "output_confirmation_required",
            "live output enable requires confirm_output=True.",
        )


def _validate_waveform_arguments(
    command: str,
    arguments: Mapping[str, object],
    model_id: object,
) -> None:
    try:
        if command == "configure-sine":
            dry_run_sine(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                arguments["phase_deg"],
            )
        elif command == "configure-square":
            dry_run_square(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["duty_cycle_percent"],
                arguments["load"],
                arguments["phase_deg"],
            )
        elif command == "configure-ramp":
            dry_run_ramp(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["symmetry_percent"],
                arguments["load"],
                arguments["phase_deg"],
            )
        elif command == "configure-triangle":
            dry_run_triangle(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                arguments["phase_deg"],
            )
        elif command == "configure-pulse":
            dry_run_pulse(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["pulse_width_s"],
                arguments["offset_v"],
                arguments["edge_time_s"],
                arguments["load"],
                arguments["phase_deg"],
            )
        elif command == "configure-dc":
            dry_run_dc(
                model_id,
                arguments["voltage_v"],
                arguments["load"],
            )
        elif command == "configure-noise":
            dry_run_noise(
                model_id,
                arguments["amplitude_vpp"],
                arguments["bandwidth_hz"],
                arguments["offset_v"],
                arguments["load"],
            )
        elif command == "configure-prbs":
            dry_run_prbs(
                model_id,
                arguments["bit_rate_bps"],
                arguments["amplitude_vpp"],
                arguments["pattern"],
                arguments["offset_v"],
                arguments["edge_time_s"],
                arguments["load"],
            )
    except WaveformParameterError as exc:
        raise WorkerRequestValidationError("invalid_arguments", str(exc)) from exc


def _unknown_fields(
    value: Mapping[object, object],
    allowed_fields: frozenset[str],
) -> list[object]:
    return [field for field in value if field not in allowed_fields]


def _format_fields(fields: list[object]) -> str:
    return ", ".join(repr(field) for field in fields)
