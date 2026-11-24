"""Explicit, contributor-only dual-channel operations using Core VISA safety."""

from __future__ import annotations

from collections.abc import Mapping
import math

from . import visa
from .errors import (
    UnsupportedInstrumentError, VisaCleanupError, VisaWriteError,
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
            result.update(mode=mode, reference_channel=ch, **{key: value})
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
        visa._validate_vpp_levels(amp, offset, "high-z", "Coupled voltage")
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
        matches = math.isclose(actual, expected, rel_tol=1e-5, abs_tol=1e-9)
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


def _active_other_source(session, channel):
    for mode in ("AM", "FM", "PM", "PWM", "SUM"):
        prefix = f"SOURce{channel}:{mode}"
        if _state(session, prefix + ":STATe?"):
            if _query(session, prefix + ":SOURce?") in {"CH1", "CH2"}:
                return True
    return False


def _frequency_limit(session, ch, value):
    prefix = f"SOURce{ch}:FREQuency?"
    if _query(session, f"SOURce{ch}:FUNCtion?") in {"ARB", "DC", "NOIS", "NOISE", "PRBS"}:
        raise WaveformParameterError("Selected waveform does not support frequency coupling.")
    low, high = (_numeric(session, f"{prefix} {bound}") for bound in ("MINimum", "MAXimum"))
    if not low <= value <= high:
        raise WaveformParameterError(f"Coupled frequency exceeds Channel {ch} limits.")


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


def _check_combine(session, receiver, source):
    for ch in (receiver, source):
        if _query(session, f"SOURce{ch}:FUNCtion?") == "DC":
            raise WaveformParameterError("DC cannot be combined.")
    low, high = _levels(session, receiver)
    other_low, other_high = _levels(session, source)
    offset = _numeric(session, f"SOURce{source}:VOLTage:OFFSet?")
    _check_levels(session, receiver, low + other_low - offset, high + other_high - offset)


def _status(session):
    frequency, voltage, tracking, combine = _topology(session)
    mode = _query(session, f"SOURce1:{_FREQ}:MODE?")
    if mode not in {"RAT", "RATIO", "OFFS", "OFFSET"}:
        raise WaveformVerificationError("Unknown frequency coupling mode.")
    channels = {}
    for ch in (1, 2):
        prefix = _source(ch)
        function = _query(session, prefix + ":FUNCtion?")
        low, high = _levels(session, ch)
        channels[str(ch)] = {
            "output_state": _outputs(session, (ch,))[str(ch)],
            "function": function,
            "frequency_hz": (None if function in {"DC", "NOIS", "NOISE", "PRBS", "ARB"}
                             else _numeric(session, prefix + ":FREQuency?")),
            "amplitude_vpp": high - low,
            "offset_v": _numeric(session, prefix + ":VOLTage:OFFSet?"),
            "phase_deg": (None if function in {"DC", "NOIS", "NOISE", "PRBS", "ARB"}
                          else _numeric(session, prefix + ":PHASe?")),
            "load": visa._parse_status_load(session.query(f"OUTPut{ch}:LOAD?")),
            "polarity": _query(session, f"OUTPut{ch}:POLarity?"),
            "combine_source": combine[ch-1].lower(),
            "tracking": tracking[ch-1].lower(),
        }
    return {
        "frequency_coupling": {"enabled": frequency, "mode": "ratio" if mode.startswith("RAT") else "offset",
                               "ratio": _numeric(session, f"SOURce1:{_FREQ}:RATio?"),
                               "offset_hz": _numeric(session, f"SOURce1:{_FREQ}:OFFSet?")},
        "voltage_coupling": voltage, "channel_status": channels,
    }


def _off_all(session):
    failures = []
    for ch in (1, 2):
        try:
            session.write(f"OUTPut{ch} OFF")
        except (Exception, KeyboardInterrupt):
            failures.append(f"Channel {ch} OFF write failed.")
    for ch in (1, 2):
        try:
            _equal(_outputs(session, (ch,))[str(ch)], "off", f"Channel {ch} OFF")
        except (Exception, KeyboardInterrupt):
            failures.append(f"Channel {ch} OFF was not confirmed.")
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
            return {"output_states": _outputs(session), "output_state": "off"}
        _require_off(session, channels)
        _, _, _, combined = _topology(session)
        for ch in (1, 2):
            _check_levels(session, ch, *_levels(session, ch))
            if combined[ch-1] != "NONE":
                _check_combine(session, ch, 3-ch)
        try:
            for command in commands:
                session.write(command)
            _equal(_outputs(session), {"1": "on", "2": "on"}, "Group output")
            _error_free(session)
        except (Exception, KeyboardInterrupt) as exc:
            error = exc if isinstance(exc, WavegenError) else VisaWriteError("Group output ON failed.")
            try:
                _off_all(session)
            except WavegenError as cleanup:
                error.attach_cleanup_errors(cleanup.cleanup_errors)
            if isinstance(exc, KeyboardInterrupt):
                exc.args = (str(error) + " " + "; ".join(error.cleanup_errors),)
                raise
            raise error from (None if error is exc else exc)
        return {"output_states": _outputs(session), "output_state": "on"}
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
    if action == "configure-frequency-coupling" and plan["state"] == "on":
        if tracked or frequency:
            raise WaveformParameterError("Disable tracking or existing frequency coupling first.")
        ref = plan["reference_channel"]
        original = _numeric(session, f"SOURce{ref}:FREQuency?")
        other = original * plan["ratio"] if plan["mode"] == "ratio" else original + plan["offset_hz"]
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
                _check_levels(session, ch, *_levels(session, ref), load=load)
        if combine != ["NONE", "NONE"]:
            raise WaveformParameterError("Disable combine before enabling voltage coupling.")
    elif action == "update-coupled-voltage":
        if not voltage or tracked:
            raise WaveformParameterError("Voltage coupling must be active without tracking.")
        amp, offset = plan["amplitude_vpp"], plan["offset_v"]
        for ch in (1, 2):
            for center in (0, offset):
                _check_levels(session, ch, center-amp/2, center+amp/2)
        if combine != ["NONE", "NONE"]:
            receiver = 1 if combine[0] != "NONE" else 2
            _check_levels(session, receiver, offset-amp, offset+amp)
    elif action == "configure-tracking" and plan["tracking"] != "off":
        if frequency or voltage or combine != ["NONE", "NONE"] or _state(session, "RATE:COUPle:STATe?"):
            raise WaveformParameterError("Explicitly disable conflicting coupling and combine first.")
        ref = plan["reference_channel"]
        if _active_other_source(session, ref):
            raise WaveformParameterError("Tracking cannot copy other-channel modulation.")
        if _state(session, "OUTPut:SYNC?"):
            raise WaveformParameterError("Sync output must be OFF before changing its source by tracking.")
        load = visa._parse_status_load(session.query(f"OUTPut{ref}:LOAD?"))
        _check_levels(session, 3-ref, *_levels(session, ref), load=load)
    elif action == "configure-combine" and plan["source"] != "none":
        ch = plan["channel"]
        if tracked or combine[2-ch] != "NONE":
            raise WaveformParameterError("Tracking and another combine receiver must be OFF.")
        _check_combine(session, ch, 3-ch)
    elif action == "configure-tracked-waveform":
        if not tracked or frequency or voltage or combine != ["NONE", "NONE"]:
            raise WaveformParameterError("Tracking must be active without conflicting coupling.")
        wave = _tracked_plan(context.identity.canonical_model_id, arguments)
        load = wave.load
        if hasattr(wave, "amplitude_vpp"):
            amp, offset = wave.amplitude_vpp, wave.offset_v
            for ch in (1, 2):
                _check_levels(session, ch, offset-amp/2, offset+amp/2, load=load)
        elif hasattr(wave, "voltage_v"):
            for ch in (1, 2):
                _check_levels(session, ch, wave.voltage_v, wave.voltage_v, load=load)

    for ch, value in expected_frequency.items():
        _frequency_limit(session, ch, value)
    for command in commands:
        session.write(command)
    _error_free(session)
    _require_off(session, channels)
    result = _status(session)
    if action == "configure-frequency-coupling":
        _equal(result["frequency_coupling"]["enabled"], plan["state"] == "on", "Frequency coupling")
        if plan["state"] == "on":
            _equal(result["frequency_coupling"]["mode"], plan["mode"], "Coupling mode")
            key = "RATio" if plan["mode"] == "ratio" else "OFFSet"
            value = plan["ratio"] if plan["mode"] == "ratio" else plan["offset_hz"]
            _equal(_numeric(session, f"SOURce{plan['reference_channel']}:{_FREQ}:{key}?"), value, "Coupling setting")
    elif action == "configure-voltage-coupling":
        _equal(result["voltage_coupling"], plan["state"] == "on", "Voltage coupling")
    elif action == "configure-tracking":
        actual = [_query(session, f"SOURce{ch}:TRACk?") for ch in (1, 2)]
        wanted = {"off": "OFF", "on": "ON", "invert": "INV"}[plan["tracking"]]
        _equal([v.replace("INVERTED", "INV") for v in actual], [wanted, wanted], "Tracking")
    elif action == "configure-combine":
        _equal(result["channel_status"][str(plan["channel"])]["combine_source"], plan["source"], "Combine")
    elif action == "update-coupled-voltage":
        for ch in (1, 2):
            state = result["channel_status"][str(ch)]
            _equal(state["amplitude_vpp"], plan["amplitude_vpp"], "Coupled amplitude")
            _equal(state["offset_v"], plan["offset_v"], "Coupled offset")
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
    except VisaCleanupError as error:
        if attempted_on:
            # A successful ON followed by session teardown failure still needs a
            # bounded best-effort OFF against the same explicitly selected resource.
            try:
                visa._run_on_supported_instrument(resource, backend,
                    lambda session, _: _off_all(session), **options)
            except WavegenError as cleanup:
                error.attach_cleanup_errors(error.cleanup_errors + (str(cleanup),) + cleanup.cleanup_errors)
        raise
    return dict(result, resource=context.resource, backend=context.backend, transport=context.transport,
                identity={"manufacturer": context.identity.manufacturer,
                          "model": context.identity.model,
                          "canonical_model_id": context.identity.canonical_model_id})
