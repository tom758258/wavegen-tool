"""Build the basic workflows from the existing software sequence operations."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, localcontext
from threading import Event

from . import visa
from .errors import UnsupportedInstrumentError, WaveformParameterError
from .identity import SUPPORT_POLICY_MODE_PRODUCT, SUPPORT_POLICY_MODE_VALIDATION
from .simulator import SimulatedResourceManagerFactory
from .software_sequence import (
    SoftwareSequence,
    _AMPLITUDE_UPDATE,
    _FREQUENCY_LIST_ONCE,
    run_software_sequence,
    validate_software_sequence,
)

TIMED_OUTPUT = "run-timed-output"
AMPLITUDE_RAMP = "run-amplitude-ramp"
OUTPUT_CYCLE = "run-output-cycle"
WAVEFORM_SEQUENCE = "run-waveform-sequence"
FREQUENCY_STEP = "run-frequency-step"
QUICK_OUTPUT_PRESET = "apply-quick-output-preset"

# Required and optional arguments other than the shared channel.
WORKFLOW_ARGUMENTS = {
    TIMED_OUTPUT: (("waveform", "duration_s"), ()),
    AMPLITUDE_RAMP: (("start_vpp", "stop_vpp", "step_vpp", "dwell_s"), ("frequency_hz",)),
    OUTPUT_CYCLE: (("on_s", "off_s", "cycles"), ()),
    WAVEFORM_SEQUENCE: (("waveforms",), ()),
    FREQUENCY_STEP: (
        ("start_frequency_hz", "stop_frequency_hz", "frequency_step_hz", "dwell_s"),
        ("amplitude_vpp", "offset_v", "load"),
    ),
    QUICK_OUTPUT_PRESET: (("preset",), ()),
}
BASIC_WORKFLOW_COMMANDS = frozenset(WORKFLOW_ARGUMENTS)
QUICK_OUTPUT_PRESETS = ("sine", "square", "triangle")
_STATIC_WAVEFORMS = {
    "configure-sine", "configure-square", "configure-ramp", "configure-triangle",
    "configure-pulse", "configure-dc", "configure-noise", "configure-prbs",
}
# Configure, ON, and OFF consume three steps. N waits and N-1 amplitude
# updates bring the total to 2*N + 2, so 124 points use all 250 steps.
RAMP_MAX_POINTS = 124
RAMP_MAX_DWELL_S = 600


def _step(action: str, **arguments: object) -> dict[str, object]:
    return {"action": action, "arguments": arguments}


def _number(value: object, name: str) -> Decimal:
    if type(value) not in (int, float, Decimal):
        raise WaveformParameterError(f"{name} must be a finite number.")
    number = visa._normalize_finite_number(value, name, waveform="Basic workflow")
    return Decimal(str(number))


def _positive(value: object, name: str) -> Decimal:
    number = _number(value, name)
    if number <= 0:
        raise WaveformParameterError(f"{name} must be positive.")
    return number


def _points(start: Decimal, stop: Decimal, step: Decimal, maximum: int) -> list[float]:
    if min(start, stop) <= 0:
        raise WaveformParameterError("Start and stop values must be positive.")
    points = [float(start)]
    with localcontext() as context:
        context.prec = 40
        direction = 1 if start < stop else -1
        current = start
        while current != stop:
            if len(points) == maximum:
                raise WaveformParameterError(f"The stepped range may contain at most {maximum} points.")
            following = current + direction * step
            current = min(following, stop) if direction > 0 else max(following, stop)
            point = float(current)
            if visa._format_scpi_number(point) == visa._format_scpi_number(points[-1]):
                raise WaveformParameterError("The step is too small to represent distinct points.")
            points.append(point)
    return points


def _waveform_step(waveform: object, channel: int) -> dict[str, object]:
    if not isinstance(waveform, Mapping) or waveform.keys() - {"action", "arguments"}:
        raise WaveformParameterError("waveform must contain a static configuration action and arguments.")
    action = waveform.get("action")
    arguments = waveform.get("arguments")
    if (
        not isinstance(action, str) or action not in _STATIC_WAVEFORMS
        or not isinstance(arguments, Mapping) or "channel" in arguments
    ):
        raise WaveformParameterError("Use a static waveform configuration with the outer workflow channel.")
    return {"action": action, "arguments": dict(arguments, channel=channel)}


def basic_workflow_document(action: str, arguments: Mapping[str, object]) -> dict[str, object]:
    """Expand the named workflow into a validated sequence document's shape."""
    if action not in BASIC_WORKFLOW_COMMANDS:
        raise WaveformParameterError("Unsupported basic workflow.")
    required, optional = WORKFLOW_ARGUMENTS[action]
    if not set(required) <= arguments.keys() or arguments.keys() - {*required, *optional, "channel"}:
        raise WaveformParameterError(f"{action} requires {', '.join(required)} and accepts only its documented options.")
    channel = visa._validate_channel(arguments.get("channel", 1))
    on = _step("output", channel=channel, enabled=True)
    off = _step("output", channel=channel, enabled=False)
    loops = 1
    if action == TIMED_OUTPUT:
        duration = _positive(arguments["duration_s"], "duration_s")
        steps = [_waveform_step(arguments["waveform"], channel), on, _step("wait", seconds=duration), off]
    elif action == OUTPUT_CYCLE:
        loops = arguments["cycles"]
        steps = [
            on, _step("wait", seconds=_positive(arguments["on_s"], "on_s")),
            off, _step("wait", seconds=_positive(arguments["off_s"], "off_s")),
        ]
    elif action == WAVEFORM_SEQUENCE:
        waveforms = arguments["waveforms"]
        if not isinstance(waveforms, list) or not 1 <= len(waveforms) <= 62:
            raise WaveformParameterError("waveforms must be a list of 1 to 62 timed waveforms.")
        steps = []
        for entry in waveforms:
            if not isinstance(entry, Mapping) or entry.keys() != {"waveform", "duration_s"}:
                raise WaveformParameterError("Each entry requires waveform and duration_s.")
            duration = _positive(entry["duration_s"], "duration_s")
            steps.extend([_waveform_step(entry["waveform"], channel), on, _step("wait", seconds=duration), off])
    elif action == AMPLITUDE_RAMP:
        points = _points(
            _number(arguments["start_vpp"], "start_vpp"),
            _number(arguments["stop_vpp"], "stop_vpp"),
            _positive(arguments["step_vpp"], "step_vpp"),
            RAMP_MAX_POINTS,
        )
        dwell = _positive(arguments["dwell_s"], "dwell_s")
        if dwell * len(points) > RAMP_MAX_DWELL_S:
            raise WaveformParameterError("Amplitude Ramp total dwell may not exceed 600 seconds.")
        sine = {"channel": channel, "frequency_hz": arguments.get("frequency_hz", 1000)}
        wait = _step("wait", seconds=dwell)
        steps = [_step("configure-sine", **sine, amplitude_vpp=points[0]), on, wait]
        for amplitude in points[1:]:
            steps.extend([_step(_AMPLITUDE_UPDATE, **sine, amplitude_vpp=amplitude), wait])
        steps.append(off)
    elif action == FREQUENCY_STEP:
        points = _points(
            _number(arguments["start_frequency_hz"], "start_frequency_hz"),
            _number(arguments["stop_frequency_hz"], "stop_frequency_hz"),
            _positive(arguments["frequency_step_hz"], "frequency_step_hz"),
            visa.FREQUENCY_LIST_MAX_POINTS,
        )
        dwell = _positive(arguments["dwell_s"], "dwell_s")
        steps = [
            _step(
                _FREQUENCY_LIST_ONCE, channel=channel, frequencies_hz=points,
                dwell_s=dwell, amplitude_vpp=arguments.get("amplitude_vpp", 0.1),
                offset_v=arguments.get("offset_v", 0), load=arguments.get("load", "50"),
            ),
        ]
    else:
        preset = arguments["preset"]
        if preset not in QUICK_OUTPUT_PRESETS:
            raise WaveformParameterError("Select the sine, square, or triangle Quick Output Preset.")
        settings = {"frequency_hz": 1000, "amplitude_vpp": 0.1, "offset_v": 0, "load": "high-z"}
        if preset == "square":
            settings["duty_cycle_percent"] = 50
        steps = [_step(f"configure-{preset}", channel=channel, **settings)]
    return {"version": 1, "loop_count": loops, "steps": steps}


def validate_basic_workflow(
    action: str, arguments: Mapping[str, object], *, product_live: bool = False,
) -> SoftwareSequence:
    """Validate the request document and Product Live feature boundary without I/O."""
    if action == AMPLITUDE_RAMP and product_live:
        raise UnsupportedInstrumentError("Amplitude Ramp is outside Product Live feature scope.")
    return validate_software_sequence(
        basic_workflow_document(action, arguments), product_live=product_live,
        _workflow_action=action,
    )


def run_basic_workflow(
    action: str,
    arguments: Mapping[str, object],
    *,
    model: str | None = None,
    resource: str | None = None,
    backend: str | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: visa.ResourceManagerFactory | None = None,
    cancel_event: Event | None = None,
) -> dict[str, object]:
    """Plan or run a named workflow with the standard sequence safety checks."""
    if (
        action == AMPLITUDE_RAMP and resource is not None
        and support_policy_mode != SUPPORT_POLICY_MODE_VALIDATION
        and not isinstance(resource_manager_factory, SimulatedResourceManagerFactory)
    ):
        raise UnsupportedInstrumentError("Amplitude Ramp is outside Product Live feature scope.")
    result = run_software_sequence(
        basic_workflow_document(action, arguments), model=model, resource=resource,
        backend=backend, support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id, resource_manager_factory=resource_manager_factory,
        cancel_event=cancel_event, _workflow_action=action,
    )
    return dict(result, channel=arguments.get("channel", 1))
