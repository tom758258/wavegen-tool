"""Shared arguments for structured instrument actions."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from .arbitrary import ARBITRARY_COMMANDS
from .errors import WaveformParameterError
from .dual_channel import DUAL_CHANNEL_COMMANDS, is_dual_action, plan_dual_channel
from .visa import (
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
    resolve_voltage_inputs,
)


class ActionArgumentError(WaveformParameterError):
    """Invalid structured action arguments."""

    code = "invalid_arguments"


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
CONFIGURE_COMMANDS = (
    _SWEEP_COMMANDS
    | _LIST_SWEEP_COMMANDS
    | frozenset(_STATIC_ADVANCED_FIELDS)
    | {"configure-dc", "configure-noise", "configure-output", "configure-sync"}
)
ACTION_COMMANDS = (
    CONFIGURE_COMMANDS
    | ARBITRARY_COMMANDS
    | DUAL_CHANNEL_COMMANDS
    | {
        "identify",
        "status",
        "read-errors",
        "readback-advanced",
        "output",
        "sync-output",
        "trigger",
    }
)
DRY_RUN_COMMANDS = CONFIGURE_COMMANDS | ARBITRARY_COMMANDS | DUAL_CHANNEL_COMMANDS
_CHANNEL_COMMANDS = ((CONFIGURE_COMMANDS | ARBITRARY_COMMANDS) - {"configure-sync"}) | {
    "status",
    "readback-advanced",
    "output",
}

_MODULATION_CONFIGS = {
    "am": AMConfig,
    "fm": FMConfig,
    "pm": PMConfig,
    "fsk": FSKConfig,
    "bpsk": BPSKConfig,
    "pwm": PWMConfig,
    "sum": SumConfig,
}

MODULATION_FIELDS = frozenset(_MODULATION_CONFIGS)

_ARGUMENT_FIELDS: dict[str, frozenset[str]] = {
    "identify": frozenset(),
    "trigger": frozenset(),
    "configure-output": frozenset(OutputConfig.__dataclass_fields__),
    "configure-sync": frozenset(SyncConfig.__dataclass_fields__),
    "sync-output": frozenset({"state", "confirm_output"}),
    "upload-arbitrary": frozenset({"name", "path"}),
    "configure-arbitrary": frozenset(
        {
            "name",
            "sample_rate_hz",
            "amplitude_vpp",
            "offset_v",
            "load",
            "load_builtin",
        }
    ),
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
    "configure-pulse": frozenset({"frequency_hz", "pulse_width_s"}),
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
        _ARGUMENT_FIELDS[_command]
        - {
            "start_frequency_hz",
            "stop_frequency_hz",
            "spacing",
            "sweep_time_s",
            "hold_time_s",
            "return_time_s",
            "trigger_timer_s",
        }
    ) | {"frequencies_hz", "dwell_s"}
    _ARGUMENT_FIELDS[_command] |= {"trigger_slope", "trigger_delay_s"}
    _REQUIRED_ARGUMENT_FIELDS[_list_command] = frozenset({"frequencies_hz"})
    _DEFAULT_ARGUMENTS[_list_command] = {
        field: value
        for field, value in _DEFAULT_ARGUMENTS[_command].items()
        if field not in {"hold_time_s", "return_time_s"}
    }

for _command, _fields in _STATIC_ADVANCED_FIELDS.items():
    _ARGUMENT_FIELDS[_command] |= _fields | {"verify_readback"}


def validate_action_arguments(
    command: str, arguments: dict[str, object], *, require_absolute_path: bool = False
) -> None:
    """Validate and supply defaults for structured instrument command arguments."""
    if is_dual_action(command, arguments):
        try:
            # This validates request shape; callers subsequently validate the actual
            # registered planning model or detected hardware identity.
            plan_dual_channel(command, arguments, "keysight-33512b")
        except WaveformParameterError as exc:
            raise ActionArgumentError(str(exc)) from exc
        return
    allowed_fields = _ARGUMENT_FIELDS[command]
    if command in _CHANNEL_COMMANDS:
        allowed_fields = allowed_fields | {"channel"}
    unknown_fields = _unknown_fields(arguments, allowed_fields)
    if unknown_fields:
        raise ActionArgumentError(
            f"Unknown argument field(s) for {command}: {_format_fields(unknown_fields)}.",
        )

    missing_fields = [
        field
        for field in _REQUIRED_ARGUMENT_FIELDS.get(command, frozenset())
        if field not in arguments
    ]
    if missing_fields:
        raise ActionArgumentError(
            f"Missing required argument field(s) for {command}: {_format_fields(missing_fields)}.",
        )

    for field, default in _DEFAULT_ARGUMENTS.get(command, {}).items():
        arguments.setdefault(field, default)

    if command in _CHANNEL_COMMANDS:
        channel = arguments.setdefault("channel", 1)
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
            raise ActionArgumentError("channel must be a positive integer.")

    if command == "configure-pulse":
        has_shared = "edge_time_s" in arguments
        has_leading = "leading_edge_s" in arguments
        has_trailing = "trailing_edge_s" in arguments
        if has_leading != has_trailing:
            raise ActionArgumentError(
                "Pulse leading_edge_s and trailing_edge_s must be provided together.",
            )
        if has_shared and (has_leading or has_trailing):
            raise ActionArgumentError(
                "Pulse edge_time_s cannot be combined with leading_edge_s or trailing_edge_s.",
            )
        if not has_shared and not has_leading:
            arguments["edge_time_s"] = 1e-8

    if command in _STATIC_ADVANCED_FIELDS or command in _SWEEP_COMMANDS | _LIST_SWEEP_COMMANDS:
        if "verify_readback" in arguments and type(arguments["verify_readback"]) is not bool:
            raise ActionArgumentError("verify_readback must be a boolean.")
        options = configuration_options(command, arguments)
        if command in _STATIC_ADVANCED_FIELDS and arguments.get("verify_readback") and not options:
            raise ActionArgumentError("verify_readback requires an Advanced Mode option.")
    for field in ("trigger_source", "trigger_slope"):
        if field in arguments and not isinstance(arguments[field], str):
            raise ActionArgumentError(f"{field} must be a string.")
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
        raise ActionArgumentError("Omit trigger_timer_s for external trigger source.")
    for field in ("voltage_limits_enabled", "autorange_enabled"):
        if field in arguments and type(arguments[field]) is not bool:
            raise ActionArgumentError(f"{field} must be a boolean.")
    if command in _LIST_SWEEP_COMMANDS and not isinstance(arguments["frequencies_hz"], list):
        raise ActionArgumentError("frequencies_hz must be an array.")
    if command in ARBITRARY_COMMANDS:
        _validate_arbitrary_fields(command, arguments, require_absolute_path=require_absolute_path)
    if command == "configure-sync":
        try:
            SyncConfig(**arguments)
        except WaveformParameterError as exc:
            raise ActionArgumentError(str(exc)) from exc

    if command == "read-errors":
        max_reads = arguments["max_reads"]
        if (
            isinstance(max_reads, bool)
            or not isinstance(max_reads, int)
            or not 1 <= max_reads <= 100
        ):
            raise ActionArgumentError("read-errors max_reads must be an integer from 1 to 100.")
    elif command in {"output", "sync-output"}:
        if command == "output" and type(arguments["enabled"]) is not bool:
            raise ActionArgumentError("output enabled must be a boolean.")
        if command == "sync-output" and arguments["state"] not in ("on", "off"):
            raise ActionArgumentError("sync-output state must be on or off.")
        if "confirm_output" in arguments and type(arguments["confirm_output"]) is not bool:
            raise ActionArgumentError(f"{command} confirm_output must be a boolean.")


def _validate_arbitrary_fields(
    command: str, arguments: Mapping[str, object], *, require_absolute_path: bool
) -> None:
    if "name" in arguments and not isinstance(arguments["name"], str):
        raise ActionArgumentError("name must be a string.")
    if command == "upload-arbitrary":
        path = arguments["path"]
        if require_absolute_path and (
            not isinstance(path, str) or "\x00" in path or not Path(path).is_absolute()
        ):
            raise ActionArgumentError("path must be a Worker-host-local absolute path.")
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ActionArgumentError("path must be a non-empty file path.")
    elif command == "configure-arbitrary":
        if "load_builtin" in arguments and type(arguments["load_builtin"]) is not bool:
            raise ActionArgumentError("load_builtin must be a boolean.")
        for field in ("sample_rate_hz", "amplitude_vpp", "offset_v"):
            if field in arguments and type(arguments[field]) not in (int, float):
                raise ActionArgumentError(f"{field} must be a number.")
        if "load" in arguments and type(arguments["load"]) not in (str, int, float):
            raise ActionArgumentError("load must be a string or number.")
    elif command == "configure-arbitrary-sequence" and not isinstance(
        arguments["document"], Mapping
    ):
        raise ActionArgumentError("document must be a JSON object.")
    elif command == "clear-arbitrary-memory" and arguments["confirm_clear"] is not True:
        raise ActionArgumentError("clear-arbitrary-memory requires confirm_clear=true.")


def _validate_trigger_delay_type(arguments: Mapping[str, object]) -> None:
    if "trigger_delay_s" in arguments and type(arguments["trigger_delay_s"]) not in (
        int,
        float,
        Decimal,
    ):
        raise ActionArgumentError("trigger_delay_s must be a number.")


def configuration_options(
    command: str, arguments: Mapping[str, object], *, readback: bool = False
) -> dict[str, object]:
    """Translate explicitly supplied structured options to existing Core parameters."""
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
            raise ActionArgumentError(f"Invalid {field} object for {command}.") from exc
    for field in (
        "trigger_source",
        "trigger_timer_s",
        "trigger_slope",
        "trigger_delay_s",
        "dwell_s",
    ):
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
    **{
        command: command.removeprefix("configure-").replace("-", " ").capitalize()
        for command in _LIST_SWEEP_COMMANDS
    },
    "configure-square": "Square",
    "configure-ramp": "Ramp",
    "configure-triangle": "Triangle",
    "configure-pulse": "Pulse",
    "configure-noise": "Noise",
    "configure-prbs": "PRBS",
}


def canonicalize_voltage_arguments(
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
        raise ActionArgumentError(
            f"{waveform} high_level_v and low_level_v must be provided together.",
        )
    if has_high and (has_amplitude or has_offset):
        raise ActionArgumentError(
            f"{waveform} high/low voltage cannot be combined with amplitude_vpp or offset_v.",
        )
    if has_high and (arguments["high_level_v"] is None or arguments["low_level_v"] is None):
        raise ActionArgumentError(
            f"{waveform} high_level_v and low_level_v must be finite numbers.",
        )
    if has_amplitude and arguments["amplitude_vpp"] is None:
        raise ActionArgumentError(
            f"{waveform} amplitude_vpp must be a finite number.",
        )
    if has_offset and arguments["offset_v"] is None:
        raise ActionArgumentError(
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
        raise ActionArgumentError(str(exc)) from exc

    arguments["amplitude_vpp"] = amplitude
    arguments["offset_v"] = offset
    arguments.pop("high_level_v", None)
    arguments.pop("low_level_v", None)


def _unknown_fields(
    value: Mapping[object, object],
    allowed_fields: frozenset[str],
) -> list[object]:
    return [field for field in value if field not in allowed_fields]


def _format_fields(fields: list[object]) -> str:
    return ", ".join(repr(field) for field in fields)
