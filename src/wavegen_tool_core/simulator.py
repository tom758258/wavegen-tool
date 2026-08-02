"""Minimal stateful Keysight 33521B simulator."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
import math


SIMULATED_33521B_RESOURCE = "USB0::SIM::33521B::INSTR"
SIMULATED_33521B_IDN = "KEYSIGHT TECHNOLOGIES,33521B,SIM000001,1.0"


@dataclass
class Simulated33521BState:
    """Process-local Channel 1 state for one simulated 33521B environment."""

    output_enabled: bool = False
    output_load: str = "50"
    voltage_unit: str = "VPP"
    active_function: str = "SIN"
    frequency_hz: float = 1000.0
    phase_deg: float = 0.0
    amplitude_vpp: float = 0.1
    offset_v: float = 0.0
    square_duty_cycle_percent: float = 50.0
    ramp_symmetry_percent: float = 100.0
    pulse_width_s: float = 0.0001
    pulse_edge_time_s: float = 1e-8
    noise_bandwidth_hz: float = 100000.0
    prbs_bit_rate_bps: float = 1000000.0
    prbs_pattern: str = "PN7"
    prbs_edge_time_s: float = 8.4e-9
    error_queue: list[str] = dataclass_field(default_factory=list)


class SimulatedResourceManager:
    """Small ResourceManager facade backed by one shared simulator state."""

    def __init__(self, state: Simulated33521BState | None = None) -> None:
        self.state = state or Simulated33521BState()
        self.closed = False

    def list_resources(self) -> tuple[str, ...]:
        self._ensure_open()
        return (SIMULATED_33521B_RESOURCE,)

    def open_resource(
        self,
        resource_name: str,
        **kwargs: object,
    ) -> SimulatedResource:
        self._ensure_open()
        if resource_name != SIMULATED_33521B_RESOURCE:
            raise ValueError("Unsupported simulated VISA resource.")
        return SimulatedResource(self.state)

    def close(self) -> None:
        self.closed = True

    def _ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError("Simulated ResourceManager is closed.")


class SimulatedResource:
    """Minimum PyVISA-like session surface used by Wavegen Tool Core."""

    def __init__(self, state: Simulated33521BState) -> None:
        self.state = state
        self.timeout = 5000
        self.baud_rate = 9600
        self.read_termination: str | None = None
        self.write_termination: str | None = None
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.closed = False

    def write(self, command: str) -> int:
        self._ensure_open()
        self._apply_write(command)
        self.writes.append(command)
        return len(command)

    def query(self, command: str) -> str:
        self._ensure_open()
        response = self._query_response(command)
        self.queries.append(command)
        return response

    def close(self) -> None:
        self.closed = True

    def _ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError("Simulated VISA session is closed.")

    def _apply_write(self, command: str) -> None:
        if command in {
            "SOURce1:FUNCtion:PULSe:HOLD WIDTh",
            "UNIT:ANGLe DEGree",
        }:
            return
        exact_updates = {
            "OUTPut1 OFF": ("output_enabled", False),
            "OUTPut1 ON": ("output_enabled", True),
            "OUTPut1:LOAD 50": ("output_load", "50"),
            "OUTPut1:LOAD INF": ("output_load", "high-z"),
            "SOURce1:VOLTage:UNIT VPP": ("voltage_unit", "VPP"),
            "SOURce1:FREQuency MINimum": ("frequency_hz", 0.000001),
            "SOURce1:FUNCtion SIN": ("active_function", "SIN"),
            "SOURce1:FUNCtion SQUare": ("active_function", "SQUARE"),
            "SOURce1:FUNCtion RAMP": ("active_function", "RAMP"),
            "SOURce1:FUNCtion TRIangle": ("active_function", "TRIANGLE"),
            "SOURce1:FUNCtion PULSe": ("active_function", "PULSE"),
            "SOURce1:FUNCtion DC": ("active_function", "DC"),
            "SOURce1:FUNCtion NOISe": ("active_function", "NOISE"),
            "SOURce1:FUNCtion PRBS": ("active_function", "PRBS"),
            "SOURce1:FUNCtion:PULSe:TRANsition:BOTH MINimum": (
                "pulse_edge_time_s",
                8.4e-9,
            ),
            "SOURce1:FUNCtion:PULSe:WIDTh MINimum": (
                "pulse_width_s",
                16e-9,
            ),
        }
        update = exact_updates.get(command)
        if update is not None:
            setattr(self.state, update[0], update[1])
            return

        numeric_updates = (
            ("SOURce1:FREQuency ", "frequency_hz"),
            ("SOURce1:PHASe ", "phase_deg"),
            ("SOURce1:VOLTage:OFFSet ", "offset_v"),
            ("SOURce1:VOLTage ", "amplitude_vpp"),
            ("SOURce1:FUNCtion:SQUare:DCYCle ", "square_duty_cycle_percent"),
            ("SOURce1:FUNCtion:RAMP:SYMMetry ", "ramp_symmetry_percent"),
            ("SOURce1:FUNCtion:PULSe:WIDTh ", "pulse_width_s"),
            (
                "SOURce1:FUNCtion:PULSe:TRANsition:BOTH ",
                "pulse_edge_time_s",
            ),
            ("SOURce1:FUNCtion:NOISe:BANDwidth ", "noise_bandwidth_hz"),
            ("SOURce1:FUNCtion:PRBS:BRATe ", "prbs_bit_rate_bps"),
            (
                "SOURce1:FUNCtion:PRBS:TRANsition:BOTH ",
                "prbs_edge_time_s",
            ),
        )
        for prefix, field in numeric_updates:
            if command.startswith(prefix):
                setattr(self.state, field, _parse_finite_number(command[len(prefix) :]))
                return

        pattern_prefix = "SOURce1:FUNCtion:PRBS:DATA "
        if command.startswith(pattern_prefix):
            pattern = command[len(pattern_prefix) :]
            if pattern not in {"PN7", "PN9", "PN11", "PN15", "PN20", "PN23"}:
                raise ValueError("Unsupported simulated PRBS pattern.")
            self.state.prbs_pattern = pattern
            return

        raise ValueError("Unsupported simulated SCPI write.")

    def _query_response(self, command: str) -> str:
        responses = {
            "*IDN?": SIMULATED_33521B_IDN,
            "OUTPut1?": "1" if self.state.output_enabled else "0",
            "SOURce1:FUNCtion?": self.state.active_function,
            "SOURce1:FREQuency?": _format_number(self.state.frequency_hz),
            "SOURce1:PHASe?": _format_number(self.state.phase_deg),
            "SOURce1:FUNCtion:PULSe:TRANsition? MAXimum": "1e-6",
            "SOURce1:FUNCtion:PULSe:WIDTh?": _format_number(
                self.state.pulse_width_s
            ),
            "SOURce1:FUNCtion:PULSe:TRANsition?": _format_number(
                self.state.pulse_edge_time_s
            ),
            "SOURce1:FUNCtion:NOISe:BANDwidth?": _format_number(
                self.state.noise_bandwidth_hz
            ),
            "SOURce1:VOLTage:UNIT?": self.state.voltage_unit,
            "SOURce1:VOLTage?": _format_number(self.state.amplitude_vpp),
            "SOURce1:VOLTage:OFFSet?": _format_number(self.state.offset_v),
            "OUTPut1:LOAD?": (
                "9.9E37"
                if self.state.output_load == "high-z"
                else self.state.output_load
            ),
        }
        if command == "SYSTem:ERRor?":
            if self.state.error_queue:
                return self.state.error_queue.pop(0)
            return '+0,"No error"'
        try:
            return responses[command]
        except KeyError as exc:
            raise ValueError("Unsupported simulated SCPI query.") from exc


def _parse_finite_number(value: str) -> float:
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Malformed simulated SCPI number.") from exc
    if not math.isfinite(number):
        raise ValueError("Malformed simulated SCPI number.")
    return number


def _format_number(value: float) -> str:
    return format(value, ".15g")
