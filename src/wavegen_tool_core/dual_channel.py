"""Explicit, contributor-only dual-channel operations using Core VISA safety."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
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
_RATIO_MIN, _RATIO_MAX = 0.001, 1000


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
            if mode == "ratio" and not _RATIO_MIN <= value <= _RATIO_MAX:
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
    return frequency, voltage, [value.replace("INVERTED", "INV") for value in tracking], combine


def _modulation_source(session, channel, mode):
    value = visa._canonical_readback_enum(_query(session, f"SOURce{channel}:{mode}:SOURce?"))
    allowed = {"INTERNAL", "EXTERNAL"}
    if mode not in {"FSKey", "BPSK"}:
        allowed.add(f"CH{3-channel}")
    if value not in allowed:
        raise WaveformVerificationError("Unknown or self-referencing modulation source.")
    return value


def _active_other_source(session, channel):
    def choice(header, allowed):
        value = visa._canonical_readback_enum(_query(session, header + "?"))
        if value not in allowed:
            raise WaveformVerificationError(f"Unknown state for {header}.")
        return value.lower()

    mode = visa._select_active_advanced_mode(choice, _source(channel) + ":")
    if mode in {"FSKey", "BPSK"}:
        choice(f"SOURce{channel}:{mode}:SOURce", frozenset({"INTERNAL", "EXTERNAL"}))
    return mode in {"AM", "FM", "PM", "PWM", "SUM"} and (
        _modulation_source(session, channel, mode) == f"CH{3-channel}"
    )


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
        if caps.max_arbitrary_sample_rate_hz is not None:
            # Clearing volatile memory reloads the default built-in waveform.
            catalog = _catalog(session, source)
            if catalog and tuple(name.casefold() for name in catalog) != (
                BUILTIN_ARBITRARY_PATHS["EXP_RISE"].casefold(),
            ):
                raise WaveformParameterError("Tracking requires only the default built-in waveform in volatile memory.")


def _frequency_limit(session, ch, value, caps):
    prefix = f"SOURce{ch}:FREQuency?"
    carrier = _function_name(_query(session, f"SOURce{ch}:FUNCtion?"))
    if carrier in {"arbitrary", "dc", "noise", "prbs"}:
        raise WaveformParameterError("Selected waveform does not support frequency coupling.")
    maximum = (visa.RAMP_TRIANGLE_MAX_FREQUENCY_HZ if carrier in {"ramp", "triangle"}
               else caps.max_sine_square_pulse_noise_frequency_hz)
    low, high = (_numeric(session, f"{prefix} {bound}") for bound in ("MINimum", "MAXimum"))
    if not 1e-6 <= value <= maximum or not low <= value <= high:
        raise WaveformParameterError(f"Frequency exceeds Channel {ch} limits.")


def _coupled_modulation_shape(session, prefix, mode, source):
    if source != "INTERNAL":
        return None
    function = visa._canonical_readback_enum(_query(session, f"{prefix}:{mode}:INTernal:FUNCtion?"))
    shape = visa._MODULATION_SHAPE_READBACK.get(function)
    if shape is None:
        raise WaveformVerificationError("Unknown or unsupported internal modulation shape.")
    return shape


def _checked_trigger_delay(ch, delay, *, label):
    if not 0 <= delay <= 1000:
        raise WaveformVerificationError(f"Invalid {label} trigger delay readback.")
    # Match the existing 1 ns readback tolerance and canonical 4 ns grid.
    rounded = round(delay * 250_000_000) * 4 / 1e9
    if not visa._configuration_readback_matches(
        f"TRIGger{ch}:DELay", str(rounded), str(delay),
    ):
        raise WaveformVerificationError(f"Invalid {label} trigger delay readback.")
    return rounded


def _check_coupled_frequencies(session, values, context, *, current=False):
    """Validate periodic rates; current settings need not be in coupling's CW mode."""
    caps = visa._capabilities_for_identity(context.identity)
    for ch, frequency in values.items():
        prefix = _source(ch)
        carrier = _function_name(_query(session, prefix + ":FUNCtion?"))
        mode = visa._canonical_readback_enum(_query(session, prefix + ":FREQuency:MODE?"))
        if mode != "CW":
            if not current:
                raise WaveformParameterError("Frequency coupling updates require CW frequency mode.")
            _, active, settings = visa._read_advanced_mode_session(session, context, ch)
            if active == "SWEEP":
                points = (settings["start_frequency_hz"], settings["stop_frequency_hz"])
                _, sweep_time, hold_time, return_time, _ = visa._prepare_sweep_timing(
                    settings["spacing"], settings["sweep_time_s"],
                    settings["hold_time_s"], settings["return_time_s"], waveform="Group output",
                )
                visa._prepare_sweep_trigger(
                    settings["trigger_source"], settings.get("trigger_timer_s"),
                    settings.get("trigger_slope"),
                    _checked_trigger_delay(ch, settings["trigger_delay_s"], label="Sweep"),
                    waveform="Group output", minimum_timer_s=sweep_time + hold_time + return_time,
                )
            elif active == "LIST":
                points = visa._normalize_frequency_list(settings["frequencies_hz"], waveform="Group output")
                if settings["trigger_source"] == "immediate":
                    visa._normalize_list_dwell(settings["dwell_s"], waveform="Group output")
                elif settings["trigger_source"] == "timer":
                    visa._prepare_trigger(
                        "timer", settings["trigger_timer_s"], waveform="Group output",
                    )
            else:
                raise WaveformVerificationError("Unknown current frequency mode.")
            frequency = max(points)
            # The extremes bound every list point without a VISA round trip
            # for each of up to 128 entries.
            for point in {min(points), frequency}:
                _frequency_limit(session, ch, point, caps)
        else:
            if current:
                frequency = _numeric(session, prefix + ":FREQuency?")
            _frequency_limit(session, ch, frequency, caps)
        duty_frequency = frequency
        if _state(session, prefix + ":FM:STATe?"):
            source = _modulation_source(session, ch, "FM")
            if source == "EXTERNAL":
                if not current:
                    raise WaveformParameterError("Cannot validate externally modulated coupled frequency.")
                visa._validate_frequency_modulation_carrier(carrier, "FM")
                deviation = _numeric(session, prefix + ":FM:DEViation?")
                visa._validate_fm_deviation(carrier, frequency, deviation, caps)
                duty_frequency = frequency + deviation
            else:
                config = visa.FMConfig(
                    modulation_frequency_hz=(
                        _numeric(session, prefix + ":FM:INTernal:FREQuency?") if source == "INTERNAL" else None
                    ),
                    deviation_hz=_numeric(session, prefix + ":FM:DEViation?"),
                    shape=_coupled_modulation_shape(session, prefix, "FM", source),
                    source="internal" if source == "INTERNAL" else source.lower(),
                )
                visa._prepare_fm(carrier, frequency, config, capabilities=caps)
                duty_frequency = frequency + config.deviation_hz
        if _state(session, prefix + ":PM:STATe?"):
            source = _modulation_source(session, ch, "PM")
            if source == "EXTERNAL":
                if not current:
                    raise WaveformParameterError("Cannot validate externally modulated coupled frequency.")
                visa._validate_frequency_modulation_carrier(carrier, "PM")
                visa._validate_pm_deviation(_numeric(session, prefix + ":PM:DEViation?"))
            else:
                config = visa.PMConfig(
                    modulation_frequency_hz=(
                        _numeric(session, prefix + ":PM:INTernal:FREQuency?") if source == "INTERNAL" else None
                    ),
                    deviation_deg=_numeric(session, prefix + ":PM:DEViation?"),
                    shape=_coupled_modulation_shape(session, prefix, "PM", source),
                    source="internal" if source == "INTERNAL" else source.lower(),
                )
                visa._prepare_pm(carrier, frequency, config, capabilities=caps)
                if source != "INTERNAL":
                    modulation_frequency = visa._other_channel_pm_source_frequency(
                        session, context, 3-ch, frequency_hz=values.get(3-ch)
                    )
                    if frequency <= 20 * modulation_frequency:
                        raise WaveformParameterError("PM carrier frequency must be greater than 20 times the modulation frequency.")
        if _state(session, prefix + ":FSKey:STATe?"):
            hop = _numeric(session, prefix + ":FSKey:FREQuency?")
            if _modulation_source(session, ch, "FSKey") == "INTERNAL":
                visa._prepare_fsk(
                    carrier, visa.FSKConfig(hop_frequency_hz=hop,
                                           rate_hz=_numeric(session, prefix + ":FSKey:INTernal:RATE?")),
                    capabilities=caps,
                )
            else:
                # External FSK still uses the programmed hop, not the internal rate.
                visa._validate_frequency_modulation_carrier(carrier, "FSK")
                visa._validate_fsk_hop_frequency(carrier, hop, caps)
            duty_frequency = max(duty_frequency, hop)
        if _state(session, prefix + ":BPSK:STATe?"):
            phase = _numeric(session, prefix + ":BPSK:PHASe?")
            if _modulation_source(session, ch, "BPSK") == "INTERNAL":
                visa._prepare_bpsk(
                    carrier, visa.BPSKConfig(
                        phase_shift_deg=phase,
                        rate_hz=_numeric(session, prefix + ":BPSK:INTernal:RATE?"),
                    ),
                )
            else:
                # The external trigger selects the programmed alternate phase.
                visa._validate_frequency_modulation_carrier(carrier, "BPSK")
                visa._validate_bpsk_phase_shift(phase)
        if carrier == "square":
            visa._prepare_square(
                frequency, 0.1, 0, _numeric(session, prefix + ":FUNCtion:SQUare:DCYCle?"),
                50, 0, capabilities=caps, duty_cycle_validation_frequency_hz=duty_frequency,
            )
        pwm_enabled = _state(session, prefix + ":PWM:STATe?")
        if pwm_enabled and carrier != "pulse":
            raise WaveformParameterError("PWM requires a Pulse carrier.")
        if carrier == "pulse":
            pulse = prefix + ":FUNCtion:PULSe:"
            width = _numeric(session, pulse + "WIDTh?")
            leading = _numeric(session, pulse + "TRANsition:LEADing?")
            trailing = _numeric(session, pulse + "TRANsition:TRAiling?")
            visa._prepare_pulse(frequency, 0.1, width, 0, None, 50, 0,
                                leading, trailing, capabilities=caps)
            if pwm_enabled:
                source = _modulation_source(session, ch, "PWM")
                if source == "EXTERNAL":
                    if not current:
                        raise WaveformParameterError("Cannot validate externally modulated coupled frequency.")
                    visa._validate_pwm_deviation(
                        frequency, width, leading, trailing,
                        _numeric(session, prefix + ":PWM:DEViation?"),
                    )
                else:
                    config = visa.PWMConfig(
                        modulation_frequency_hz=(
                            _numeric(session, prefix + ":PWM:INTernal:FREQuency?") if source == "INTERNAL" else None
                        ),
                        deviation_s=_numeric(session, prefix + ":PWM:DEViation?"),
                        shape=_coupled_modulation_shape(session, prefix, "PWM", source),
                        source="internal" if source == "INTERNAL" else source.lower(),
                    )
                    visa._prepare_pwm(frequency, width, leading, trailing, config, capabilities=caps)
        _check_burst_settings(session, context, ch, carrier, frequency)


def _check_burst_settings(session, context, ch, carrier, rate):
    """Validate active Burst readback without changing the current angle unit."""
    if not _state(session, _source(ch) + ":BURSt:STATe?"):
        return
    _, mode, settings = visa._read_advanced_mode_session(session, context, ch)
    if mode != "BURST" or carrier == "dc":
        raise WaveformVerificationError("Invalid active Burst configuration.")
    # Noise can use gated Burst, but its start phase has no effect.
    phase = 0.0
    if carrier != "noise":
        unit = _query(session, "UNIT:ANGLe?")
        phase = settings["phase_deg"]
        if unit in {"RAD", "RADIAN"}:
            phase = math.degrees(phase)
        elif unit not in {"DEG", "DEGREE"}:
            raise WaveformVerificationError("Unknown phase angle unit.")
        # Convert a numeric readback to the programmed 0.1-degree grid,
        # allowing only floating-point noise (including radians conversion).
        rounded_phase = round(phase, 1)
        if not math.isclose(phase, rounded_phase, rel_tol=0.0, abs_tol=1e-8):
            raise WaveformVerificationError("Invalid Burst start phase readback.")
        phase = rounded_phase
    if settings["burst_mode"] == "gated":
        config = visa.GatedBurstConfig(
            polarity=settings["gate_polarity"], start_phase_deg=phase,
        )
    else:
        if carrier == "noise":
            raise WaveformParameterError("Noise only supports gated Burst.")
        rounded_delay = _checked_trigger_delay(ch, settings["trigger_delay_s"], label="Burst")
        config = visa.CountedBurstConfig(
            count=settings["count"], trigger_source=settings["trigger_source"],
            period_s=settings.get("period_s"), trigger_timer_s=settings.get("trigger_timer_s"),
            trigger_slope=settings.get("trigger_slope"),
            start_phase_deg=phase, trigger_delay_s=rounded_delay,
        )
    visa._prepare_counted_burst(carrier, rate, config)


def _check_current_frequency_settings(session, context, ch):
    """Check present rates before joint ON or copying a tracking reference.

    Keep non-periodic waveforms out of frequency-coupling's CW-only rules.
    Native Arbitrary playback and external modulation are not reconfigured here.
    """
    prefix = _source(ch)
    carrier = _function_name(_query(session, prefix + ":FUNCtion?"))
    if carrier in {"arbitrary", "dc", "noise", "prbs"}:
        # Native PRBS supports FM/PM but not FSK/BPSK. DC and Noise
        # support none of these modes. Keep native Arbitrary handling unchanged.
        if carrier in {"dc", "noise", "prbs"}:
            modes = ("FSKey", "BPSK")
            if carrier != "prbs":
                modes += ("FM", "PM")
            for mode in modes:
                if _state(session, f"{prefix}:{mode}:STATe?"):
                    visa._validate_frequency_modulation_carrier(carrier, mode)
        if _state(session, prefix + ":PWM:STATe?"):
            raise WaveformParameterError("PWM requires a Pulse carrier.")
        if carrier == "dc":
            for mode in ("AM", "SUM"):
                if _state(session, f"{prefix}:{mode}:STATe?"):
                    raise WaveformParameterError(f"{mode} does not support a DC carrier.")
    if carrier == "arbitrary":
        return
    if carrier == "dc":
        _check_burst_settings(session, context, ch, carrier, None)
        return
    if carrier == "noise":
        bandwidth = _numeric(session, prefix + ":FUNCtion:NOISe:BANDwidth?")
        visa._prepare_noise(
            0.1, bandwidth, 0, 50,
            capabilities=visa._capabilities_for_identity(context.identity),
        )
        _check_burst_settings(session, context, ch, carrier, bandwidth)
    elif carrier == "prbs":
        rate = _numeric(session, prefix + ":FUNCtion:PRBS:BRATe?")
        visa._prepare_prbs(
            rate, 0.1, _query(session, prefix + ":FUNCtion:PRBS:DATA?"), 0,
            _numeric(session, prefix + ":FUNCtion:PRBS:TRANsition:BOTH?"), 50,
        )
        _check_burst_settings(session, context, ch, carrier, rate)
    else:
        _check_coupled_frequencies(session, {ch: None}, context, current=True)


def _check_other_channel_frequency(session, context, ch):
    """Validate the current carrier of frequency-sensitive other-channel modulation."""
    prefix = _source(ch)
    for mode in ("FM", "PM", "PWM"):
        if not _state(session, f"{prefix}:{mode}:STATe?"):
            continue
        if _modulation_source(session, ch, mode) != f"CH{3-ch}":
            continue
        # Reuse the current/proposed frequency, modulation and timing checks.
        _check_coupled_frequencies(session, {ch: _numeric(session, prefix + ":FREQuency?")}, context)


def _voltage_readback(session, ch):
    source = _source(ch)
    offset = _numeric(session, f"{source}:VOLTage:OFFSet?")
    if _function_name(_query(session, f"{source}:FUNCtion?")) == "dc":
        return offset, offset, offset
    low = _numeric(session, f"{source}:VOLTage:LOW?")
    high = _numeric(session, f"{source}:VOLTage:HIGH?")
    span = high - low
    if not math.isfinite(span) or span <= 0:
        raise WaveformVerificationError("Invalid voltage range readback.")
    # These are the documented minimum programmed amplitudes for the two
    # nominal terminations. Do not assume an undocumented minimum for other loads.
    load = visa._parse_status_load(session.query(f"OUTPut{ch}:LOAD?"))
    minimum = {"50": 0.001, "high-z": 0.002}.get(load)
    if minimum is not None and span < minimum and not math.isclose(
        span, minimum, rel_tol=1e-5, abs_tol=1e-9
    ):
        raise WaveformVerificationError("Voltage range readback is below the minimum amplitude.")
    _equal(low + span / 2, offset, "Voltage range offset")
    return low, high, offset


def _levels(session, ch):
    low, high, _ = _voltage_readback(session, ch)
    return low, high


def _check_voltage_coupling_scope(session, *, updating=False):
    # DC has no effective AC amplitude. The joint amplitude/offset contract
    # cannot confirm a mixed DC/non-DC pair or an amplitude update on DC.
    dc = [_function_name(_query(session, f"SOURce{ch}:FUNCtion?")) == "dc" for ch in (1, 2)]
    if updating and any(dc):
        raise WaveformParameterError("Coupled amplitude update requires two non-DC waveforms.")
    if any(dc) and not all(dc):
        raise WaveformParameterError("Mixed DC/non-DC voltage coupling cannot verify equal amplitudes.")


def _check_levels(session, ch, low, high, load=None):
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise WaveformVerificationError("Invalid signal voltage range.")
    source = _source(ch)
    if load is None:
        load = visa._parse_status_load(session.query(f"OUTPut{ch}:LOAD?"))
    peak = 10.0 if load == "high-z" else 10 * float(load) / (float(load) + 50)
    if low < -peak - 1e-9 or high > peak + 1e-9:
        raise WaveformParameterError(f"Voltage exceeds Channel {ch} output rating.")
    if _state(session, f"{source}:VOLTage:LIMit:STATe?"):
        minimum = _numeric(session, f"{source}:VOLTage:LIMit:LOW?")
        maximum = _numeric(session, f"{source}:VOLTage:LIMit:HIGH?")
        if minimum > maximum:
            raise WaveformVerificationError("Invalid programmed voltage limit range.")
        if low < minimum or high > maximum:
            raise WaveformParameterError(f"Voltage exceeds Channel {ch} programmed limits.")


def _signal_levels(session, ch, levels=None):
    low, high = _levels(session, ch) if levels is None else levels
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise WaveformVerificationError("Invalid carrier voltage range.")
    offset = (low + high) / 2
    prefix = _source(ch)
    factor = 1.0
    am = _state(session, f"{prefix}:AM:STATe?")
    summation = _state(session, f"{prefix}:SUM:STATe?")
    if am and summation:
        raise WaveformVerificationError("Conflicting amplitude modulation states.")
    if am:
        depth = _numeric(session, f"{prefix}:AM:DEPTh?")
        # The instrument may have been set from its panel, where AM permits 120%.
        if not 0 <= depth <= 120:
            raise WaveformVerificationError("Invalid AM depth readback.")
        depth /= 100
        factor = depth if _state(session, f"{prefix}:AM:DSSC?") else 1 + depth
    if summation:
        percent = _numeric(session, f"{prefix}:SUM:AMPLitude?")
        if not 0 <= percent <= 100:
            raise WaveformVerificationError("Invalid Sum amplitude readback.")
        factor = 1 + percent / 100
    half = (high - low) / 2 * factor
    return offset - half, offset + half


def _check_combine(session, receiver, source):
    for ch in (receiver, source):
        if _query(session, f"SOURce{ch}:FUNCtion?") == "DC":
            raise WaveformParameterError("DC cannot be combined.")
    low, high = _signal_levels(session, receiver)
    # Use one validated source voltage snapshot, not a second offset query
    # that could disagree with the range used to compute its envelope.
    source_low, source_high, offset = _voltage_readback(session, source)
    other_low, other_high = _signal_levels(session, source, (source_low, source_high))
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
        low, high, offset = _voltage_readback(session, ch)
        channels[str(ch)] = {
            "output_state": _outputs(session, (ch,))[str(ch)],
            "function": function,
            "frequency_hz": (None if function in {"DC", "NOIS", "NOISE", "PRBS", "ARB"}
                             else _numeric(session, prefix + ":FREQuency?")),
            "amplitude_vpp": high - low,
            "offset_v": offset,
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


def _status_topology(status):
    channels = [status["channel_status"][str(ch)] for ch in (1, 2)]
    return (
        status["frequency_coupling"]["enabled"], status["voltage_coupling"],
        [state["tracking"].upper() for state in channels],
        [state["combine_source"].upper() for state in channels],
    )


def _verify_topology_transition(before, after, plan):
    frequency, voltage, tracking, combine = before
    action = plan["action"]
    if action == "configure-frequency-coupling":
        frequency = plan["state"] == "on"
    elif action == "configure-voltage-coupling":
        voltage = plan["state"] == "on"
    elif action == "configure-tracking":
        wanted = {"off": "OFF", "on": "ON", "invert": "INV"}[plan["tracking"]]
        tracking = [wanted, wanted]
    elif action == "configure-combine":
        combine = list(combine)
        combine[plan["channel"] - 1] = plan["source"].upper()
    for actual, expected, label in zip(
        after, (frequency, voltage, tracking, combine),
        ("Frequency coupling", "Voltage coupling", "Tracking", "Combine"), strict=True,
    ):
        _equal(actual, expected, label)


def _check_voltage_relation(status):
    first, second = (status["channel_status"][str(ch)] for ch in (1, 2))
    for field in ("amplitude_vpp", "offset_v", "load", "amplitude_unit", "autorange_enabled"):
        _equal(first[field], second[field], "Coupled " + field)


def _require_status_off(status, channels):
    for ch in channels:
        _equal(status["channel_status"][str(ch)]["output_state"], "off", f"Channel {ch} status")


def _read_frequency_coupling_setting(session, channel=1):
    mode = _query(session, f"SOURce1:{_FREQ}:MODE?")
    if mode in {"RAT", "RATIO"}:
        return "ratio", _numeric(session, f"SOURce{channel}:{_FREQ}:RATio?")
    if mode in {"OFFS", "OFFSET"}:
        return "offset", _numeric(session, f"SOURce{channel}:{_FREQ}:OFFSet?")
    raise WaveformVerificationError("Unknown frequency coupling mode.")


def _check_frequency_setting(mode, value, context):
    if mode == "ratio":
        if not _RATIO_MIN <= value <= _RATIO_MAX:
            raise WaveformVerificationError("Invalid frequency coupling ratio readback.")
    else:
        caps = visa._capabilities_for_identity(context.identity)
        if not abs(value) <= caps.max_sine_square_pulse_noise_frequency_hz:
            raise WaveformVerificationError("Invalid frequency coupling offset readback.")


def _check_frequency_relation(status, context):
    values = {ch: status["channel_status"][str(ch)]["frequency_hz"] for ch in (1, 2)}
    if any(value is None for value in values.values()):
        raise WaveformVerificationError("Unsupported coupled frequency state.")
    settings = status["frequency_coupling"]
    mode = settings["mode"]
    value = settings["ratio"] if mode == "ratio" else settings["offset_hz"]
    _check_frequency_setting(mode, value, context)
    expected = values[1] * value if mode == "ratio" else values[1] + value
    _equal(values[2], expected, "Coupled frequency relation")
    return values


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


def _verify_tracked_pulse_value(field, actual, expected, context):
    """Use the same timing and phase verification as independent Pulse."""
    parser = (visa._parse_phase_verification_number if field == "phase_deg"
              else visa._parse_pulse_verification_number)
    try:
        actual, expected = parser(str(actual)), parser(str(expected))
    except ValueError as exc:
        raise WaveformVerificationError(f"Invalid tracked Pulse {field} readback.") from exc
    tolerance = {
        "frequency_hz": visa.PULSE_FREQUENCY_ABS_TOLERANCE_HZ,
        "phase_deg": 0.0,
    }.get(field, visa.PULSE_TIMING_ABS_TOLERANCE_S)
    visa._verify_pulse_readback(
        field, expected, actual, rel_tolerance=0.0,
        abs_tolerance=tolerance, context=context,
    )


def _verify_tracking_settings(actual, expected, context):
    fields, mode, advanced = actual
    wanted_fields, wanted_mode, wanted_advanced = expected
    _equal(fields.keys(), wanted_fields.keys(), "Tracked waveform fields")
    for suffix, value in wanted_fields.items():
        if suffix.startswith("PULSe:"):
            _verify_tracked_pulse_value(suffix, fields[suffix], value, context)
        elif not visa._configuration_readback_matches(suffix, value, fields[suffix]):
            raise WaveformVerificationError(f"Tracked {suffix} readback did not match.")
    _equal(mode, wanted_mode, "Tracked advanced mode")
    _equal(advanced.keys(), wanted_advanced.keys(), "Tracked advanced settings")
    for field, value in wanted_advanced.items():
        header = {"count": "SOURce1:BURSt:NCYCles", "trigger_delay_s": "TRIGger1:DELay"}.get(field)
        if header is not None:
            if not visa._configuration_readback_matches(header, str(value), str(advanced[field])):
                raise WaveformVerificationError(f"Tracked {field} readback did not match.")
        else:
            _equal(advanced[field], value, "Tracked " + field)


def _verify_tracking_pair(session, context, result, inverted):
    first, second = (result["channel_status"][str(ch)] for ch in (1, 2))
    _equal(_function_name(first["function"]), _function_name(second["function"]), "Tracked function")
    for field in ("frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load", "amplitude_unit", "autorange_enabled"):
        if _function_name(first["function"]) == "pulse" and field in {"frequency_hz", "phase_deg"}:
            _verify_tracked_pulse_value(field, second[field], first[field], context)
        else:
            _equal(first[field], second[field], "Tracked " + field)
    if any(state["polarity"] not in {"NORM", "INV"} for state in (first, second)):
        raise WaveformVerificationError("Unknown tracked output polarity.")
    if (first["polarity"] == second["polarity"]) == inverted:
        raise WaveformVerificationError("Tracked output polarity did not match mode.")
    settings = {
        ch: _tracking_settings(session, context, ch, result["channel_status"][str(ch)]["function"])
        for ch in (1, 2)
    }
    _verify_tracking_settings(settings[2], settings[1], context)
    return settings


def _off_all(session, channels=(1, 2)):
    failures = []
    interruption = None
    label = "Group OFF" if len(channels) == 2 else f"Channel {channels[0]} OFF"
    for ch in channels:
        try:
            session.write(f"OUTPut{ch} OFF")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                interruption = exc
            failures.append(f"Channel {ch} OFF write failed.")
    for ch in channels:
        try:
            _equal(_outputs(session, (ch,))[str(ch)], "off", f"Channel {ch} OFF")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                interruption = exc
            failures.append(f"Channel {ch} OFF was not confirmed.")
    # Even a successful OFF write/readback can leave a settings conflict in
    # the instrument queue. Check it on every group cleanup path.
    try:
        _error_free(session)
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            interruption = exc
        diagnostic = str(exc) if isinstance(exc, WavegenError) else "Error queue query failed."
        failures.append(f"{label}: {diagnostic}")
    if interruption is not None:
        interruption.cleanup_errors = tuple(failures)
        raise interruption
    if failures:
        message = "Group output OFF failed." if len(channels) == 2 else f"{label} failed."
        raise VisaCleanupError(message).attach_cleanup_errors(tuple(failures))


@contextmanager
def _joint_configuration_cleanup(session, channels):
    """Recover affected outputs only after a preflighted joint write starts."""
    started = False

    def mark_started():
        nonlocal started
        started = True

    try:
        yield mark_started
        if started:
            _require_off(session, channels)
    except (Exception, KeyboardInterrupt) as error:
        if not started:
            raise
        try:
            _off_all(session, channels)
        except (WavegenError, KeyboardInterrupt) as cleanup:
            diagnostics = getattr(cleanup, "cleanup_errors", ()) or (str(cleanup) or "OFF cleanup interrupted.",)
            if isinstance(cleanup, KeyboardInterrupt):
                cleanup.cleanup_errors = (str(error) or "Configuration interrupted.",
                                          *getattr(error, "cleanup_errors", ()), *diagnostics)
                raise
            if isinstance(error, WavegenError):
                error.output_state = None
                error.attach_cleanup_errors(error.cleanup_errors + diagnostics)
            elif isinstance(error, KeyboardInterrupt):
                error.cleanup_errors = (*getattr(error, "cleanup_errors", ()), *diagnostics)
                error.args = (f"{error}; configuration OFF cleanup failed: {'; '.join(diagnostics)}",)
            else:
                raise VisaWriteError(
                    "Could not apply the requested instrument control write."
                ).attach_cleanup_errors(diagnostics) from error
        else:
            if isinstance(error, WavegenError):
                error.output_state = "off"
        raise


def _apply(session, plan, arguments, context, mark_configuration_write, mark_output_write):
    action = plan["action"]
    channels = plan["channels"]
    commands = plan["commands"]
    if action == "dual-channel-status":
        return _status(session)
    if action == "output":
        if plan["requested_output_state"] == "off":
            _off_all(session)
            actual = _outputs(session)
            _equal(actual, {"1": "off", "2": "off"}, "Group output OFF")
            return {"output_states": actual, "output_state": "off"}
        _require_off(session, channels)
        topology = _topology(session)
        _check_topology(session, topology)
        frequency, voltage, tracking, combined = topology
        if frequency or voltage or tracking[0] != "OFF":
            status = _status(session)
            _require_status_off(status, channels)
            _equal(_status_topology(status), topology, "Group output topology")
            if frequency:
                values = _check_frequency_relation(status, context)
                _check_coupled_frequencies(session, values, context)
            if voltage:
                _check_voltage_relation(status)
            if tracking[0] != "OFF":
                _check_tracking_memory(session, context)
                _verify_tracking_pair(session, context, status, tracking[0] in {"INV", "INVERTED"})
        for ch in (1, 2):
            if not frequency:
                _check_current_frequency_settings(session, context, ch)
            _check_levels(session, ch, *_signal_levels(session, ch))
            if combined[ch-1] != "NONE":
                _check_combine(session, ch, 3-ch)
        _require_off(session, channels)
        try:
            mark_output_write()
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
                error.output_state = None
                error.attach_cleanup_errors(error.cleanup_errors + cleanup.cleanup_errors)
            except KeyboardInterrupt as cleanup:
                cleanup.cleanup_errors = (str(error), *getattr(cleanup, "cleanup_errors", ()))
                raise
            else:
                error.output_state = "off"
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
        mark_configuration_write()
        session.write(commands[0])
        _error_free(session)
        for ch, expected in phases.items():
            _equal(_numeric(session, f"SOURce{ch}:PHASe?"), expected, "Phase setting")
        return {"output_state": "off", "phase_settings_preserved": True}

    frequency, voltage, tracking, combine = _topology(session)
    tracked = any(mode != "OFF" for mode in tracking)
    # Explicit release operations must remain available to recover from a
    # conflicting topology. All other topology writes must start from a
    # consistent state, not just frequency changes and group ON.
    releasing = (
        (action in {"configure-frequency-coupling", "configure-voltage-coupling"} and plan["state"] == "off")
        or (action == "configure-tracking" and plan["tracking"] == "off")
        or (action == "configure-combine" and plan["source"] == "none")
    )
    if not releasing:
        _check_topology(session, (frequency, voltage, tracking, combine))
    expected_frequency = {}
    expected_coupling = None
    tracking_reference = None
    if action == "configure-frequency-coupling" and plan["state"] == "on":
        if tracked or frequency:
            raise WaveformParameterError("Disable tracking or existing frequency coupling first.")
        ref = plan["reference_channel"]
        original = _numeric(session, f"SOURce{ref}:FREQuency?")
        mode = plan["coupling_mode"]
        value = plan["ratio"] if mode == "ratio" else plan["offset_hz"]
        other = original * value if mode == "ratio" else original + value
        expected_frequency = {ref: original, 3-ref: other}
        expected_coupling = (mode, value if ref == 1 else (1 / value if mode == "ratio" else -value))
    elif action == "update-coupled-frequency":
        if not frequency or tracked:
            raise WaveformParameterError("Frequency coupling must be active without tracking.")
        ch = plan["channel"]
        val = plan["frequency_hz"]
        mode, value = _read_frequency_coupling_setting(session, ch)
        _check_frequency_setting(mode, value, context)
        other = val * value if mode == "ratio" else val + value
        expected_frequency = {ch: val, 3-ch: other}
        expected_coupling = (mode, value if ch == 1 else (1 / value if mode == "ratio" else -value))
    elif action == "configure-voltage-coupling" and plan["state"] == "on":
        if tracked:
            raise WaveformParameterError("Disable tracking first.")
        _check_voltage_coupling_scope(session)
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
        _check_voltage_coupling_scope(session, updating=True)
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
        status = _status(session)
        _require_status_off(status, channels)
        _equal(_status_topology(status), (frequency, voltage, tracking, combine), "Tracking topology")
        tracking_reference = status["channel_status"][str(ref)]
        _check_current_frequency_settings(session, context, ref)
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

    if frequency and action not in {"configure-frequency-coupling", "update-coupled-frequency"}:
        # Unrelated topology writes must preserve the active relation, not
        # merely leave some valid frequency coupling enabled.
        expected_coupling = _read_frequency_coupling_setting(session)
    if expected_frequency:
        _check_coupled_frequencies(session, expected_frequency, context)
    if action == "configure-tracked-waveform":
        selected = plan["channel"]
        original = tuple(command.replace(f"SOURce{selected}:", "SOURce1:").replace(
            f"TRIGger{selected}:", "TRIGger1:"
        ) for command in commands)
        # Reuse the independent configuration's explicit-delay pre-write gate.
        # The existing joint error check below still checks the queue after writes.
        visa._check_trigger_delay_configuration(session, original, before_writes=True)
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
        mark_configuration_write()
        session.write(command)
    if action == "configure-tracking" and plan["tracking"] != "off":
        # Native tracking assigns Sync to the reference channel. Verify the
        # connector is still OFF before accepting the new source.
        try:
            visa._verify_sync_reply(session, visa._SYNC_STATE_QUERY, visa._SYNC_STATE_REPLIES, "off")
        except (WavegenError, KeyboardInterrupt) as error:
            visa._attempt_sync_off(session, error)
            raise
        visa._verify_sync_reply(
            session, "OUTPut:SYNC:SOURce?", {"CH1": "CH1", "CH2": "CH2"},
            f"CH{plan['reference_channel']}",
        )
    _error_free(session)
    _require_off(session, channels)
    result = _status(session)
    _require_status_off(result, channels)
    actual_topology = _status_topology(result)
    _verify_topology_transition((frequency, voltage, tracking, combine), actual_topology, plan)
    if not releasing:
        _check_topology(session, actual_topology)
        if actual_topology[0]:
            _check_frequency_relation(result, context)
        if actual_topology[1]:
            _check_voltage_relation(result)
    if expected_coupling is not None:
        mode, value = expected_coupling
        settings = result["frequency_coupling"]
        _equal(settings["mode"], mode, "Coupling mode")
        _equal(settings["ratio" if mode == "ratio" else "offset_hz"], value, "Coupling setting")
    if action == "configure-tracking" and plan["tracking"] != "off":
        settings = _verify_tracking_pair(session, context, result, plan["tracking"] == "invert")
        _verify_tracking_settings(settings[plan["reference_channel"]], tracking_reference_settings, context)
        reference = result["channel_status"][str(plan["reference_channel"])]
        _equal(_function_name(reference["function"]), _function_name(tracking_reference["function"]),
               "Tracking reference function")
        for field in ("frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load",
                      "amplitude_unit", "autorange_enabled", "polarity"):
            if _function_name(reference["function"]) == "pulse" and field in {"frequency_hz", "phase_deg"}:
                _verify_tracked_pulse_value(field, reference[field], tracking_reference[field], context)
            else:
                _equal(reference[field], tracking_reference[field], "Tracking reference " + field)
    elif action == "update-coupled-voltage":
        for ch in (1, 2):
            state = result["channel_status"][str(ch)]
            _equal(state["amplitude_vpp"], plan["amplitude_vpp"], "Coupled amplitude")
            _equal(state["offset_v"], plan["offset_v"], "Coupled offset")
            _equal(state["amplitude_unit"], "VPP", "Coupled amplitude unit")
    elif action == "configure-tracked-waveform":
        settings = _verify_tracking_pair(session, context, result, tracking[0] in {"INV", "INVERTED"})
        wave = _tracked_plan(context.identity.canonical_model_id, arguments)
        for ch in (1, 2):
            actual = result["channel_status"][str(ch)]
            _equal(_function_name(actual["function"]), plan["waveform"], f"CH{ch} tracked function")
            for field in ("frequency_hz", "amplitude_vpp", "offset_v", "phase_deg", "load"):
                expected = getattr(wave, field, None)
                if expected is not None:
                    if plan["waveform"] == "pulse" and field in {"frequency_hz", "phase_deg"}:
                        _verify_tracked_pulse_value(field, actual[field], expected, context)
                    else:
                        _equal(actual[field], expected, f"CH{ch} tracked {field}")
            if plan["waveform"] == "pulse":
                for suffix, field in (
                    ("PULSe:WIDTh", "pulse_width_s"),
                    ("PULSe:TRANsition:LEADing", "leading_edge_s"),
                    ("PULSe:TRANsition:TRAiling", "trailing_edge_s"),
                ):
                    _verify_tracked_pulse_value(suffix, settings[ch][0][suffix], getattr(wave, field), context)
            if hasattr(wave, "voltage_v"):
                _equal(actual["offset_v"], wave.voltage_v, f"CH{ch} tracked DC voltage")
        # Check other waveform-specific values with the existing generic
        # readback; Pulse's width and both edges were verified above.
        fields = {}
        if plan["waveform"] != "pulse":
            for command in commands:
                header, sep, value = command.partition(" ")
                if sep and header.startswith(f"SOURce{selected}:FUNCtion:") and value not in {"MINimum", "MAXimum"}:
                    if header.endswith((":DCYCle", ":SYMMetry", ":BANDwidth", ":BRATe", ":DATA", ":TRANsition:BOTH")):
                        fields[header] = value
        has_advanced = any(command.endswith(":STATe ON") for command in original)
        for ch in (1, 2):
            if "SOURce1:VOLTage:UNIT VPP" in original:
                _equal(result["channel_status"][str(ch)]["amplitude_unit"], "VPP", "Tracked voltage unit")
            if "SOURce1:FREQuency:MODE CW" in original and not has_advanced:
                _equal(settings[ch][1], "CW", "Tracked frequency mode")
            if plan["waveform"] != "pulse":
                visa._verify_configuration_readback(session, context,
                    {key.replace(f"SOURce{selected}:", f"SOURce{ch}:"): value for key, value in fields.items()},
                    label="Tracked waveform")
            if has_advanced:
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

    def mark_output_write():
        nonlocal attempted_on
        attempted_on = True

    def operation(session, context):
        plan = plan_dual_channel(action, arguments, context.identity.canonical_model_id)
        with _joint_configuration_cleanup(session, plan["channels"]) as mark_configuration_write:
            result = _apply(session, plan, arguments, context,
                            mark_configuration_write, mark_output_write)
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
                    error.output_state = None
                    error.attach_cleanup_errors(error.cleanup_errors + diagnostics)
                else:
                    error.cleanup_errors = (*getattr(error, "cleanup_errors", ()), *diagnostics)
                    error.args = (f"{error}; group OFF cleanup failed: {diagnostics[0]}",)
            else:
                if isinstance(error, WavegenError):
                    error.output_state = "off"
        raise
    return dict(result, resource=context.resource, backend=context.backend, transport=context.transport,
                identity={"manufacturer": context.identity.manufacturer,
                          "model": context.identity.model,
                          "canonical_model_id": context.identity.canonical_model_id})
