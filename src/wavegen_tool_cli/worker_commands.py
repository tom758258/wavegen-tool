"""Pure Wavegen Worker command request admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from wavegen_tool_core import resolve_voltage_inputs
from wavegen_tool_core.arbitrary import ARBITRARY_COMMANDS
from wavegen_tool_core.errors import WaveformParameterError
from wavegen_tool_core.identity import model_info_for_model_id
from wavegen_tool_cli.worker_protocol import WORKER_SCHEMA_VERSION
from wavegen_tool_core.visa import (
    AMConfig,
    BPSKConfig,
    CountedBurstConfig,
    FMConfig,
    FSKConfig,
    GatedBurstConfig,
    OutputConfig,
    PMConfig,
    PWMConfig,
    SumConfig,
    SyncConfig,
    dry_run_dc,
    dry_run_output,
    dry_run_sync,
    dry_run_ramp_list_sweep,
    dry_run_sine_list_sweep,
    dry_run_square_list_sweep,
    dry_run_triangle_list_sweep,
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
    "SUPPORTED_WORKER_COMMANDS",
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
_SWEEP_COMMANDS = frozenset(
    f"configure-{waveform}-sweep" for waveform in ("sine", "square", "ramp", "triangle")
)
_LIST_SWEEP_COMMANDS = frozenset(
    command.replace("-sweep", "-list-sweep") for command in _SWEEP_COMMANDS
)
_STATIC_ADVANCED_FIELDS = {
    "configure-sine": {"am", "fm", "pm", "fsk", "bpsk", "sum", "burst"},
    "configure-square": {"am", "fm", "pm", "fsk", "bpsk", "sum", "burst"},
    "configure-ramp": {"am", "fm", "pm", "fsk", "bpsk", "sum", "burst"},
    "configure-triangle": {"am", "fm", "pm", "fsk", "bpsk", "sum", "burst"},
    "configure-pulse": {"am", "pwm", "sum", "burst"},
    "configure-prbs": {"burst"},
}
_CONFIGURE_COMMANDS = (
    _SWEEP_COMMANDS | _LIST_SWEEP_COMMANDS | frozenset(_STATIC_ADVANCED_FIELDS)
    | {"configure-dc", "configure-noise", "configure-output", "configure-sync"}
)
SUPPORTED_WORKER_COMMANDS = _CONFIGURE_COMMANDS | ARBITRARY_COMMANDS | {
    "identify", "status", "read-errors", "readback-advanced", "output", "sync-output", "trigger",
}
_DRY_RUN_COMMANDS = _CONFIGURE_COMMANDS | ARBITRARY_COMMANDS
_CHANNEL_COMMANDS = (_DRY_RUN_COMMANDS - {"configure-sync"}) | {"status", "readback-advanced", "output"}
_LIVE_WRITE_COMMANDS = _DRY_RUN_COMMANDS - {"list-arbitrary-memory"} | {"trigger"}

_MODULATION_CONFIGS = {
    "am": AMConfig, "fm": FMConfig, "pm": PMConfig, "fsk": FSKConfig,
    "bpsk": BPSKConfig, "pwm": PWMConfig, "sum": SumConfig,
}

_ARGUMENT_FIELDS: dict[str, frozenset[str]] = {
    "identify": frozenset(),
    "trigger": frozenset(),
    "configure-output": frozenset(OutputConfig.__dataclass_fields__),
    "configure-sync": frozenset(SyncConfig.__dataclass_fields__),
    "sync-output": frozenset({"state", "confirm_output"}),
    "upload-arbitrary": frozenset({"name", "path"}),
    "configure-arbitrary": frozenset({
        "name", "sample_rate_hz", "amplitude_vpp", "offset_v", "load", "load_builtin",
    }),
    "configure-arbitrary-sequence": frozenset({"document"}),
    "list-arbitrary-memory": frozenset(),
    "clear-arbitrary-memory": frozenset({"confirm_clear"}),
    "status": frozenset(),
    "readback-advanced": frozenset(),
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
    "configure-sync": _ARGUMENT_FIELDS["configure-sync"],
    "sync-output": frozenset({"state"}),
    "upload-arbitrary": frozenset({"name", "path"}),
    "configure-arbitrary": frozenset({"name", "sample_rate_hz", "amplitude_vpp"}),
    "configure-arbitrary-sequence": frozenset({"document"}),
    "clear-arbitrary-memory": frozenset({"confirm_clear"}),
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


for _command in _SWEEP_COMMANDS:
    _ARGUMENT_FIELDS[_command] |= {"trigger_source", "trigger_timer_s", "verify_readback"}
    _list_command = _command.replace("-sweep", "-list-sweep")
    _ARGUMENT_FIELDS[_list_command] = (
        (_ARGUMENT_FIELDS[_command] - {
            "start_frequency_hz", "stop_frequency_hz", "spacing", "sweep_time_s",
            "hold_time_s", "return_time_s", "trigger_timer_s",
        }) | {"frequencies_hz", "dwell_s"}
    )
    _ARGUMENT_FIELDS[_command] |= {"trigger_slope", "trigger_delay_s"}
    _REQUIRED_ARGUMENT_FIELDS[_list_command] = frozenset({"frequencies_hz"})
    _DEFAULT_ARGUMENTS[_list_command] = {
        field: value for field, value in _DEFAULT_ARGUMENTS[_command].items()
        if field not in {"hold_time_s", "return_time_s"}
    }

for _command, _fields in _STATIC_ADVANCED_FIELDS.items():
    _ARGUMENT_FIELDS[_command] |= _fields | {"verify_readback"}


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

    if command not in SUPPORTED_WORKER_COMMANDS:
        raise WorkerRequestValidationError(
            "unsupported_command", f"Unsupported Worker command: {command}."
        )
    if request_mode == "dry_run" and command not in _DRY_RUN_COMMANDS:
        raise WorkerRequestValidationError(
            "unsupported_command",
            "dry_run context only supports configure-* and Arbitrary commands.",
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
                channel=arguments.get("channel", 1),
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

    if command in _STATIC_ADVANCED_FIELDS or command in _SWEEP_COMMANDS | _LIST_SWEEP_COMMANDS:
        if "verify_readback" in arguments and type(arguments["verify_readback"]) is not bool:
            raise WorkerRequestValidationError("invalid_arguments", "verify_readback must be a boolean.")
        options = configuration_options(command, arguments)
        if command in _STATIC_ADVANCED_FIELDS and arguments.get("verify_readback") and not options:
            raise WorkerRequestValidationError(
                "invalid_arguments", "verify_readback requires an Advanced Mode option."
            )
    for field in ("trigger_source", "trigger_slope"):
        if field in arguments and not isinstance(arguments[field], str):
            raise WorkerRequestValidationError("invalid_arguments", f"{field} must be a string.")
    _validate_trigger_delay_type(arguments)
    burst = arguments.get("burst")
    if isinstance(burst, Mapping):
        _validate_trigger_delay_type(burst)
    trigger_source = arguments.get("trigger_source")
    if (
        command in _SWEEP_COMMANDS
        and "trigger_timer_s" in arguments
        and arguments["trigger_timer_s"] is None
        and isinstance(trigger_source, str)
        and trigger_source.strip().casefold() == "external"
    ):
        raise WorkerRequestValidationError(
            "invalid_arguments", "Omit trigger_timer_s for external trigger source."
        )
    for field in ("voltage_limits_enabled", "autorange_enabled"):
        if field in arguments and type(arguments[field]) is not bool:
            raise WorkerRequestValidationError("invalid_arguments", f"{field} must be a boolean.")
    if command in _LIST_SWEEP_COMMANDS and not isinstance(arguments["frequencies_hz"], list):
        raise WorkerRequestValidationError("invalid_arguments", "frequencies_hz must be an array.")
    if command in ARBITRARY_COMMANDS:
        _validate_arbitrary_fields(command, arguments)
    if command == "configure-sync":
        try:
            SyncConfig(**arguments)
        except WaveformParameterError as exc:
            raise WorkerRequestValidationError("invalid_arguments", str(exc)) from exc

    if command == "read-errors":
        max_reads = arguments["max_reads"]
        if isinstance(max_reads, bool) or not isinstance(max_reads, int) or not 1 <= max_reads <= 100:
            raise WorkerRequestValidationError(
                "invalid_arguments", "read-errors max_reads must be an integer from 1 to 100."
            )
    elif command in {"output", "sync-output"}:
        if command == "output" and type(arguments["enabled"]) is not bool:
            raise WorkerRequestValidationError(
                "invalid_arguments", "output enabled must be a boolean."
            )
        if command == "sync-output" and arguments["state"] not in ("on", "off"):
            raise WorkerRequestValidationError(
                "invalid_arguments", "sync-output state must be on or off."
            )
        if "confirm_output" in arguments and type(arguments["confirm_output"]) is not bool:
            raise WorkerRequestValidationError(
                "invalid_arguments", f"{command} confirm_output must be a boolean."
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

    if command in _LIVE_WRITE_COMMANDS and not allow_output_writes:
        raise WorkerRequestValidationError(
            "live_write_not_allowed",
            f"{command} requires allow_output_writes=True in live context.",
        )

    enabling_output = (
        (command == "output" and arguments["enabled"])
        or (command == "sync-output" and arguments["state"] == "on")
    )
    if not enabling_output:
        return
    if not allow_output_writes:
        raise WorkerRequestValidationError(
            "live_write_not_allowed",
            f"live {command} enable requires allow_output_writes=True.",
        )
    if arguments.get("confirm_output") is not True:
        raise WorkerRequestValidationError(
            "output_confirmation_required",
            f"live {command} enable requires confirm_output=True.",
        )


def _validate_arbitrary_fields(command: str, arguments: Mapping[str, object]) -> None:
    if "name" in arguments and not isinstance(arguments["name"], str):
        raise WorkerRequestValidationError("invalid_arguments", "name must be a string.")
    if command == "upload-arbitrary":
        path = arguments["path"]
        if not isinstance(path, str) or "\x00" in path or not Path(path).is_absolute():
            raise WorkerRequestValidationError(
                "invalid_arguments", "path must be a Worker-host-local absolute path."
            )
    elif command == "configure-arbitrary":
        if "load_builtin" in arguments and type(arguments["load_builtin"]) is not bool:
            raise WorkerRequestValidationError("invalid_arguments", "load_builtin must be a boolean.")
        for field in ("sample_rate_hz", "amplitude_vpp", "offset_v"):
            if field in arguments and type(arguments[field]) not in (int, float):
                raise WorkerRequestValidationError("invalid_arguments", f"{field} must be a number.")
        if "load" in arguments and type(arguments["load"]) not in (str, int, float):
            raise WorkerRequestValidationError("invalid_arguments", "load must be a string or number.")
    elif command == "configure-arbitrary-sequence" and not isinstance(arguments["document"], Mapping):
        raise WorkerRequestValidationError("invalid_arguments", "document must be a JSON object.")
    elif command == "clear-arbitrary-memory" and arguments["confirm_clear"] is not True:
        raise WorkerRequestValidationError(
            "invalid_arguments", "clear-arbitrary-memory requires confirm_clear=true."
        )


def _validate_trigger_delay_type(arguments: Mapping[str, object]) -> None:
    if "trigger_delay_s" in arguments and type(arguments["trigger_delay_s"]) not in (
        int, float, Decimal,
    ):
        raise WorkerRequestValidationError("invalid_arguments", "trigger_delay_s must be a number.")


def configuration_options(
    command: str, arguments: Mapping[str, object], *, readback: bool = False
) -> dict[str, object]:
    """Translate explicitly supplied Worker options to existing Core parameters."""
    options = {}
    for field in _STATIC_ADVANCED_FIELDS.get(command, ()):
        if field not in arguments:
            continue
        group = arguments[field]
        try:
            if not isinstance(group, Mapping):
                raise TypeError
            values = dict(group)
            if field == "burst":
                mode = values.pop("mode", None)
                if mode == "counted":
                    options[field] = CountedBurstConfig(**values)
                elif mode == "gated":
                    options[field] = GatedBurstConfig(**values)
                else:
                    raise TypeError
            else:
                options[field] = _MODULATION_CONFIGS[field](**values)
        except TypeError as exc:
            raise WorkerRequestValidationError(
                "invalid_arguments", f"Invalid {field} object for {command}."
            ) from exc
    for field in ("trigger_source", "trigger_timer_s", "trigger_slope", "trigger_delay_s", "dwell_s"):
        if field in arguments:
            options[field] = arguments[field]
    if readback and "verify_readback" in arguments:
        options["verify_readback"] = arguments["verify_readback"]
    return options


_VOLTAGE_WAVEFORMS = {
    "configure-sine": "Sine",
    "configure-sine-sweep": "Sine sweep",
    "configure-square-sweep": "Square sweep",
    "configure-ramp-sweep": "Ramp sweep",
    "configure-triangle-sweep": "Triangle sweep",
    **{command: command.removeprefix("configure-").replace("-", " ").capitalize()
       for command in _LIST_SWEEP_COMMANDS},
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
        if command == "configure-sync":
            dry_run_sync(model_id, SyncConfig(**arguments))
        elif command == "configure-output":
            config = OutputConfig(**{key: value for key, value in arguments.items() if key != "channel"})
            dry_run_output(model_id, config, channel=channel)
        elif command == "configure-sine":
            dry_run_sine(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                arguments["phase_deg"],
                channel=channel,
                **configuration_options(command, arguments),
            )
        else:
            operation = {
                "configure-sine-sweep": dry_run_sine_sweep,
                "configure-square-sweep": dry_run_square_sweep,
                "configure-ramp-sweep": dry_run_ramp_sweep,
                "configure-triangle-sweep": dry_run_triangle_sweep,
                "configure-sine-list-sweep": dry_run_sine_list_sweep,
                "configure-square-list-sweep": dry_run_square_list_sweep,
                "configure-ramp-list-sweep": dry_run_ramp_list_sweep,
                "configure-triangle-list-sweep": dry_run_triangle_list_sweep,
                "configure-square": dry_run_square,
                "configure-ramp": dry_run_ramp,
                "configure-triangle": dry_run_triangle,
                "configure-pulse": dry_run_pulse,
                "configure-dc": dry_run_dc,
                "configure-noise": dry_run_noise,
                "configure-prbs": dry_run_prbs,
            }[command]
            options = configuration_options(command, arguments)
            values = {key: value for key, value in arguments.items()
                      if key not in options and key not in {"verify_readback", *_MODULATION_CONFIGS, "burst"}}
            operation(model_id, **values, **options)
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
