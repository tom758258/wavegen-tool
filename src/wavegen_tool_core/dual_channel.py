"""Explicit, contributor-only dual-channel operations using Core VISA safety."""

from __future__ import annotations

from collections.abc import Mapping
import math

from . import visa
from ._keysight_builtin import BUILTIN_ARBITRARY_PATHS
from .errors import (
    VisaCleanupError, VisaWriteError,
    WaveformParameterError, WaveformVerificationError, WavegenError,
)
from .identity import SUPPORT_POLICY_MODE_PRODUCT, SUPPORT_POLICY_MODE_VALIDATION

DUAL_CHANNEL_COMMANDS = frozenset({
    "phase-sync", "configure-frequency-coupling", "configure-voltage-coupling",
    "configure-tracking", "configure-combine", "update-coupled-frequency",
    "update-coupled-voltage", "configure-tracked-waveform", "dual-channel-status",
})
DUAL_CHANNEL_WRITE_COMMANDS = DUAL_CHANNEL_COMMANDS - {"dual-channel-status"}
_FEATURE = "Dual channel operations"
_TRACK = {"off": "OFF", "on": "ON", "invert": "INVerted"}
_STATIC = frozenset({"sine", "square", "ramp", "triangle", "pulse", "dc", "noise", "prbs"})
_FREQ = "FREQuency:COUPle"
_VOLT = "VOLTage:COUPle"


def is_dual_action(action: str, arguments: Mapping) -> bool:
    return action in DUAL_CHANNEL_COMMANDS or (
        action == "output" and arguments.get("channel") == "all"
    )


def uses_other_channel_modulation(arguments: Mapping) -> bool:
    return any(isinstance(arguments.get(key), Mapping) and
               arguments[key].get("source") in ("ch1", "ch2")
               for key in ("am", "fm", "pm", "pwm", "sum"))


def _fields(arguments: Mapping, required=(), optional=()) -> None:
    if not isinstance(arguments, Mapping):
        raise WaveformParameterError("Dual channel arguments must be an object.")
    if set(arguments) - set(required) - set(optional) or set(required) - set(arguments):
        raise WaveformParameterError("Missing or unknown dual channel argument.")


def _choice(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise WaveformParameterError(f"Invalid {name}.")
    return value


def _number(value, name):
    return visa._normalize_finite_number(value, name, waveform="Dual channel")


def _source(channel):
    return f"SOURce{channel}"


def _fmt(value):
    return visa._format_scpi_number(value)


def _tracked_plan(model, arguments):
    # Reuse the existing structured waveform validation and planning, not another
    # set of waveform parameter limits. No arbitrary memory operation is added.
    from .action_arguments import (
        MODULATION_FIELDS, canonicalize_voltage_arguments,
        configuration_options, validate_action_arguments,
    )
    waveform = _choice(arguments["waveform"], _STATIC, "tracked waveform")
    if not isinstance(arguments["arguments"], Mapping):
        raise WaveformParameterError("Tracked waveform arguments must be an object.")
    values = dict(arguments["arguments"])
    action = f"configure-{waveform}"
    validate_action_arguments(action, values)
    canonicalize_voltage_arguments(action, values)
    if any(isinstance(values.get(key), Mapping) and
           values[key].get("source", "internal") != "internal" for key in MODULATION_FIELDS):
        raise WaveformParameterError("Tracking cannot use other-channel modulation.")
    options = configuration_options(action, values)
    kwargs = {k: v for k, v in values.items()
              if k not in MODULATION_FIELDS | {"burst", "verify_readback"}}
    kwargs.update(options)
    return getattr(visa, f"dry_run_{waveform}")(model, **kwargs)


def plan_dual_channel(action: str, arguments: Mapping, model: str) -> dict:
    """Plan explicit joint operations; no VISA manager or state readback."""
    info, caps = visa._require_hardware_free_model(model, "dual channel")
    if action != "phase-sync" and caps.channel_count != 2:
        raise WaveformParameterError("This operation requires a two-channel model.")
    if not isinstance(arguments, Mapping):
        raise WaveformParameterError("Dual channel arguments must be an object.")
    args = dict(arguments)
    commands = []
    result = {}
    if action == "phase-sync":
        _fields(args)
        commands = ["PHASe:SYNChronize"]
    elif action == "dual-channel-status":
        _fields(args)
    elif action == "output":
        _fields(args, ("channel", "enabled"), ("confirm_output",))
        if args["channel"] != "all" or type(args["enabled"]) is not bool:
            raise WaveformParameterError("Group output requires channel all and boolean enabled.")
        if "confirm_output" in args and type(args["confirm_output"]) is not bool:
            raise WaveformParameterError("confirm_output must be boolean.")
        state = "on" if args["enabled"] else "off"
        commands = [f"OUTPut{ch} {state.upper()}" for ch in (1, 2)]
        result["requested_output_state"] = state
        result["channel"] = "all"
    elif action == "configure-frequency-coupling":
        _fields(args, ("state",), ("mode", "ratio", "offset_hz", "reference_channel"))
        state = _choice(args["state"], ("on", "off"), "coupling state")
        if state == "off":
            _fields(args, ("state",))
            commands = [f"SOURce1:{_FREQ}:STATe OFF"]
        else:
            mode = _choice(args.get("mode"), ("ratio", "offset"), "coupling mode")
            key = "ratio" if mode == "ratio" else "offset_hz"
            _fields(args, ("state", "mode", key), ("reference_channel",))
            ch = visa._validate_channel(args.get("reference_channel", 1), caps)
            value = _number(args[key], key)
            maximum = caps.max_sine_square_pulse_noise_frequency_hz
            if mode == "ratio" and not 0.001 <= value <= 1000:
                raise WaveformParameterError("Frequency ratio must be between 0.001 and 1000.")
            if mode == "offset" and abs(value) > maximum:
                raise WaveformParameterError("Frequency offset exceeds model limits.")
            setting = "RATio" if mode == "ratio" else "OFFSet"
            commands = [f"SOURce1:{_FREQ}:MODE {setting}",
                        f"{_source(ch)}:{_FREQ}:{setting} {_fmt(value)}",
                        f"{_source(ch)}:{_FREQ}:STATe ON"]
            result.update(coupling_mode=mode, reference_channel=ch, **{key: value})
        result["state"] = state
    elif action == "configure-voltage-coupling":
        _fields(args, ("state",))
        state = _choice(args["state"], ("on", "off"), "coupling state")
        commands = [f"SOURce1:{_VOLT}:STATe {state.upper()}"]
        result["state"] = state
    elif action == "configure-tracking":
        _fields(args, ("mode",), ("reference_channel",))
        mode = _choice(args["mode"], _TRACK, "tracking mode")
        ch = visa._validate_channel(args.get("reference_channel", 1), caps)
        commands = [f"{_source(ch)}:TRACk {_TRACK[mode]}"]
        result.update(tracking=mode, reference_channel=ch)
    elif action == "configure-combine":
        _fields(args, ("channel", "source"))
        ch = visa._validate_channel(args["channel"], caps)
        source = _choice(args["source"], ("none", f"ch{3-ch}"), "combine source")
        commands = [f"{_source(ch)}:COMBine:FEED {source.upper()}"]
        result.update(channel=ch, source=source)
    elif action == "update-coupled-frequency":
        _fields(args, ("frequency_hz",), ("channel",))
        ch = visa._validate_channel(args.get("channel", 1), caps)
        value = _number(args["frequency_hz"], "frequency")
        if not 1e-6 <= value <= caps.max_sine_square_pulse_noise_frequency_hz:
            raise WaveformParameterError("Frequency exceeds model limits.")
        commands = [f"{_source(ch)}:FREQuency {_fmt(value)}"]
        result.update(channel=ch, frequency_hz=value)
    elif action == "update-coupled-voltage":
        _fields(args, ("amplitude_vpp", "offset_v"), ("channel",))
        ch = visa._validate_channel(args.get("channel", 1), caps)
        amp, offset = (_number(args[k], k) for k in ("amplitude_vpp", "offset_v"))
        if not 0.001 <= amp <= 20 or abs(offset) + amp / 2 > 10:
            raise WaveformParameterError("Coupled voltage exceeds supported amplitude or offset limits.")
        commands = [f"{_source(ch)}:VOLTage:UNIT VPP",
                    f"{_source(ch)}:VOLTage:OFFSet 0",
                    f"{_source(ch)}:VOLTage {_fmt(amp)}",
                    f"{_source(ch)}:VOLTage:OFFSet {_fmt(offset)}"]
        result.update(channel=ch, amplitude_vpp=amp, offset_v=offset)
    elif action == "configure-tracked-waveform":
        _fields(args, ("waveform", "arguments"))
        plan = _tracked_plan(model, args)
        commands = list(plan.commands)
        result.update(channel=plan.channel, waveform=args["waveform"])
    else:
        raise WaveformParameterError("Unknown dual channel operation.")
    if action != "dual-channel-status" and action != "output":
        result["output_state"] = "off"
    return dict(action=action, model=info.canonical_model, canonical_model_id=model,
                channels=list(range(1, caps.channel_count + 1)), commands=commands,
                executed=False, **result)


def _query(session, command):
    try:
        return visa._parse_status_text(session.query(command), command).upper()
    except Exception as exc:
        raise WaveformVerificationError(f"Could not confirm {command}.") from exc


def _numeric(session, command):
    return visa._parse_status_number(_query(session, command), command)


def _state(session, command):
    response = _query(session, command)
    if response not in ("0", "1", "OFF", "ON"):
        raise WaveformVerificationError(f"Unknown state for {command}.")
    return response in ("1", "ON")


def _equal(actual, expected, label):
    if isinstance(expected, (float, int)) and not isinstance(expected, bool):
        matches = isinstance(actual, (float, int)) and math.isclose(actual, expected, rel_tol=1e-5, abs_tol=1e-9)
    else:
        matches = actual == expected
    if not matches:
        raise WaveformVerificationError(f"{label} readback did not match the request.")


def _error_free(session):
    entry = visa._parse_error_queue_entry(session.query("SYSTem:ERRor?"))
    if entry.code:
        raise WaveformVerificationError(f"Instrument reported error {entry.code}.")


def _outputs(session, channels=(1, 2)):
    return {str(ch): "on" if _state(session, f"OUTPut{ch}?") else "off" for ch in channels}


def _require_off(session, channels):
    if any(state != "off" for state in _outputs(session, channels).values()):
        raise WaveformVerificationError("All affected main outputs must be confirmed OFF.")


def _topology(session):
    frequency = _state(session, f"SOURce1:{_FREQ}:STATe?")
    voltage = _state(session, f"SOURce1:{_VOLT}:STATe?")
    tracking = [_query(session, f"SOURce{ch}:TRACk?") for ch in (1, 2)]
    if any(value not in {"OFF", "ON", "INV", "INVERTED"} for value in tracking):
        raise WaveformVerificationError("Unknown tracking state.")
    combine = [_query(session, f"SOURce{ch}:COMBine:FEED?") for ch in (1, 2)]
    if combine[0] not in {"NONE", "CH2"} or combine[1] not in {"NONE", "CH1"}:
        raise WaveformVerificationError("Unknown combine source.")
    return frequency, voltage, tracking, combine


def _modulation_source(session, channel, mode):
    value = visa._canonical_readback_enum(_query(session, f"SOURce{channel}:{mode}:SOURce?"))
    if value not in {"INTERNAL", "EXTERNAL", f"CH{3-channel}"}:
        raise WaveformVerificationError("Unknown or self-referencing modulation source.")
    return value


def _active_other_source(session, channel):
    sources = [
        _modulation_source(session, channel, mode)
        for mode in ("AM", "FM", "PM", "PWM", "SUM")
        if _state(session, f"SOURce{channel}:{mode}:STATe?")
    ]
    if len(sources) > 1:
        raise WaveformVerificationError("Conflicting modulation modes.")
    return f"CH{3-channel}" in sources


def _check_topology(session, topology):
    frequency, voltage, tracking, combine = topology
    tracking = [mode.replace("INVERTED", "INV") for mode in tracking]
    if tracking[0] != tracking[1] or (
        tracking[0] != "OFF" and (
            frequency or voltage or combine != ["NONE", "NONE"]
            or _state(session, "RATE:COUPle:STATe?")
        )
    ) or all(source != "NONE" for source in combine):
        raise WaveformVerificationError("Inconsistent or conflicting dual channel topology.")
    other_sources = [_active_other_source(session, ch) for ch in (1, 2)]
    if all(other_sources) or (tracking[0] != "OFF" and any(other_sources)):
        raise WaveformVerificationError("Conflicting other-channel modulation routes.")


def _function_name(value):
    names = {"SIN": "sine", "SINE": "sine", "SINUSOID": "sine",
             "SQU": "square", "SQUARE": "square", "RAMP": "ramp",
             "TRI": "triangle", "TRIANGLE": "triangle", "PULS": "pulse",
             "PULSE": "pulse", "NOIS": "noise", "NOISE": "noise",
             "DC": "dc", "PRBS": "prbs", "ARB": "arbitrary"}
    if value not in names:
        raise WaveformVerificationError("Unknown waveform function.")
    return names[value]


def _polarity(session, channel):
    value = visa._canonical_readback_enum(_query(session, f"OUTPut{channel}:POLarity?"))
    if value not in {"NORMAL", "INVERTED"}:
        raise WaveformVerificationError("Unknown output polarity.")
    return "NORM" if value == "NORMAL" else "INV"


def _check_tracking_memory(session, context):
    from .arbitrary import _catalog
    caps = visa._capabilities_for_identity(context.identity)
    for ch in (1, 2):
        source = _source(ch)
        if _function_name(_query(session, source + ":FUNCtion?")) == "arbitrary":
            raise WaveformParameterError("Tracking Arbitrary is outside the supported scope.")
        if visa._canonical_readback_enum(_query(session, source + ":FREQuency:MODE?")) == "LIST":
            raise WaveformParameterError("Tracking an active frequency list is outside the supported scope.")
        if caps.max_arbitrary_sample_rate_hz is not None:
            # Clearing volatile memory reloads the default built-in waveform.
            catalog = _catalog(session, source)
            if catalog and tuple(name.casefold() for name in catalog) != (
                BUILTIN_ARBITRARY_PATHS["EXP_RISE"].casefold(),
            ):
                raise WaveformParameterError("Tracking requires only the default built-in waveform in volatile memory.")


def _frequency_limit(session, ch, value):
    prefix = f"SOURce{ch}:FREQuency?"
    if _function_name(_query(session, f"SOURce{ch}:FUNCtion?")) in {"arbitrary", "dc", "noise", "prbs"}:
        raise WaveformParameterError("Selected waveform does not support frequency coupling.")
    low, high = (_numeric(session, f"{prefix} {bound}") for bound in ("MINimum", "MAXimum"))
    if not low <= value <= high:
        raise WaveformParameterError(f"Coupled frequency exceeds Channel {ch} limits.")


def _check_coupled_frequencies(session, values, context):
    caps = visa._capabilities_for_identity(context.identity)
    for ch, frequency in values.items():
        _frequency_limit(session, ch, frequency)
        prefix = _source(ch)
        carrier = _function_name(_query(session, prefix + ":FUNCtion?"))
        if visa._canonical_readback_enum(_query(session, prefix + ":FREQuency:MODE?")) != "CW":
            raise WaveformParameterError("Frequency coupling updates require CW frequency mode.")
        duty_frequency = frequency
        if _state(session, prefix + ":FM:STATe?"):
            source = _modulation_source(session, ch, "FM")
            if source == "EXTERNAL":
                raise WaveformParameterError("Cannot validate externally modulated coupled frequency.")
            config = visa.FMConfig(
                modulation_frequency_hz=(
                    _numeric(session, prefix + ":FM:INTernal:FREQuency?") if source == "INTERNAL" else None
                ),
                deviation_hz=_numeric(session, prefix + ":FM:DEViation?"),
                source="internal" if source == "INTERNAL" else source.lower(),
            )
            visa._prepare_fm(carrier, frequency, config, capabilities=caps)
            duty_frequency = frequency + config.deviation_hz
        if _state(session, prefix + ":PM:STATe?"):
            source = _modulation_source(session, ch, "PM")
            if source == "EXTERNAL":
                raise WaveformParameterError("Cannot validate externally modulated coupled frequency.")
            modulation_frequency = (
                _numeric(session, prefix + ":PM:INTernal:FREQuency?")
                if source == "INTERNAL" else visa._other_channel_pm_source_frequency(
                    session, context, 3-ch, frequency_hz=values[3-ch]
                )
            )
            if modulation_frequency <= 0 or frequency <= 20 * modulation_frequency:
                raise WaveformParameterError("PM carrier frequency must be greater than 20 times the modulation frequency.")
        if carrier == "square":
            if _state(session, prefix + ":FSKey:STATe?"):
                duty_frequency = max(duty_frequency, _numeric(session, prefix + ":FSKey:FREQuency?"))
            visa._prepare_square(
                frequency, 0.1, 0, _numeric(session, prefix + ":FUNCtion:SQUare:DCYCle?"),
                50, 0, capabilities=caps, duty_cycle_validation_frequency_hz=duty_frequency,
            )
        if carrier == "pulse":
            pulse = prefix + ":FUNCtion:PULSe:"
            width = _numeric(session, pulse + "WIDTh?")
            leading = _numeric(session, pulse + "TRANsition:LEADing?")
            trailing = _numeric(session, pulse + "TRANsition:TRAiling?")
            visa._prepare_pulse(frequency, 0.1, width, 0, None, 50, 0,
                                leading, trailing, capabilities=caps)
            if _state(session, prefix + ":PWM:STATe?"):
                source = _modulation_source(session, ch, "PWM")
                if source == "EXTERNAL":
                    raise WaveformParameterError("Cannot validate externally modulated coupled frequency.")
                config = visa.PWMConfig(
                    modulation_frequency_hz=(
                        _numeric(session, prefix + ":PWM:INTernal:FREQuency?") if source == "INTERNAL" else None
                    ),
                    deviation_s=_numeric(session, prefix + ":PWM:DEViation?"),
                    source="internal" if source == "INTERNAL" else source.lower(),
                )
                visa._prepare_pwm(frequency, width, leading, trailing, config, capabilities=caps)
        if _state(session, prefix + ":BURSt:STATe?"):
            mode = visa._canonical_readback_enum(_query(session, prefix + ":BURSt:MODE?"))
            if mode == "TRIGGERED":
                trigger = visa._canonical_readback_enum(_query(session, f"TRIGger{ch}:SOURce?")).lower()
                count = _numeric(session, prefix + ":BURSt:NCYCles?")
                if not count.is_integer():
                    raise WaveformVerificationError("Unknown Burst count.")
                config = visa.CountedBurstConfig(
                    count=int(count), trigger_source=trigger,
                    period_s=_numeric(session, prefix + ":BURSt:INTernal:PERiod?") if trigger == "immediate" else None,
                    trigger_timer_s=_numeric(session, f"TRIGger{ch}:TIMer?") if trigger == "timer" else None,
                )
                visa._prepare_counted_burst(carrier, frequency, config)
            elif mode != "GATED":
                raise WaveformVerificationError("Unknown Burst mode.")


def _levels(session, ch):
    source = _source(ch)
    offset = _numeric(session, f"{source}:VOLTage:OFFSet?")
    if _query(session, f"{source}:FUNCtion?") == "DC":
        return offset, offset
    low = _numeric(session, f"{source}:VOLTage:LOW?")
    high = _numeric(session, f"{source}:VOLTage:HIGH?")
    if high < low:
        raise WaveformVerificationError("Invalid voltage range readback.")
    return low, high


def _check_levels(session, ch, low, high, load=None):
    source = _source(ch)
    if load is None:
        load = visa._parse_status_load(session.query(f"OUTPut{ch}:LOAD?"))
    peak = 10.0 if load == "high-z" else 10 * float(load) / (float(load) + 50)
    if low < -peak - 1e-9 or high > peak + 1e-9:
        raise WaveformParameterError(f"Voltage exceeds Channel {ch} output rating.")
    if _state(session, f"{source}:VOLTage:LIMit:STATe?"):
        minimum = _numeric(session, f"{source}:VOLTage:LIMit:LOW?")
        maximum = _numeric(session, f"{source}:VOLTage:LIMit:HIGH?")
        if low < minimum or high > maximum:
            raise WaveformParameterError(f"Voltage exceeds Channel {ch} programmed limits.")


def _signal_levels(session, ch, levels=None):
    low, high = _levels(session, ch) if levels is None else levels
    offset = (low + high) / 2
    prefix = _source(ch)
    factor = 1.0
    if _state(session, f"{prefix}:AM:STATe?"):
        depth = _numeric(session, f"{prefix}:AM:DEPTh?") / 100
        factor = depth if _state(session, f"{prefix}:AM:DSSC?") else 1 + depth
    if _state(session, f"{prefix}:SUM:STATe?"):
        factor = 1 + _numeric(session, f"{prefix}:SUM:AMPLitude?") / 100
    half = (high - low) / 2 * factor
    return offset - half, offset + half


def _check_combine(session, receiver, source):
    for ch in (receiver, source):
        if _query(session, f"SOURce{ch}:FUNCtion?") == "DC":
            raise WaveformParameterError("DC cannot be combined.")
    low, high = _signal_levels(session, receiver)
    other_low, other_high = _signal_levels(session, source)
    offset = _numeric(session, f"SOURce{source}:VOLTage:OFFSet?")
    _check_levels(session, receiver, low + other_low - offset, high + other_high - offset)


def _status(session):
    frequency, voltage, tracking, combine = _topology(session)
    angle_unit = _query(session, "UNIT:ANGLe?")
    if angle_unit not in {"DEG", "DEGREE", "RAD", "RADIAN"}:
        raise WaveformVerificationError("Unknown phase angle unit.")
    phase_factor = 180 / math.pi if angle_unit.startswith("RAD") else 1
    mode = _query(session, f"SOURce1:{_FREQ}:MODE?")
    if mode not in {"RAT", "RATIO", "OFFS", "OFFSET"}:
        raise WaveformVerificationError("Unknown frequency coupling mode.")
    channels = {}
    for ch in (1, 2):
        prefix = _source(ch)
        function = _query(session, prefix + ":FUNCtion?")
        _function_name(function)
        low, high = _levels(session, ch)
        channels[str(ch)] = {
            "output_state": _outputs(session, (ch,))[str(ch)],
            "function": function,
            "frequency_hz": (None if function in {"DC", "NOIS", "NOISE", "PRBS", "ARB"}
                             else _numeric(session, prefix + ":FREQuency?")),
            "amplitude_vpp": high - low,
            "offset_v": _numeric(session, prefix + ":VOLTage:OFFSet?"),
            "phase_deg": (None if function in {"DC", "NOIS", "NOISE", "PRBS", "ARB"}
                          else phase_factor * _numeric(session, prefix + ":PHASe?")),
            "load": visa._parse_status_load(session.query(f"OUTPut{ch}:LOAD?")),
            "amplitude_unit": visa._parse_status_unit(session.query(prefix + ":VOLTage:UNIT?")),
            "autorange_enabled": _state(session, prefix + ":VOLTage:RANGe:AUTO?"),
            "polarity": _polarity(session, ch),
            "combine_source": combine[ch-1].lower(),
            "tracking": tracking[ch-1].replace("INVERTED", "INV").lower(),
        }
    return {
        "frequency_coupling": {"enabled": frequency, "mode": "ratio" if mode.startswith("RAT") else "offset",
                               "ratio": _numeric(session, f"SOURce1:{_FREQ}:RATio?"),
                               "offset_hz": _numeric(session, f"SOURce1:{_FREQ}:OFFSet?")},
        "voltage_coupling": voltage, "channel_status": channels,
    }


def _tracking_settings(session, context, channel, function):
    suffixes = {
        "square": ("SQUare:DCYCle",),
        "ramp": ("RAMP:SYMMetry",),
        "pulse": ("PULSe:WIDTh", "PULSe:TRANsition:LEADing", "PULSe:TRANsition:TRAiling"),
        "noise": ("NOISe:BANDwidth",),
        "prbs": ("PRBS:BRATe", "PRBS:DATA", "PRBS:TRANsition:BOTH"),
    }.get(_function_name(function), ())
    fields = {suffix: _query(session, f"SOURce{channel}:FUNCtion:{suffix}?")
              for suffix in suffixes}
    _, mode, advanced = visa._read_advanced_mode_session(session, context, channel)
    return fields, mode, advanced


def _verify_tracking_settings(actual, expected):
    fields, mode, advanced = actual
    wanted_fields, wanted_mode, wanted_advanced = expected
    _equal(fields.keys(), wanted_fields.keys(), "Tracked waveform fields")
    for suffix, value in wanted_fields.items():
        if not visa._configuration_readback_matches(suffix, value, fields[suffix]):
            raise WaveformVerificationError(f"Tracked {suffix} readback did not match.")
    _equal(mode, wanted_mode, "Tracked advanced mode")
    _equal(advanced.keys(), wanted_advanced.keys(), "Tracked advanced settings")
    for field, value in wanted_advanced.items():
        _equal(advanced[field], value, "Tracked " + field)


def _verify_tracking_pair(session, context, result, inverted):
    first, second = (result["channel_status"][str(ch)] for ch in (1, 2))
    _equal(_function_name(first["function"]), _function_name(second["function"]), "Tracked function")
    for field in ("frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load", "amplitude_unit", "autorange_enabled"):
        _equal(first[field], second[field], "Tracked " + field)
    if any(state["polarity"] not in {"NORM", "INV"} for state in (first, second)):
        raise WaveformVerificationError("Unknown tracked output polarity.")
    if (first["polarity"] == second["polarity"]) == inverted:
        raise WaveformVerificationError("Tracked output polarity did not match mode.")
    settings = {
        ch: _tracking_settings(session, context, ch, result["channel_status"][str(ch)]["function"])
        for ch in (1, 2)
    }
    _verify_tracking_settings(settings[2], settings[1])
    return settings


def _off_all(session):
    failures = []
    interruption = None
    for ch in (1, 2):
        try:
            session.write(f"OUTPut{ch} OFF")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                interruption = exc
            failures.append(f"Channel {ch} OFF write failed.")
    for ch in (1, 2):
        try:
            _equal(_outputs(session, (ch,))[str(ch)], "off", f"Channel {ch} OFF")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                interruption = exc
            failures.append(f"Channel {ch} OFF was not confirmed.")
    if interruption is not None:
        interruption.cleanup_errors = tuple(failures)
        raise interruption
    if failures:
        raise VisaCleanupError("Group output OFF failed.").attach_cleanup_errors(tuple(failures))


def _apply(session, plan, arguments, context):
    action = plan["action"]
    channels = plan["channels"]
    commands = plan["commands"]
    if action == "dual-channel-status":
        return _status(session)
    if action == "output":
        if plan["requested_output_state"] == "off":
            _off_all(session)
            _error_free(session)
            actual = _outputs(session)
            _equal(actual, {"1": "off", "2": "off"}, "Group output OFF")
            return {"output_states": actual, "output_state": "off"}
        _require_off(session, channels)
        topology = _topology(session)
        _check_topology(session, topology)
        frequency, voltage, tracking, combined = topology
        if frequency or voltage or tracking[0] != "OFF":
            status = _status(session)
            first, second = (status["channel_status"][str(ch)] for ch in (1, 2))
            if frequency:
                values = {1: first["frequency_hz"], 2: second["frequency_hz"]}
                if any(value is None for value in values.values()):
                    raise WaveformVerificationError("Unsupported coupled frequency state.")
                settings = status["frequency_coupling"]
                expected = (values[1] * settings["ratio"] if settings["mode"] == "ratio"
                            else values[1] + settings["offset_hz"])
                _equal(values[2], expected, "Coupled frequency relation")
                _check_coupled_frequencies(session, values, context)
            if voltage:
                for field in ("amplitude_vpp", "offset_v", "load", "amplitude_unit", "autorange_enabled"):
                    _equal(first[field], second[field], "Coupled " + field)
            if tracking[0] != "OFF":
                _check_tracking_memory(session, context)
                _verify_tracking_pair(session, context, status, tracking[0] in {"INV", "INVERTED"})
        for ch in (1, 2):
            if not frequency and _state(session, f"SOURce{ch}:PM:STATe?"):
                if _modulation_source(session, ch, "PM") == f"CH{3-ch}":
                    visa._verify_other_channel_pm_frequency(session, context, ch)
            _check_levels(session, ch, *_signal_levels(session, ch))
            if combined[ch-1] != "NONE":
                _check_combine(session, ch, 3-ch)
        try:
            for command in commands:
                session.write(command)
            actual = _outputs(session)
            _equal(actual, {"1": "on", "2": "on"}, "Group output")
            _error_free(session)
        except (Exception, KeyboardInterrupt) as exc:
            error = exc if isinstance(exc, WavegenError) else VisaWriteError("Group output ON failed.")
            try:
                _off_all(session)
            except WavegenError as cleanup:
                error.attach_cleanup_errors(cleanup.cleanup_errors)
            except KeyboardInterrupt as cleanup:
                cleanup.cleanup_errors = (str(error), *getattr(cleanup, "cleanup_errors", ()))
                raise
            if isinstance(exc, KeyboardInterrupt):
                exc.cleanup_errors = error.cleanup_errors
                exc.args = (str(error) + " " + "; ".join(error.cleanup_errors),)
                raise
            raise error from (None if error is exc else exc)
        return {"output_states": actual, "output_state": "on"}
    _require_off(session, channels)
    if action == "phase-sync":
        # There is no instrument query that proves physical phase alignment.
        phases = {ch: _numeric(session, f"SOURce{ch}:PHASe?") for ch in channels
                  if _query(session, f"SOURce{ch}:FUNCtion?") not in {"DC", "NOIS", "NOISE", "PRBS", "ARB"}}
        session.write(commands[0])
        _error_free(session)
        for ch, expected in phases.items():
            _equal(_numeric(session, f"SOURce{ch}:PHASe?"), expected, "Phase setting")
        _require_off(session, channels)
        return {"output_state": "off", "phase_settings_preserved": True}

    frequency, voltage, tracking, combine = _topology(session)
    tracked = any(mode != "OFF" for mode in tracking)
    expected_frequency = {}
    tracking_reference = None
    if action == "configure-frequency-coupling" and plan["state"] == "on":
        if tracked or frequency:
            raise WaveformParameterError("Disable tracking or existing frequency coupling first.")
        ref = plan["reference_channel"]
        original = _numeric(session, f"SOURce{ref}:FREQuency?")
        other = original * plan["ratio"] if plan["coupling_mode"] == "ratio" else original + plan["offset_hz"]
        expected_frequency = {ref: original, 3-ref: other}
    elif action == "update-coupled-frequency":
        if not frequency or tracked:
            raise WaveformParameterError("Frequency coupling must be active without tracking.")
        ch = plan["channel"]
        val = plan["frequency_hz"]
        mode = _query(session, f"SOURce1:{_FREQ}:MODE?")
        if mode in {"RAT", "RATIO"}:
            other = val * _numeric(session, f"SOURce{ch}:{_FREQ}:RATio?")
        elif mode in {"OFFS", "OFFSET"}:
            other = val + _numeric(session, f"SOURce{ch}:{_FREQ}:OFFSet?")
        else:
            raise WaveformVerificationError("Unknown frequency coupling mode.")
        expected_frequency = {ch: val, 3-ch: other}
    elif action == "configure-voltage-coupling" and plan["state"] == "on":
        if tracked:
            raise WaveformParameterError("Disable tracking first.")
        # Coupling can copy load and voltage settings; validate either starting setup
        # against both channels' programmed limits before allowing the native action.
        for ref in (1, 2):
            load = visa._parse_status_load(session.query(f"OUTPut{ref}:LOAD?"))
            for ch in (1, 2):
                _check_levels(session, ch, *_signal_levels(session, ch, _levels(session, ref)), load=load)
            if combine != ["NONE", "NONE"]:
                receiver = 1 if combine[0] != "NONE" else 2
                low, high = _levels(session, ref)
                lo1, hi1 = _signal_levels(session, receiver, (low, high))
                lo2, hi2 = _signal_levels(session, 3-receiver, (low, high))
                center = (low + high) / 2
                _check_levels(session, receiver, lo1+lo2-center, hi1+hi2-center, load=load)
    elif action == "update-coupled-voltage":
        if not voltage or tracked:
            raise WaveformParameterError("Voltage coupling must be active without tracking.")
        amp, offset = plan["amplitude_vpp"], plan["offset_v"]
        old_low, old_high = _levels(session, plan["channel"])
        old_half = (old_high - old_low) / 2
        for center, half in ((0, old_half), (0, amp/2), (offset, amp/2)):
            for ch in (1, 2):
                load = visa._parse_status_load(session.query(f"OUTPut{ch}:LOAD?"))
                if half == amp/2:
                    visa._validate_vpp_levels(amp, center, load, "Coupled voltage")
                _check_levels(session, ch, *_signal_levels(session, ch, (center-half, center+half)))
            if combine != ["NONE", "NONE"]:
                receiver = 1 if combine[0] != "NONE" else 2
                lo1, hi1 = _signal_levels(session, receiver, (center-half, center+half))
                lo2, hi2 = _signal_levels(session, 3-receiver, (center-half, center+half))
                _check_levels(session, receiver, lo1+lo2-center, hi1+hi2-center)
    elif action == "configure-tracking" and plan["tracking"] != "off":
        if frequency or voltage or combine != ["NONE", "NONE"] or _state(session, "RATE:COUPle:STATe?"):
            raise WaveformParameterError("Explicitly disable conflicting coupling and combine first.")
        ref = plan["reference_channel"]
        _check_tracking_memory(session, context)
        if _active_other_source(session, ref):
            raise WaveformParameterError("Tracking cannot copy other-channel modulation.")
        if _state(session, "OUTPut:SYNC?"):
            raise WaveformParameterError("Sync output must be OFF before changing its source by tracking.")
        load = visa._parse_status_load(session.query(f"OUTPut{ref}:LOAD?"))
        _check_levels(session, 3-ref, *_signal_levels(session, ref), load=load)
        tracking_reference = _status(session)["channel_status"][str(ref)]
        tracking_reference_settings = _tracking_settings(session, context, ref, tracking_reference["function"])
    elif action == "configure-combine" and plan["source"] != "none":
        ch = plan["channel"]
        if tracked or combine[2-ch] != "NONE":
            raise WaveformParameterError("Tracking and another combine receiver must be OFF.")
        _check_combine(session, ch, 3-ch)
    elif action == "configure-tracked-waveform":
        if not tracked or frequency or voltage or combine != ["NONE", "NONE"]:
            raise WaveformParameterError("Tracking must be active without conflicting coupling.")
        _check_tracking_memory(session, context)
        _check_topology(session, (frequency, voltage, tracking, combine))
        wave = _tracked_plan(context.identity.canonical_model_id, arguments)
        load = wave.load
        if hasattr(wave, "amplitude_vpp"):
            amp, offset = wave.amplitude_vpp, wave.offset_v
            am, summation = getattr(wave, "am", None), getattr(wave, "sum", None)
            if am is not None:
                amp *= am.depth_percent / 100 + (0 if am.am_type == "dssc" else 1)
            if summation is not None:
                amp *= 1 + summation.amplitude_percent / 100
            for ch in (1, 2):
                _check_levels(session, ch, offset-amp/2, offset+amp/2, load=load)
        elif hasattr(wave, "voltage_v"):
            for ch in (1, 2):
                _check_levels(session, ch, wave.voltage_v, wave.voltage_v, load=load)

    if expected_frequency:
        _check_topology(session, (frequency, voltage, tracking, combine))
        _check_coupled_frequencies(session, expected_frequency, context)
    for command in commands:
        if action == "configure-tracked-waveform" and ":FUNCtion:PULSe:TRANsition:" in command:
            header, _, value = command.partition(" ")
            if value != "MINimum":
                edge = "BOTH" if header.endswith(":BOTH") else ("LEADING" if header.endswith(":LEADing") else "TRAILING")
                for ch in (1, 2):
                    query = header.replace(f"SOURce{plan['channel']}", f"SOURce{ch}")
                    if edge == "BOTH":
                        query = query.removesuffix(":BOTH")
                    maximum = _numeric(session, query + "? MAXimum")
                    visa._validate_pulse_edge_maximum(float(value), maximum, edge, context)
        session.write(command)
    _error_free(session)
    _require_off(session, channels)
    result = _status(session)
    if action == "configure-frequency-coupling":
        _equal(result["frequency_coupling"]["enabled"], plan["state"] == "on", "Frequency coupling")
        if plan["state"] == "on":
            _equal(result["frequency_coupling"]["mode"], plan["coupling_mode"], "Coupling mode")
            key = "RATio" if plan["coupling_mode"] == "ratio" else "OFFSet"
            value = plan["ratio"] if plan["coupling_mode"] == "ratio" else plan["offset_hz"]
            _equal(_numeric(session, f"SOURce{plan['reference_channel']}:{_FREQ}:{key}?"), value, "Coupling setting")
    elif action == "configure-voltage-coupling":
        _equal(result["voltage_coupling"], plan["state"] == "on", "Voltage coupling")
        if plan["state"] == "on":
            for field in ("amplitude_vpp", "offset_v", "load", "amplitude_unit", "autorange_enabled"):
                _equal(result["channel_status"]["1"][field], result["channel_status"]["2"][field], field)
    elif action == "configure-tracking":
        actual = [_query(session, f"SOURce{ch}:TRACk?") for ch in (1, 2)]
        wanted = {"off": "OFF", "on": "ON", "invert": "INV"}[plan["tracking"]]
        _equal([v.replace("INVERTED", "INV") for v in actual], [wanted, wanted], "Tracking")
        if wanted != "OFF":
            settings = _verify_tracking_pair(session, context, result, wanted == "INV")
            _verify_tracking_settings(settings[plan["reference_channel"]], tracking_reference_settings)
            reference = result["channel_status"][str(plan["reference_channel"])]
            _equal(_function_name(reference["function"]), _function_name(tracking_reference["function"]),
                   "Tracking reference function")
            for field in ("frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load",
                          "amplitude_unit", "autorange_enabled", "polarity"):
                _equal(reference[field], tracking_reference[field], "Tracking reference " + field)
    elif action == "configure-combine":
        _equal(result["channel_status"][str(plan["channel"])]["combine_source"], plan["source"], "Combine")
    elif action == "update-coupled-frequency":
        _equal(result["frequency_coupling"]["enabled"], True, "Frequency coupling")
    elif action == "update-coupled-voltage":
        _equal(result["voltage_coupling"], True, "Voltage coupling")
        for ch in (1, 2):
            state = result["channel_status"][str(ch)]
            _equal(state["amplitude_vpp"], plan["amplitude_vpp"], "Coupled amplitude")
            _equal(state["offset_v"], plan["offset_v"], "Coupled offset")
            _equal(state["amplitude_unit"], "VPP", "Coupled amplitude unit")
    elif action == "configure-tracked-waveform":
        _verify_tracking_pair(session, context, result, tracking[0] in {"INV", "INVERTED"})
        actual_tracking = [result["channel_status"][str(ch)]["tracking"].upper().replace("INVERTED", "INV") for ch in (1, 2)]
        _equal(actual_tracking, [mode.replace("INVERTED", "INV") for mode in tracking], "Tracking state")
        wave = _tracked_plan(context.identity.canonical_model_id, arguments)
        for ch in (1, 2):
            actual = result["channel_status"][str(ch)]
            _equal(_function_name(actual["function"]), plan["waveform"], f"CH{ch} tracked function")
            for field in ("frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load"):
                expected = getattr(wave, field, None)
                if expected is not None:
                    _equal(actual[field], expected, f"CH{ch} tracked {field}")
            if hasattr(wave, "voltage_v"):
                _equal(actual["offset_v"], wave.voltage_v, f"CH{ch} tracked DC voltage")
        # Check waveform-specific programmed values using the existing readback
        # comparison, independent of the tracking state flag.
        selected = plan["channel"]
        fields = {}
        for command in commands:
            header, sep, value = command.partition(" ")
            if sep and header.startswith(f"SOURce{selected}:FUNCtion:") and value not in {"MINimum", "MAXimum"}:
                if header.endswith((":DCYCle", ":SYMMetry", ":WIDTh", ":BANDwidth", ":BRATe", ":DATA", ":TRANsition:BOTH", ":TRANsition:LEADing", ":TRANsition:TRAiling")):
                    if header.endswith(":PULSe:TRANsition:BOTH"):
                        header = header.removesuffix(":BOTH")
                    fields[header] = value
        original = tuple(command.replace(f"SOURce{selected}:", "SOURce1:").replace(
            f"TRIGger{selected}:", "TRIGger1:"
        ) for command in commands)
        for ch in (1, 2):
            visa._verify_configuration_readback(session, context,
                {key.replace(f"SOURce{selected}:", f"SOURce{ch}:"): value for key, value in fields.items()},
                label="Tracked waveform")
            if any(command.endswith(":STATe ON") for command in original):
                visa._verify_advanced_mode_configuration(session, context, original, selected_channel=ch)
            for command in original:
                if command.endswith(":STATe OFF"):
                    header = command.split(" ", 1)[0]
                    if header + " ON" not in original:
                        _equal(_state(session, header.replace("SOURce1:", f"SOURce{ch}:") + "?"),
                               False, "Tracked inactive mode")
    for ch, value in expected_frequency.items():
        _equal(result["channel_status"][str(ch)]["frequency_hz"], value, f"Channel {ch} frequency")
    return dict(result, output_state="off", readback_verified=True)


def run_dual_channel(action: str, arguments: Mapping, *, model: str | None = None,
                     resource: str | None = None, backend: str | None = None,
                     support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
                     expected_model_id: str | None = None,
                     resource_manager_factory=None) -> dict:
    """Use the existing exact identity and contributor policy for joint operations."""
    if (model is None) == (resource is None):
        raise WaveformParameterError("Specify a planning model or an explicit resource.")
    if model is not None:
        return plan_dual_channel(action, arguments, model)
    if support_policy_mode == SUPPORT_POLICY_MODE_VALIDATION and expected_model_id is None:
        raise WaveformParameterError("Dual channel live validation requires an exact model.")
    options = dict(support_policy_mode=support_policy_mode, expected_model_id=expected_model_id,
                   resource_manager_factory=resource_manager_factory, validation_only_feature=_FEATURE)
    if expected_model_id is not None:
        plan_dual_channel(action, arguments, expected_model_id)
    attempted_on = False

    def operation(session, context):
        nonlocal attempted_on
        plan = plan_dual_channel(action, arguments, context.identity.canonical_model_id)
        attempted_on = action == "output" and plan.get("requested_output_state") == "on"
        result = _apply(session, plan, arguments, context)
        return dict(plan, executed=True, **result)

    try:
        context, result = visa._run_on_supported_instrument(resource, backend, operation, **options)
    except (VisaCleanupError, KeyboardInterrupt) as error:
        if attempted_on:
            # A successful ON followed by session teardown failure still needs a
            # bounded best-effort OFF against the same explicitly selected resource.
            try:
                visa._run_on_supported_instrument(resource, backend,
                    lambda session, _: _off_all(session), **options)
            except (WavegenError, KeyboardInterrupt) as cleanup:
                diagnostics = (str(cleanup) or "Group OFF cleanup interrupted.",
                               *getattr(cleanup, "cleanup_errors", ()))
                if isinstance(error, WavegenError):
                    error.attach_cleanup_errors(error.cleanup_errors + diagnostics)
                else:
                    error.cleanup_errors = (*getattr(error, "cleanup_errors", ()), *diagnostics)
                    error.args = (f"{error}; group OFF cleanup failed: {diagnostics[0]}",)
        raise
    return dict(result, resource=context.resource, backend=context.backend, transport=context.transport,
                identity={"manufacturer": context.identity.manufacturer,
                          "model": context.identity.model,
                          "canonical_model_id": context.identity.canonical_model_id})
