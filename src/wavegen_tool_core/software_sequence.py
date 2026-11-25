"""Plan and execute finite, single-instrument software sequences."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from threading import TIMEOUT_MAX, Event

from . import visa
from .dual_channel import DUAL_CHANNEL_COMMANDS, DUAL_CHANNEL_WRITE_COMMANDS, is_dual_action, run_dual_channel, uses_other_channel_modulation
from .action_arguments import (
    MODULATION_FIELDS,
    canonicalize_voltage_arguments,
    configuration_options,
    validate_action_arguments,
)
from .arbitrary import (
    _name,
    _open_regular,
    _require_sample_rate_uncoupled,
    _sequence,
    run_arbitrary,
)
from .errors import (
    SoftwareSequenceCancelled,
    SoftwareSequenceInterrupted,
    StatusQueryError,
    UnsupportedInstrumentError,
    VisaCleanupError,
    WaveformParameterError,
    WaveformVerificationError,
    WavegenError,
)
from .identity import SUPPORT_POLICY_MODE_PRODUCT, SUPPORT_POLICY_MODE_VALIDATION
from .simulator import SimulatedResourceManagerFactory

SOFTWARE_SEQUENCE_ACTION = "run-software-sequence"
_AMPLITUDE_UPDATE = "update-sine-amplitude"
_FREQUENCY_LIST_ONCE = "run-sine-list-sweep-once"

_WAVEFORM_OPERATIONS = {
    f"configure-{waveform}": (
        getattr(visa, "dry_run_" + waveform.replace("-", "_")),
        getattr(visa, "configure_" + waveform.replace("-", "_")),
    )
    for waveform in (
        "sine",
        "square",
        "ramp",
        "triangle",
        "pulse",
        "dc",
        "noise",
        "prbs",
        "sine-sweep",
        "square-sweep",
        "ramp-sweep",
        "triangle-sweep",
        "sine-list-sweep",
        "square-list-sweep",
        "ramp-list-sweep",
        "triangle-list-sweep",
    )
}
_CONFIGURE_ACTIONS = frozenset(_WAVEFORM_OPERATIONS) | {"configure-output"}
_CREATE_ACTIONS = {"upload-arbitrary", "configure-arbitrary-sequence"}
_PLAYBACK_ACTIONS = {"configure-arbitrary", "configure-arbitrary-sequence"}
_ARBITRARY_ACTIONS = _CREATE_ACTIONS | _PLAYBACK_ACTIONS | {"list-arbitrary-memory"}
_CHANNEL_WRITE_ACTIONS = _CONFIGURE_ACTIONS | _CREATE_ACTIONS | _PLAYBACK_ACTIONS | {
    "output", _AMPLITUDE_UPDATE, _FREQUENCY_LIST_ONCE,
}
_ACTIONS = _CHANNEL_WRITE_ACTIONS | DUAL_CHANNEL_COMMANDS | {
    "list-arbitrary-memory",
    "trigger",
    "wait",
    "status",
    "read-errors",
    "readback-advanced",
}
_READ_OPERATIONS = {
    "status": visa.query_status,
    "read-errors": visa.read_error_queue,
    "readback-advanced": visa.query_advanced_mode,
}
_MAX_RESULTS = 200
_TEXT_FIELDS = {
    "am_type",
    "mode",
    "name",
    "path",
    "pattern",
    "polarity",
    "shape",
    "spacing",
    "trigger_source",
    "trigger_slope",
    "source",
    "state",
    "waveform",
}
_BOOL_FIELDS = {
    "enabled",
    "verify_readback",
    "load_builtin",
    "voltage_limits_enabled",
    "autorange_enabled",
}


class SoftwareSequenceError(WavegenError):
    """A sequence failure with bounded step and cleanup details."""

    def __init__(self, cause: WavegenError, details: dict[str, object]) -> None:
        super().__init__(str(cause)[:512], backend=cause.backend, transport=cause.transport)
        self.code = cause.code
        self.cause = cause
        self.cleanup_errors = tuple(message[:256] for message in cause.cleanup_errors)
        self.details = details


@dataclass(frozen=True)
class _Step:
    action: str
    arguments: dict[str, object]

    @property
    def enables_output(self) -> bool:
        return self.action == _FREQUENCY_LIST_ONCE or (
            self.action == "output" and self.arguments["enabled"] is True
        )


@dataclass(frozen=True)
class SoftwareSequence:
    """A validated software sequence definition."""

    loop_count: int
    steps: tuple[_Step, ...]

    @property
    def writes(self) -> bool:
        return any(step.action in _CHANNEL_WRITE_ACTIONS | DUAL_CHANNEL_WRITE_COMMANDS | {"trigger"} for step in self.steps)

    @property
    def enables_output(self) -> bool:
        return any(step.enables_output for step in self.steps)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Duplicate JSON fields.")
    return fields


def _reject_constant(value: str) -> None:
    raise ValueError("JSON numbers must be finite.")


def _normalize_decimals(value: object, key: str = "") -> object:
    if isinstance(value, dict):
        return {name: _normalize_decimals(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_normalize_decimals(item) for item in value]
    if isinstance(value, Decimal) and key not in {"trigger_delay_s", "start_phase_deg", "seconds"}:
        return float(value)
    return value


def parse_software_sequence_json(text: str) -> object:
    """Parse a JSON document or envelope, retaining exact stepped decimals."""
    return _normalize_decimals(
        json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_fields,
        )
    )


def read_software_sequence_file(path: str) -> object:
    """Read a sequence from a local regular UTF-8 JSON file."""
    try:
        with open(path, "r", encoding="utf-8-sig", opener=_open_regular) as stream:
            return parse_software_sequence_json(stream.read())
    except (OSError, TypeError, ValueError, RecursionError, ArithmeticError) as exc:
        raise WaveformParameterError(
            "Could not read a valid UTF-8 software sequence JSON file."
        ) from exc


def _validate_json_types(arguments: Mapping[str, object]) -> None:
    for key, value in arguments.items():
        if key in MODULATION_FIELDS | {"burst"}:
            if isinstance(value, Mapping):
                _validate_json_types(value)
        elif key in _TEXT_FIELDS:
            if not isinstance(value, str):
                raise WaveformParameterError(f"{key} must be a string.")
        elif key in _BOOL_FIELDS | {"document"}:
            # The shared argument validator checks these fields.
            continue
        elif key == "arguments" and isinstance(value, Mapping):
            _validate_json_types(value)
        elif key == "channel" and value == "all":
            # The action validator accepts this only for group output.
            continue
        elif key in {"channel", "reference_channel", "max_reads", "count"}:
            if type(value) is not int:
                raise WaveformParameterError(f"{key} must be an integer.")
        elif key == "load" and isinstance(value, str):
            continue
        else:
            numbers = value if key == "frequencies_hz" else (value,)
            for number in numbers:
                if type(number) not in (int, float, Decimal):
                    raise WaveformParameterError(f"{key} must contain JSON numbers.")
                visa._normalize_finite_number(number, key, waveform="Software sequence")


def _step_failure(
    details: dict[str, object], action: object, index: int, loop: int, cause: WavegenError
) -> SoftwareSequenceError:
    # Keep command identifiers without reflecting arbitrary input such as local paths.
    action_name = (
        action if isinstance(action, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", action) else None
    )
    return SoftwareSequenceError(
        cause,
        dict(
            details,
            failed_step={
                "loop": loop,
                "step": index,
                "action": action_name,
                "error": _error_summary(cause),
            },
        ),
    )


def _error_summary(cause: WavegenError) -> dict[str, object]:
    result = {"code": cause.code, "message": str(cause)[:512]}
    if cause.cleanup_errors:
        result["cleanup_errors"] = [message[:256] for message in cause.cleanup_errors]
    return result


def validate_software_sequence(
    document: object, *, require_absolute_path: bool = False, product_live: bool = False,
    _workflow_action: str | None = None,
) -> SoftwareSequence:
    """Validate the document and static safety constraints without device or file I/O."""
    if not isinstance(document, Mapping) or document.keys() - {"version", "loop_count", "steps"}:
        raise WaveformParameterError(
            "Software sequence must be an object with version, steps and optional loop_count."
        )
    if type(document.get("version")) is not int or document["version"] != 1:
        raise WaveformParameterError("Software sequence version must be the integer 1.")
    loop_count = document.get("loop_count", 1)
    if type(loop_count) is not int or not 1 <= loop_count <= 10_000:
        raise WaveformParameterError(
            "Software sequence loop_count must be an integer from 1 to 10000."
        )
    steps = document.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 250:
        raise WaveformParameterError("Software sequence must contain 1 to 250 steps.")
    if len(steps) * loop_count > 1_000_000:
        raise WaveformParameterError("Software sequence exceeds 1000000 total step executions.")

    details = _sequence_counts(len(steps), loop_count)
    normalized = []
    created_names = set()
    for index, entry in enumerate(steps, 1):
        action = entry.get("action") if isinstance(entry, Mapping) else None
        try:
            if not isinstance(entry, Mapping) or entry.keys() - {"action", "arguments"}:
                raise WaveformParameterError(
                    f"Software sequence step {index} has invalid or unknown fields."
                )
            if not isinstance(action, str) or action not in _ACTIONS:
                raise WaveformParameterError(
                    f"Software sequence step {index} has an unsupported action."
                )
            raw_arguments = entry.get("arguments", {})
            if not isinstance(raw_arguments, Mapping):
                raise WaveformParameterError(
                    f"Software sequence step {index} arguments must be an object."
                )
            arguments = dict(raw_arguments)
            if product_live and (is_dual_action(action, arguments) or uses_other_channel_modulation(arguments)):
                raise UnsupportedInstrumentError("Dual channel operations are outside Product Live feature scope.")
            if product_live and action in _ARBITRARY_ACTIONS:
                raise UnsupportedInstrumentError(
                    "Arbitrary operations are outside Product Live feature scope."
                )
            if action == _AMPLITUDE_UPDATE:
                if _workflow_action != "run-amplitude-ramp":
                    raise WaveformParameterError("Amplitude updates require Amplitude Ramp.")
                if product_live:
                    raise UnsupportedInstrumentError("Amplitude Ramp is outside Product Live feature scope.")
                _validate_json_types(arguments)
            elif action == "wait":
                if arguments.keys() != {"seconds"}:
                    raise WaveformParameterError("wait requires only the seconds argument.")
                _validate_json_types(arguments)
                if not 0 <= arguments["seconds"] <= TIMEOUT_MAX:
                    raise WaveformParameterError(
                        "wait seconds must be non-negative and within the host timer range."
                    )
                arguments["seconds"] = float(arguments["seconds"])
            else:
                if "confirm_output" in arguments:
                    raise WaveformParameterError(
                        "Output confirmation is an outer request argument."
                    )
                configure_action = action
                if action == _FREQUENCY_LIST_ONCE:
                    if _workflow_action != "run-frequency-step":
                        raise WaveformParameterError("A single-pass List Sweep requires Frequency Step.")
                    configure_action = "configure-sine-list-sweep"
                validate_action_arguments(
                    configure_action, arguments, require_absolute_path=require_absolute_path
                )
                _validate_json_types(arguments)
                canonicalize_voltage_arguments(configure_action, arguments)
                if action in _CREATE_ACTIONS:
                    if loop_count != 1:
                        raise WaveformParameterError("Arbitrary creation requires loop_count=1.")
                    if action == "upload-arbitrary":
                        name = _name(arguments["name"])
                    else:
                        native = arguments["document"]
                        playback, _ = _sequence(native)
                        _validate_json_types(
                            {
                                key: value
                                for key, value in native.items()
                                if key not in {"steps", "version"}
                            }
                        )
                        name = playback.name
                    item = (arguments["channel"], name.casefold())
                    if item in created_names:
                        raise WaveformParameterError(
                            "Software sequence creates the same Arbitrary name twice on one channel."
                        )
                    created_names.add(item)
        except WavegenError as exc:
            raise _step_failure(details, action, index, 1, exc) from exc
        normalized.append(_Step(action, arguments))
    return SoftwareSequence(loop_count, tuple(normalized))


def _sequence_counts(step_count: int, loop_count: int) -> dict[str, object]:
    return {
        "action": SOFTWARE_SEQUENCE_ACTION,
        "step_count": step_count,
        "loop_count": loop_count,
        "execution_units": step_count * loop_count,
        "completed_loops": 0,
        "completed_step_executions": 0,
        "cleanup": None,
    }


def _waveform_arguments(step: _Step, *, readback: bool = False) -> dict[str, object]:
    args = step.arguments
    if step.action == "configure-output":
        config = visa.OutputConfig(
            **{key: value for key, value in args.items() if key != "channel"}
        )
        return {"config": config, "channel": args["channel"]}
    values = {
        key: value
        for key, value in args.items()
        if key not in MODULATION_FIELDS | {"burst", "verify_readback"}
    }
    values.update(configuration_options(step.action, args, readback=readback))
    return values


def _plan_step(step: _Step, model: str, *, product_live: bool) -> dict[str, object]:
    action = step.action
    if is_dual_action(action, step.arguments):
        if product_live:
            raise UnsupportedInstrumentError("Dual channel operations are outside Product Live feature scope.")
        return run_dual_channel(action, step.arguments, model=model)
    if action == _FREQUENCY_LIST_ONCE:
        return visa._plan_sine_list_sweep_once(model, step.arguments)
    if action == _AMPLITUDE_UPDATE:
        sine = visa.dry_run_sine(model, **step.arguments)
        return {
            "channel": sine.channel,
            "amplitude_vpp": sine.amplitude_vpp,
            "commands": [f"SOURce{sine.channel}:VOLTage {visa._format_scpi_number(sine.amplitude_vpp)}"],
        }
    if action in _ARBITRARY_ACTIONS:
        return run_arbitrary(action, step.arguments, model=model)
    if action in _CONFIGURE_ACTIONS:
        operation = (
            visa.dry_run_output if action == "configure-output" else _WAVEFORM_OPERATIONS[action][0]
        )
        result = operation(model, **_waveform_arguments(step))
        if product_live:
            configs = tuple(getattr(result, field, None) for field in MODULATION_FIELDS | {"burst"})
            feature = visa._advanced_mode_validation_only_feature(configs)
            if action.endswith("-sweep") and not action.endswith("-list-sweep"):
                _, feature = visa._prepare_live_sweep_source(
                    result.trigger_source,
                    result.trigger_delay_s,
                    waveform="Software sequence sweep",
                )
            if feature is not None:
                raise UnsupportedInstrumentError(
                    f"{feature} is outside Product Live feature scope."
                )
        if step.arguments.get("verify_readback"):
            visa._advanced_mode_readback_fields(result.commands, channel=step.arguments["channel"])
        return asdict(result)
    return dict(step.arguments)


def _check_cancellation(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SoftwareSequenceCancelled("Sequence cancellation requested.")


def _wait(seconds: float, cancel_event: Event | None) -> None:
    if cancel_event is None:
        time.sleep(seconds)
    elif cancel_event.wait(seconds):
        _check_cancellation(cancel_event)


def _execute_step(
    step: _Step, resource: str, backend: str | None, options: dict,
    cancel_event: Event | None,
) -> object:
    action = step.action
    args = step.arguments
    if is_dual_action(action, args):
        return run_dual_channel(action, args, resource=resource, backend=backend, **options)
    if action == "wait":
        _wait(args["seconds"], cancel_event)
        return args
    if action == _FREQUENCY_LIST_ONCE:
        return visa._run_sine_list_sweep_once(
            resource, backend, args, wait=lambda seconds: _wait(seconds, cancel_event),
            check_cancel=lambda: _check_cancellation(cancel_event), **options
        )
    if action == _AMPLITUDE_UPDATE:
        return visa.update_sine_amplitude(
            resource, args["amplitude_vpp"], backend, channel=args["channel"], **options
        )
    if action in _ARBITRARY_ACTIONS:
        return run_arbitrary(action, args, resource=resource, backend=backend, **options)
    if action == "trigger":
        return visa.send_bus_trigger(resource, backend, **options)
    if action == "output":
        return visa.set_output(
            resource,
            "on" if step.enables_output else "off",
            backend,
            channel=args["channel"],
            **options,
        )
    if action in _READ_OPERATIONS:
        return _READ_OPERATIONS[action](resource, backend, **args, **options)
    operation = (
        visa.configure_output if action == "configure-output" else _WAVEFORM_OPERATIONS[action][1]
    )
    return operation(
        resource, backend=backend, **_waveform_arguments(step, readback=True), **options
    )


def _step_summary(result: object, *, preview: bool) -> dict[str, object]:
    if isinstance(result, visa.ErrorQueueResult):
        return {
            "error_codes": [entry.code for entry in result.errors],
            "read_count": result.read_count,
            "empty_confirmed": result.empty_confirmed,
            "limit_reached": result.limit_reached,
        }
    values = result if isinstance(result, dict) else asdict(result)
    omitted = {"resource", "backend", "transport", "identity", "model", "canonical_model_id"}
    if not preview:
        omitted.add("commands")
    return {key: value for key, value in values.items() if key not in omitted}


def _check_output_off(session: visa.VisaSession, channel: int) -> None:
    try:
        response = session.query(f"OUTPut{channel}?")
    except Exception as exc:
        raise StatusQueryError(f"Could not read Channel {channel} output state.") from exc
    if visa._parse_status_output(response) != "off":
        raise WaveformVerificationError(f"Channel {channel} output must be confirmed OFF.")


def _cleanup(
    resource: str, backend: str | None, options: dict, channels: set[int], *, group: bool = False
) -> tuple[dict, bool]:
    cleanup = {"attempted_channels": sorted(channels), "confirmed_off_channels": [], "errors": []}
    interrupted = False
    if group and channels:
        try:
            run_dual_channel("output", {"channel": "all", "enabled": False},
                             resource=resource, backend=backend, **options)
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, KeyboardInterrupt)
            cause = exc if isinstance(exc, WavegenError) else VisaCleanupError(
                "Group OFF did not complete."
            ).attach_cleanup_errors(getattr(exc, "cleanup_errors", ()))
            for channel in cleanup["attempted_channels"]:
                cleanup["errors"].append({"channel": channel, **_error_summary(cause)})
    for channel in cleanup["attempted_channels"]:
        try:
            if not group:
                visa.set_output(resource, "off", backend, channel=channel, **options)
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
            cause = (
                exc
                if isinstance(exc, WavegenError)
                else VisaCleanupError("Output OFF did not complete.")
            )
            cleanup["errors"].append({"channel": channel, **_error_summary(cause)})
        try:
            visa._run_on_supported_instrument(
                resource,
                backend,
                lambda session, _: _check_output_off(session, channel),
                **options,
            )
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
            cause = (
                exc
                if isinstance(exc, WavegenError)
                else VisaCleanupError("Output OFF could not be confirmed.")
            )
            cleanup["errors"].append({"channel": channel, **_error_summary(cause)})
        else:
            cleanup["confirmed_off_channels"].append(channel)
    return cleanup, interrupted


def run_software_sequence(
    document: object,
    *,
    model: str | None = None,
    resource: str | None = None,
    backend: str | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: visa.ResourceManagerFactory | None = None,
    cancel_event: Event | None = None,
    _workflow_action: str | None = None,
) -> dict[str, object]:
    """Plan with a registered model, or execute against an explicit resource."""
    if (model is None) == (resource is None):
        raise WaveformParameterError(
            "Software sequences require either a planning model or a VISA resource."
        )
    if (
        support_policy_mode == SUPPORT_POLICY_MODE_VALIDATION
        and resource is not None
        and expected_model_id is None
    ):
        raise WaveformParameterError(
            "Software sequence live validation requires an exact expected model."
        )
    product_live = (
        resource is not None
        and support_policy_mode != SUPPORT_POLICY_MODE_VALIDATION
        and not isinstance(resource_manager_factory, SimulatedResourceManagerFactory)
    )
    sequence = validate_software_sequence(
        document, product_live=product_live, _workflow_action=_workflow_action
    )
    options = dict(
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
        resource_manager_factory=resource_manager_factory,
    )
    counts = _sequence_counts(len(sequence.steps), sequence.loop_count)
    if _workflow_action is not None:
        counts["action"] = _workflow_action
    try:
        _check_cancellation(cancel_event)
    except SoftwareSequenceCancelled as exc:
        raise SoftwareSequenceError(exc, counts) from exc
    if resource is not None:
        try:
            identity = visa.identify_instrument(resource, backend, **options).identity
        except WavegenError as exc:
            raise SoftwareSequenceError(exc, counts) from exc
        model = options["expected_model_id"] = identity.canonical_model_id
    model_info, capabilities = visa._require_hardware_free_model(model, "software sequence")
    all_channels = set(range(1, capabilities.channel_count + 1))
    dual_steps = [step for step in sequence.steps if is_dual_action(step.action, step.arguments) or uses_other_channel_modulation(step.arguments)]
    dual_writes = any(step.action != "dual-channel-status" for step in dual_steps)
    channels = {step.arguments["channel"] for step in sequence.steps
                if type(step.arguments.get("channel")) is int}
    if dual_steps:
        channels |= all_channels
    controlled_channels = {
        step.arguments["channel"]
        for step in sequence.steps
        if step.action in _CHANNEL_WRITE_ACTIONS and not is_dual_action(step.action, step.arguments)
    }
    guarded_channels = (
        all_channels
        if dual_writes or any(step.action == "trigger" for step in sequence.steps)
        else controlled_channels
    )
    details = {
        **counts,
        "model": model_info.canonical_model,
        "canonical_model_id": model,
        "channels": sorted(channels | guarded_channels),
        "total_wait_seconds": sum(
            step.arguments["seconds"] for step in sequence.steps if step.action == "wait"
        ) * sequence.loop_count,
    }
    plans = []
    for index, step in enumerate(sequence.steps, 1):
        try:
            _check_cancellation(cancel_event)
            if "channel" in step.arguments and not is_dual_action(step.action, step.arguments):
                visa._validate_channel(
                    step.arguments["channel"], capabilities, model_info.canonical_model
                )
            plan = _plan_step(step, model, product_live=product_live)
            if step.action == _FREQUENCY_LIST_ONCE:
                details["total_wait_seconds"] += plan["seconds"] * sequence.loop_count
        except WavegenError as exc:
            raise _step_failure(details, step.action, index, 1, exc) from exc
        if resource is None:
            plans.append(
                {"step": index, "action": step.action, **_step_summary(plan, preview=True)}
            )
    if resource is None:
        return dict(details, status="planned", executed=False, steps=plans, cleanup=None)

    if guarded_channels:

        def preflight(session: visa.VisaSession, context: visa.IdentificationResult) -> None:
            if controlled_channels and not dual_writes:
                # OFF-only sequences retain the pre-existing coupling/tracking
                # guard, without the new dependency restriction on other writes.
                check_dependencies = any(
                    step.action in _CHANNEL_WRITE_ACTIONS and not (
                        step.action == "output" and not step.arguments["enabled"]
                    ) for step in sequence.steps
                )
                visa._check_independent_channel_guard(
                    session, capabilities, context, check_cross_dependencies=check_dependencies,
                )
            if capabilities.channel_count > 1 and any(
                step.action in _PLAYBACK_ACTIONS for step in sequence.steps
            ):
                _require_sample_rate_uncoupled(session)
            for channel in sorted(guarded_channels):
                _check_output_off(session, channel)

        try:
            _check_cancellation(cancel_event)
            visa._run_on_supported_instrument(resource, backend, preflight, **options)
        except WavegenError as exc:
            raise SoftwareSequenceError(exc, details) from exc

    attempted = set(controlled_channels) if _workflow_action is not None else set()
    dual_write_attempted = False
    results = []
    failure = None
    loop, index, step = 1, 1, sequence.steps[0]
    try:
        for loop in range(1, sequence.loop_count + 1):
            for index, step in enumerate(sequence.steps, 1):
                _check_cancellation(cancel_event)
                if (is_dual_action(step.action, step.arguments) and step.action != "dual-channel-status"
                        or uses_other_channel_modulation(step.arguments)):
                    dual_write_attempted = True
                if step.enables_output:
                    if step.arguments["channel"] == "all":
                        attempted.update(all_channels)
                    else:
                        attempted.add(step.arguments["channel"])
                result = _execute_step(step, resource, backend, options, cancel_event)
                if len(results) < _MAX_RESULTS:
                    results.append(
                        {
                            "loop": loop,
                            "step": index,
                            "action": step.action,
                            **_step_summary(result, preview=False),
                        }
                    )
                details["completed_step_executions"] += 1
            details["completed_loops"] += 1
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            cause = SoftwareSequenceInterrupted("Software sequence interrupted.").attach_cleanup_errors(
                getattr(exc, "cleanup_errors", ())
            )
        elif isinstance(exc, WavegenError):
            cause = exc
        else:
            cause = WavegenError("Unexpected software sequence step failure.")
        failure = (step.action, index, loop, cause)
    finally:
        # Preflight confirmed both outputs OFF. A failed joint write can still
        # leave an unexpected output state even without an explicit ON step.
        group = dual_writes and capabilities.channel_count == 2 and (
            bool(attempted) or (failure is not None and dual_write_attempted)
        )
        cleanup, cleanup_interrupted = _cleanup(
            resource, backend, options, all_channels if group else attempted, group=group
        )
        details["cleanup"] = cleanup
    details["results"] = results
    step_error = _step_failure(details, *failure) if failure is not None else None
    if cleanup_interrupted and (
        step_error is None or not isinstance(step_error.cause, SoftwareSequenceInterrupted)
    ):
        message = "Software sequence interrupted during output cleanup."
        if step_error is None:
            raise SoftwareSequenceError(SoftwareSequenceInterrupted(message), details)
        cause = SoftwareSequenceInterrupted(
            f"{message} {step_error}", backend=step_error.backend, transport=step_error.transport
        ).attach_cleanup_errors(step_error.cleanup_errors)
        raise SoftwareSequenceError(cause, step_error.details) from step_error
    if step_error is not None:
        raise step_error
    if cleanup["errors"]:
        raise SoftwareSequenceError(
            VisaCleanupError("Software sequence output cleanup failed."), details
        )
    return dict(details, status="completed", executed=True)
