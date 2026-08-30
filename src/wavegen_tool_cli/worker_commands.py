"""Pure Wavegen Worker command request admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from wavegen_tool_core import resolve_voltage_inputs
from wavegen_tool_core.errors import WaveformParameterError
from wavegen_tool_core.identity import model_info_for_model_id
from wavegen_tool_cli.worker_protocol import WORKER_SCHEMA_VERSION
from wavegen_tool_core.visa import (
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
        "output",
    }
)
_CONFIGURE_COMMANDS = frozenset(
    {
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
)
_DRY_RUN_COMMANDS = _CONFIGURE_COMMANDS
_CHANNEL_COMMANDS = _CONFIGURE_COMMANDS | frozenset({"status", "output"})

_ARGUMENT_FIELDS: dict[str, frozenset[str]] = {
    "identify": frozenset(),
    "status": frozenset(),
    "read-errors": frozenset({"max_reads"}),
    "configure-sine": frozenset(
        {
            "frequency_hz",
            "amplitude_vpp",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "phase_deg",
            "load",
        }
    ),
    "configure-sine-sweep": frozenset(
        {
            "start_frequency_hz",
            "stop_frequency_hz",
            "spacing",
            "sweep_time_s",
            "hold_time_s",
            "return_time_s",
            "amplitude_vpp",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "phase_deg",
            "load",
        }
    ),
    "configure-square-sweep": frozenset(
        {
            "start_frequency_hz",
            "stop_frequency_hz",
            "spacing",
            "sweep_time_s",
            "hold_time_s",
            "return_time_s",
            "amplitude_vpp",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "phase_deg",
            "duty_cycle_percent",
            "load",
        }
    ),
    "configure-ramp-sweep": frozenset(
        {
            "start_frequency_hz",
            "stop_frequency_hz",
            "spacing",
            "sweep_time_s",
            "hold_time_s",
            "return_time_s",
            "amplitude_vpp",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "phase_deg",
            "symmetry_percent",
            "load",
        }
    ),
    "configure-triangle-sweep": frozenset(
        {
            "start_frequency_hz",
            "stop_frequency_hz",
            "spacing",
            "sweep_time_s",
            "hold_time_s",
            "return_time_s",
            "amplitude_vpp",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "phase_deg",
            "load",
        }
    ),
    "configure-square": frozenset(
        {
            "frequency_hz",
            "amplitude_vpp",
            "offset_v",
            "high_level_v",
            "low_level_v",
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
            "high_level_v",
            "low_level_v",
            "phase_deg",
            "symmetry_percent",
            "load",
        }
    ),
    "configure-triangle": frozenset(
        {
            "frequency_hz",
            "amplitude_vpp",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "phase_deg",
            "load",
        }
    ),
    "configure-pulse": frozenset(
        {
            "frequency_hz",
            "amplitude_vpp",
            "pulse_width_s",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "edge_time_s",
            "leading_edge_s",
            "trailing_edge_s",
            "phase_deg",
            "load",
        }
    ),
    "configure-dc": frozenset({"voltage_v", "load"}),
    "configure-noise": frozenset(
        {
            "amplitude_vpp",
            "bandwidth_hz",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "load",
        }
    ),
    "configure-prbs": frozenset(
        {
            "bit_rate_bps",
            "amplitude_vpp",
            "pattern",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "edge_time_s",
            "load",
        }
    ),
    "output": frozenset({"enabled", "confirm_output"}),
}
_REQUIRED_ARGUMENT_FIELDS: dict[str, frozenset[str]] = {
    "read-errors": frozenset(),
    "configure-sine": frozenset({"frequency_hz"}),
    "configure-sine-sweep": frozenset(
        {"start_frequency_hz", "stop_frequency_hz", "spacing", "sweep_time_s"}
    ),
    "configure-square-sweep": frozenset(
        {"start_frequency_hz", "stop_frequency_hz", "spacing", "sweep_time_s"}
    ),
    "configure-ramp-sweep": frozenset(
        {"start_frequency_hz", "stop_frequency_hz", "spacing", "sweep_time_s"}
    ),
    "configure-triangle-sweep": frozenset(
        {"start_frequency_hz", "stop_frequency_hz", "spacing", "sweep_time_s"}
    ),
    "configure-square": frozenset({"frequency_hz"}),
    "configure-ramp": frozenset({"frequency_hz"}),
    "configure-triangle": frozenset({"frequency_hz"}),
    "configure-pulse": frozenset(
        {"frequency_hz", "pulse_width_s"}
    ),
    "configure-dc": frozenset({"voltage_v"}),
    "configure-noise": frozenset({"bandwidth_hz"}),
    "configure-prbs": frozenset({"bit_rate_bps"}),
    "output": frozenset({"enabled"}),
}
_DEFAULT_ARGUMENTS: dict[str, dict[str, object]] = {
    "read-errors": {"max_reads": 20},
    "configure-sine": {"phase_deg": 0.0, "load": "50"},
    "configure-sine-sweep": {
        "hold_time_s": 0,
        "return_time_s": 0,
        "phase_deg": 0.0,
        "load": "50",
    },
    "configure-square-sweep": {
        "hold_time_s": 0,
        "return_time_s": 0,
        "phase_deg": 0.0,
        "duty_cycle_percent": 50,
        "load": "50",
    },
    "configure-ramp-sweep": {
        "hold_time_s": 0,
        "return_time_s": 0,
        "phase_deg": 0.0,
        "symmetry_percent": 100,
        "load": "50",
    },
    "configure-triangle-sweep": {
        "hold_time_s": 0,
        "return_time_s": 0,
        "phase_deg": 0.0,
        "load": "50",
    },
    "configure-square": {
        "phase_deg": 0.0,
        "duty_cycle_percent": 50,
        "load": "50",
    },
    "configure-ramp": {
        "phase_deg": 0.0,
        "symmetry_percent": 100,
        "load": "50",
    },
    "configure-triangle": {"phase_deg": 0.0, "load": "50"},
    "configure-pulse": {
        "phase_deg": 0.0,
        "load": "50",
    },
    "configure-dc": {"load": "50"},
    "configure-noise": {"load": "50"},
    "configure-prbs": {
        "pattern": "PN7",
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
    if type(schema_version) is not int or schema_version != WORKER_SCHEMA_VERSION:
        raise WorkerRequestValidationError(
            "invalid_request",
            f"schema_version must be the exact integer {WORKER_SCHEMA_VERSION}.",
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
        _canonicalize_voltage_arguments(command, arguments)
        validation_model_id = (
            context.get("expected_model_id")
            if request_mode == "live"
            else context["planning_model_id"]
        )
        if validation_model_id is not None:
            _validate_waveform_arguments(
                command,
                arguments,
                validation_model_id,
                channel=arguments["channel"],
            )

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
        if "expected_model_id" in context:
            _validate_registered_model_id(
                context["expected_model_id"],
                field_name="expected_model_id",
            )
    else:
        if "expected_model_id" in context:
            raise WorkerRequestValidationError(
                "invalid_context",
                f"{request_mode} context must not include expected_model_id.",
            )
        if "planning_model_id" not in context:
            raise WorkerRequestValidationError(
                "invalid_context",
                f"{request_mode} context requires planning_model_id.",
            )
        _validate_registered_model_id(
            context["planning_model_id"],
            field_name="planning_model_id",
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
    allowed_fields = _ARGUMENT_FIELDS[command]
    if command in _CHANNEL_COMMANDS:
        allowed_fields = allowed_fields | {"channel"}
    unknown_fields = _unknown_fields(arguments, allowed_fields)
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

    if command in _CHANNEL_COMMANDS:
        channel = arguments.setdefault("channel", 1)
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
            raise WorkerRequestValidationError(
                "invalid_arguments", "channel must be a positive integer."
            )

    if command == "configure-pulse":
        has_shared = "edge_time_s" in arguments
        has_leading = "leading_edge_s" in arguments
        has_trailing = "trailing_edge_s" in arguments
        if has_leading != has_trailing:
            raise WorkerRequestValidationError(
                "invalid_arguments",
                "Pulse leading_edge_s and trailing_edge_s must be provided together.",
            )
        if has_shared and (has_leading or has_trailing):
            raise WorkerRequestValidationError(
                "invalid_arguments",
                "Pulse edge_time_s cannot be combined with leading_edge_s "
                "or trailing_edge_s.",
            )
        if not has_shared and not has_leading:
            arguments["edge_time_s"] = 1e-8

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


_VOLTAGE_WAVEFORMS = {
    "configure-sine": "Sine",
    "configure-sine-sweep": "Sine sweep",
    "configure-square-sweep": "Square sweep",
    "configure-ramp-sweep": "Ramp sweep",
    "configure-triangle-sweep": "Triangle sweep",
    "configure-square": "Square",
    "configure-ramp": "Ramp",
    "configure-triangle": "Triangle",
    "configure-pulse": "Pulse",
    "configure-noise": "Noise",
    "configure-prbs": "PRBS",
}


def _canonicalize_voltage_arguments(
    command: str,
    arguments: dict[str, object],
) -> None:
    waveform = _VOLTAGE_WAVEFORMS.get(command)
    if waveform is None:
        return

    has_amplitude = "amplitude_vpp" in arguments
    has_offset = "offset_v" in arguments
    has_high = "high_level_v" in arguments
    has_low = "low_level_v" in arguments
    if has_high != has_low:
        raise WorkerRequestValidationError(
            "invalid_arguments",
            f"{waveform} high_level_v and low_level_v must be provided together.",
        )
    if has_high and (has_amplitude or has_offset):
        raise WorkerRequestValidationError(
            "invalid_arguments",
            f"{waveform} high/low voltage cannot be combined with "
            "amplitude_vpp or offset_v.",
        )
    if has_high and (
        arguments["high_level_v"] is None or arguments["low_level_v"] is None
    ):
        raise WorkerRequestValidationError(
            "invalid_arguments",
            f"{waveform} high_level_v and low_level_v must be finite numbers.",
        )
    if has_amplitude and arguments["amplitude_vpp"] is None:
        raise WorkerRequestValidationError(
            "invalid_arguments",
            f"{waveform} amplitude_vpp must be a finite number.",
        )
    if has_offset and arguments["offset_v"] is None:
        raise WorkerRequestValidationError(
            "invalid_arguments",
            f"{waveform} offset_v must be a finite number.",
        )

    try:
        amplitude, offset = resolve_voltage_inputs(
            arguments.get("amplitude_vpp"),
            arguments.get("offset_v"),
            arguments.get("high_level_v"),
            arguments.get("low_level_v"),
            arguments["load"],
            waveform,
        )
    except WaveformParameterError as exc:
        raise WorkerRequestValidationError("invalid_arguments", str(exc)) from exc

    arguments["amplitude_vpp"] = amplitude
    arguments["offset_v"] = offset
    arguments.pop("high_level_v", None)
    arguments.pop("low_level_v", None)


def _validate_waveform_arguments(
    command: str,
    arguments: Mapping[str, object],
    model_id: object,
    *,
    channel: int,
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
                channel=channel,
            )
        elif command == "configure-sine-sweep":
            dry_run_sine_sweep(
                model_id,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
                channel=channel,
            )
        elif command == "configure-square-sweep":
            dry_run_square_sweep(
                model_id,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
                duty_cycle_percent=arguments["duty_cycle_percent"],
                channel=channel,
            )
        elif command == "configure-ramp-sweep":
            dry_run_ramp_sweep(
                model_id,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
                symmetry_percent=arguments["symmetry_percent"],
                channel=channel,
            )
        elif command == "configure-triangle-sweep":
            dry_run_triangle_sweep(
                model_id,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
                channel=channel,
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
                channel=channel,
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
                channel=channel,
            )
        elif command == "configure-triangle":
            dry_run_triangle(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                arguments["phase_deg"],
                channel=channel,
            )
        elif command == "configure-pulse":
            dry_run_pulse(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["pulse_width_s"],
                arguments["offset_v"],
                arguments.get("edge_time_s"),
                arguments["load"],
                arguments["phase_deg"],
                arguments.get("leading_edge_s"),
                arguments.get("trailing_edge_s"),
                channel=channel,
            )
        elif command == "configure-dc":
            dry_run_dc(
                model_id,
                arguments["voltage_v"],
                arguments["load"],
                channel=channel,
            )
        elif command == "configure-noise":
            dry_run_noise(
                model_id,
                arguments["amplitude_vpp"],
                arguments["bandwidth_hz"],
                arguments["offset_v"],
                arguments["load"],
                channel=channel,
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
                channel=channel,
            )
    except WaveformParameterError as exc:
        raise WorkerRequestValidationError("invalid_arguments", str(exc)) from exc


def _validate_registered_model_id(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or model_info_for_model_id(value) is None:
        raise WorkerRequestValidationError(
            "invalid_context",
            f"{field_name} must be an exact registered model ID.",
        )


def _unknown_fields(
    value: Mapping[object, object],
    allowed_fields: frozenset[str],
) -> list[object]:
    return [field for field in value if field not in allowed_fields]


def _format_fields(fields: list[object]) -> str:
    return ", ".join(repr(field) for field in fields)
