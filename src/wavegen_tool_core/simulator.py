"""Minimal stateful Keysight 33521B and multi-channel simulator."""

from __future__ import annotations

from dataclasses import dataclass
import math

from wavegen_tool_core.capabilities import capabilities_for_model_id
from wavegen_tool_core.identity import (
    CANONICAL_MODEL_ID,
    ModelInfo,
    model_info_for_model_id,
)


SIMULATED_33521B_RESOURCE = "USB0::SIM::33521B::INSTR"
SIMULATED_33521B_IDN = "KEYSIGHT TECHNOLOGIES,33521B,SIM000001,1.0"


@dataclass
class SimulatedChannelState:
    """Channel-specific waveform state for simulated instruments."""

    output_enabled: bool = False
    output_load: str = "50"
    voltage_unit: str = "VPP"
    active_function: str = "SIN"
    frequency_hz: float = 1000.0
    frequency_mode: str = "CW"
    sweep_start_frequency_hz: float = 1000.0
    sweep_stop_frequency_hz: float = 10000.0
    sweep_spacing: str = "linear"
    sweep_time_s: float = 1.0
    sweep_hold_time_s: float = 0.0
    sweep_return_time_s: float = 0.0
    trigger_source: str = "immediate"
    phase_deg: float = 0.0
    amplitude_vpp: float = 0.1
    offset_v: float = 0.0
    square_duty_cycle_percent: float = 50.0
    ramp_symmetry_percent: float = 100.0
    pulse_width_s: float = 0.0001
    pulse_edge_time_s: float | None = 1e-8
    pulse_leading_edge_s: float = 1e-8
    pulse_trailing_edge_s: float = 1e-8
    noise_bandwidth_hz: float = 100000.0
    prbs_bit_rate_bps: float = 1000000.0
    prbs_pattern: str = "PN7"
    prbs_edge_time_s: float = 8.4e-9
    am_enabled: bool = False
    am_type: str = "normal"
    am_source: str = "internal"
    am_internal_function: str = "sine"
    am_internal_frequency_hz: float = 100.0
    am_depth_percent: float = 100.0
    fm_enabled: bool = False
    fm_source: str = "internal"
    fm_internal_function: str = "sine"
    fm_internal_frequency_hz: float = 10.0
    fm_deviation_hz: float = 100.0
    pm_enabled: bool = False
    pm_source: str = "internal"
    pm_internal_function: str = "sine"
    pm_internal_frequency_hz: float = 10.0
    pm_deviation_deg: float = 180.0
    fsk_enabled: bool = False
    fsk_source: str = "internal"
    fsk_hop_frequency_hz: float = 100.0
    fsk_rate_hz: float = 10.0
    bpsk_enabled: bool = False
    bpsk_source: str = "internal"
    bpsk_phase_shift_deg: float = 180.0
    bpsk_rate_hz: float = 10.0


class Simulated33521BState:
    """Process-local state for registered simulated models (CH1 & CH2)."""

    def __init__(
        self,
        output_enabled: bool = False,
        output_load: str = "50",
        voltage_unit: str = "VPP",
        active_function: str = "SIN",
        frequency_hz: float = 1000.0,
        frequency_mode: str = "CW",
        sweep_start_frequency_hz: float = 1000.0,
        sweep_stop_frequency_hz: float = 10000.0,
        sweep_spacing: str = "linear",
        sweep_time_s: float = 1.0,
        sweep_hold_time_s: float = 0.0,
        sweep_return_time_s: float = 0.0,
        trigger_source: str = "immediate",
        phase_deg: float = 0.0,
        amplitude_vpp: float = 0.1,
        offset_v: float = 0.0,
        square_duty_cycle_percent: float = 50.0,
        ramp_symmetry_percent: float = 100.0,
        pulse_width_s: float = 0.0001,
        pulse_edge_time_s: float | None = 1e-8,
        pulse_leading_edge_s: float = 1e-8,
        pulse_trailing_edge_s: float = 1e-8,
        noise_bandwidth_hz: float = 100000.0,
        prbs_bit_rate_bps: float = 1000000.0,
        prbs_pattern: str = "PN7",
        prbs_edge_time_s: float = 8.4e-9,
        frequency_coupling: bool = False,
        voltage_coupling: bool = False,
        tracking: str = "OFF",
        error_queue: list[str] | None = None,
        model_id: str = CANONICAL_MODEL_ID,
    ) -> None:
        self.channels = {
            1: SimulatedChannelState(
                output_enabled=output_enabled,
                output_load=output_load,
                voltage_unit=voltage_unit,
                active_function=active_function,
                frequency_hz=frequency_hz,
                frequency_mode=frequency_mode,
                sweep_start_frequency_hz=sweep_start_frequency_hz,
                sweep_stop_frequency_hz=sweep_stop_frequency_hz,
                sweep_spacing=sweep_spacing,
                sweep_time_s=sweep_time_s,
                sweep_hold_time_s=sweep_hold_time_s,
                sweep_return_time_s=sweep_return_time_s,
                trigger_source=trigger_source,
                phase_deg=phase_deg,
                amplitude_vpp=amplitude_vpp,
                offset_v=offset_v,
                square_duty_cycle_percent=square_duty_cycle_percent,
                ramp_symmetry_percent=ramp_symmetry_percent,
                pulse_width_s=pulse_width_s,
                pulse_edge_time_s=pulse_edge_time_s,
                pulse_leading_edge_s=pulse_leading_edge_s,
                pulse_trailing_edge_s=pulse_trailing_edge_s,
                noise_bandwidth_hz=noise_bandwidth_hz,
                prbs_bit_rate_bps=prbs_bit_rate_bps,
                prbs_pattern=prbs_pattern,
                prbs_edge_time_s=prbs_edge_time_s,
            ),
            2: SimulatedChannelState(),
        }
        self.frequency_coupling = frequency_coupling
        self.voltage_coupling = voltage_coupling
        self.tracking = tracking
        self.error_queue = error_queue if error_queue is not None else []
        self.model_id = model_id
        _require_model_info(self.model_id)

    @property
    def ch1(self) -> SimulatedChannelState:
        return self.channels[1]

    @property
    def ch2(self) -> SimulatedChannelState:
        return self.channels[2]

    @property
    def model_info(self) -> ModelInfo:
        return _require_model_info(self.model_id)

    @property
    def resource_name(self) -> str:
        return f"USB0::SIM::{self.model_info.canonical_model}::INSTR"

    @property
    def idn_response(self) -> str:
        return (
            "KEYSIGHT TECHNOLOGIES,"
            f"{self.model_info.canonical_model},SIM000001,1.0"
        )

    # Delegating properties for backward-compatible Channel 1 access:
    @property
    def output_enabled(self) -> bool:
        return self.ch1.output_enabled

    @output_enabled.setter
    def output_enabled(self, value: bool) -> None:
        self.ch1.output_enabled = value

    @property
    def output_load(self) -> str:
        return self.ch1.output_load

    @output_load.setter
    def output_load(self, value: str) -> None:
        self.ch1.output_load = value

    @property
    def voltage_unit(self) -> str:
        return self.ch1.voltage_unit

    @voltage_unit.setter
    def voltage_unit(self, value: str) -> None:
        self.ch1.voltage_unit = value

    @property
    def active_function(self) -> str:
        return self.ch1.active_function

    @active_function.setter
    def active_function(self, value: str) -> None:
        self.ch1.active_function = value

    @property
    def frequency_hz(self) -> float:
        return self.ch1.frequency_hz

    @frequency_hz.setter
    def frequency_hz(self, value: float) -> None:
        self.ch1.frequency_hz = value

    @property
    def frequency_mode(self) -> str:
        return self.ch1.frequency_mode

    @frequency_mode.setter
    def frequency_mode(self, value: str) -> None:
        self.ch1.frequency_mode = value

    @property
    def sweep_start_frequency_hz(self) -> float:
        return self.ch1.sweep_start_frequency_hz

    @sweep_start_frequency_hz.setter
    def sweep_start_frequency_hz(self, value: float) -> None:
        self.ch1.sweep_start_frequency_hz = value

    @property
    def sweep_stop_frequency_hz(self) -> float:
        return self.ch1.sweep_stop_frequency_hz

    @sweep_stop_frequency_hz.setter
    def sweep_stop_frequency_hz(self, value: float) -> None:
        self.ch1.sweep_stop_frequency_hz = value

    @property
    def sweep_spacing(self) -> str:
        return self.ch1.sweep_spacing

    @sweep_spacing.setter
    def sweep_spacing(self, value: str) -> None:
        self.ch1.sweep_spacing = value

    @property
    def sweep_time_s(self) -> float:
        return self.ch1.sweep_time_s

    @sweep_time_s.setter
    def sweep_time_s(self, value: float) -> None:
        self.ch1.sweep_time_s = value

    @property
    def sweep_hold_time_s(self) -> float:
        return self.ch1.sweep_hold_time_s

    @sweep_hold_time_s.setter
    def sweep_hold_time_s(self, value: float) -> None:
        self.ch1.sweep_hold_time_s = value

    @property
    def sweep_return_time_s(self) -> float:
        return self.ch1.sweep_return_time_s

    @sweep_return_time_s.setter
    def sweep_return_time_s(self, value: float) -> None:
        self.ch1.sweep_return_time_s = value

    @property
    def trigger_source(self) -> str:
        return self.ch1.trigger_source

    @trigger_source.setter
    def trigger_source(self, value: str) -> None:
        self.ch1.trigger_source = value

    @property
    def phase_deg(self) -> float:
        return self.ch1.phase_deg

    @phase_deg.setter
    def phase_deg(self, value: float) -> None:
        self.ch1.phase_deg = value

    @property
    def amplitude_vpp(self) -> float:
        return self.ch1.amplitude_vpp

    @amplitude_vpp.setter
    def amplitude_vpp(self, value: float) -> None:
        self.ch1.amplitude_vpp = value

    @property
    def offset_v(self) -> float:
        return self.ch1.offset_v

    @offset_v.setter
    def offset_v(self, value: float) -> None:
        self.ch1.offset_v = value

    @property
    def square_duty_cycle_percent(self) -> float:
        return self.ch1.square_duty_cycle_percent

    @square_duty_cycle_percent.setter
    def square_duty_cycle_percent(self, value: float) -> None:
        self.ch1.square_duty_cycle_percent = value

    @property
    def ramp_symmetry_percent(self) -> float:
        return self.ch1.ramp_symmetry_percent

    @ramp_symmetry_percent.setter
    def ramp_symmetry_percent(self, value: float) -> None:
        self.ch1.ramp_symmetry_percent = value

    @property
    def pulse_width_s(self) -> float:
        return self.ch1.pulse_width_s

    @pulse_width_s.setter
    def pulse_width_s(self, value: float) -> None:
        self.ch1.pulse_width_s = value

    @property
    def pulse_edge_time_s(self) -> float | None:
        return self.ch1.pulse_edge_time_s

    @pulse_edge_time_s.setter
    def pulse_edge_time_s(self, value: float | None) -> None:
        self.ch1.pulse_edge_time_s = value

    @property
    def pulse_leading_edge_s(self) -> float:
        return self.ch1.pulse_leading_edge_s

    @pulse_leading_edge_s.setter
    def pulse_leading_edge_s(self, value: float) -> None:
        self.ch1.pulse_leading_edge_s = value

    @property
    def pulse_trailing_edge_s(self) -> float:
        return self.ch1.pulse_trailing_edge_s

    @pulse_trailing_edge_s.setter
    def pulse_trailing_edge_s(self, value: float) -> None:
        self.ch1.pulse_trailing_edge_s = value

    @property
    def noise_bandwidth_hz(self) -> float:
        return self.ch1.noise_bandwidth_hz

    @noise_bandwidth_hz.setter
    def noise_bandwidth_hz(self, value: float) -> None:
        self.ch1.noise_bandwidth_hz = value

    @property
    def prbs_bit_rate_bps(self) -> float:
        return self.ch1.prbs_bit_rate_bps

    @prbs_bit_rate_bps.setter
    def prbs_bit_rate_bps(self, value: float) -> None:
        self.ch1.prbs_bit_rate_bps = value

    @property
    def prbs_pattern(self) -> str:
        return self.ch1.prbs_pattern

    @prbs_pattern.setter
    def prbs_pattern(self, value: str) -> None:
        self.ch1.prbs_pattern = value

    @property
    def prbs_edge_time_s(self) -> float:
        return self.ch1.prbs_edge_time_s

    @prbs_edge_time_s.setter
    def prbs_edge_time_s(self, value: float) -> None:
        self.ch1.prbs_edge_time_s = value


class SimulatedResourceManagerFactory:
    """Explicit hardware-free factory context for one simulator state."""

    def __init__(self, state: Simulated33521BState) -> None:
        self.state = state

    @property
    def model_id(self) -> str:
        return self.state.model_id

    @property
    def resource_name(self) -> str:
        return self.state.resource_name

    def __call__(self, _pyvisa_library: str) -> SimulatedResourceManager:
        return SimulatedResourceManager(self.state)


class SimulatedResourceManager:
    """Small ResourceManager facade backed by one shared simulator state."""

    def __init__(self, state: Simulated33521BState | None = None) -> None:
        self.state = state or Simulated33521BState()
        self.closed = False

    def list_resources(self) -> tuple[str, ...]:
        self._ensure_open()
        return (self.state.resource_name,)

    def open_resource(
        self,
        resource_name: str,
        **kwargs: object,
    ) -> SimulatedResource:
        self._ensure_open()
        if resource_name != self.state.resource_name:
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

    def _target_channel_and_state(
        self,
        command: str,
    ) -> tuple[int, SimulatedChannelState]:
        capabilities = capabilities_for_model_id(self.state.model_id)
        max_channels = capabilities.channel_count if capabilities else 1

        if "2" in command.split(":")[0] or "2" in command.split()[0]:
            ch = 2
        else:
            ch = 1

        if ch > max_channels:
            raise ValueError("Unsupported simulated SCPI command for model channel count.")
        return ch, self.state.channels[ch]

    def _apply_write(self, command: str) -> None:
        if command in {
            "SOURce1:FUNCtion:PULSe:HOLD WIDTh",
            "SOURce2:FUNCtion:PULSe:HOLD WIDTh",
            "UNIT:ANGLe DEGree",
        }:
            # Ensure channel 2 validation check if SOURce2
            if "SOURce2" in command:
                self._target_channel_and_state(command)
            return

        if command.startswith("SOURce1:FUNCtion:PULSe:TRANsition:BOTH MINimum"):
            ch_state = self.state.ch1
            ch_state.pulse_edge_time_s = 8.4e-9
            ch_state.pulse_leading_edge_s = 8.4e-9
            ch_state.pulse_trailing_edge_s = 8.4e-9
            return
        if command.startswith("SOURce2:FUNCtion:PULSe:TRANsition:BOTH MINimum"):
            _, ch_state = self._target_channel_and_state(command)
            ch_state.pulse_edge_time_s = 8.4e-9
            ch_state.pulse_leading_edge_s = 8.4e-9
            ch_state.pulse_trailing_edge_s = 8.4e-9
            return

        ch_num, ch_state = self._target_channel_and_state(command)
        prefix_ch = f"{ch_num}"

        if command == f"SOURce{prefix_ch}:AM:STATe ON":
            ch_state.am_enabled = True
            ch_state.fm_enabled = False
            ch_state.pm_enabled = False
            ch_state.fsk_enabled = False
            ch_state.bpsk_enabled = False
            return
        if command == f"SOURce{prefix_ch}:FM:STATe ON":
            ch_state.fm_enabled = True
            ch_state.am_enabled = False
            ch_state.pm_enabled = False
            ch_state.fsk_enabled = False
            ch_state.bpsk_enabled = False
            return
        if command == f"SOURce{prefix_ch}:PM:STATe ON":
            ch_state.pm_enabled = True
            ch_state.am_enabled = False
            ch_state.fm_enabled = False
            ch_state.fsk_enabled = False
            ch_state.bpsk_enabled = False
            return
        if command == f"SOURce{prefix_ch}:FSKey:STATe ON":
            ch_state.fsk_enabled = True
            ch_state.am_enabled = False
            ch_state.fm_enabled = False
            ch_state.pm_enabled = False
            ch_state.bpsk_enabled = False
            return
        if command == f"SOURce{prefix_ch}:BPSK:STATe ON":
            ch_state.bpsk_enabled = True
            ch_state.am_enabled = False
            ch_state.fm_enabled = False
            ch_state.pm_enabled = False
            ch_state.fsk_enabled = False
            return

        exact_updates = {
            f"OUTPut{prefix_ch} OFF": ("output_enabled", False),
            f"OUTPut{prefix_ch} ON": ("output_enabled", True),
            f"OUTPut{prefix_ch}:LOAD 50": ("output_load", "50"),
            f"OUTPut{prefix_ch}:LOAD INF": ("output_load", "high-z"),
            f"SOURce{prefix_ch}:VOLTage:UNIT VPP": ("voltage_unit", "VPP"),
            f"SOURce{prefix_ch}:FREQuency MINimum": ("frequency_hz", 0.000001),
            f"SOURce{prefix_ch}:FUNCtion SIN": ("active_function", "SIN"),
            f"SOURce{prefix_ch}:FUNCtion SQUare": ("active_function", "SQUARE"),
            f"SOURce{prefix_ch}:FUNCtion RAMP": ("active_function", "RAMP"),
            f"SOURce{prefix_ch}:FUNCtion TRIangle": ("active_function", "TRIANGLE"),
            f"SOURce{prefix_ch}:FUNCtion PULSe": ("active_function", "PULSE"),
            f"SOURce{prefix_ch}:FUNCtion DC": ("active_function", "DC"),
            f"SOURce{prefix_ch}:FUNCtion NOISe": ("active_function", "NOISE"),
            f"SOURce{prefix_ch}:FUNCtion PRBS": ("active_function", "PRBS"),
            f"TRIGger{prefix_ch}:SOURce IMMediate": ("trigger_source", "immediate"),
            f"SOURce{prefix_ch}:FREQuency:MODE CW": ("frequency_mode", "CW"),
            f"SOURce{prefix_ch}:FREQuency:MODE SWEep": ("frequency_mode", "SWEep"),
            f"SOURce{prefix_ch}:AM:STATe OFF": ("am_enabled", False),
            f"SOURce{prefix_ch}:AM:DSSC OFF": ("am_type", "normal"),
            f"SOURce{prefix_ch}:AM:DSSC ON": ("am_type", "dssc"),
            f"SOURce{prefix_ch}:AM:SOURce INTernal": ("am_source", "internal"),
            f"SOURce{prefix_ch}:AM:INTernal:FUNCtion SINusoid": (
                "am_internal_function",
                "sine",
            ),
            f"SOURce{prefix_ch}:FM:STATe OFF": ("fm_enabled", False),
            f"SOURce{prefix_ch}:FM:SOURce INTernal": ("fm_source", "internal"),
            f"SOURce{prefix_ch}:FM:INTernal:FUNCtion SINusoid": (
                "fm_internal_function",
                "sine",
            ),
            f"SOURce{prefix_ch}:PM:STATe OFF": ("pm_enabled", False),
            f"SOURce{prefix_ch}:PM:SOURce INTernal": ("pm_source", "internal"),
            f"SOURce{prefix_ch}:PM:INTernal:FUNCtion SINusoid": (
                "pm_internal_function",
                "sine",
            ),
            f"SOURce{prefix_ch}:FSKey:STATe OFF": ("fsk_enabled", False),
            f"SOURce{prefix_ch}:FSKey:SOURce INTernal": (
                "fsk_source",
                "internal",
            ),
            f"SOURce{prefix_ch}:BPSK:STATe OFF": ("bpsk_enabled", False),
            f"SOURce{prefix_ch}:BPSK:SOURce INTernal": (
                "bpsk_source",
                "internal",
            ),
            f"SOURce{prefix_ch}:SWEep:SPACing LINear": ("sweep_spacing", "linear"),
            f"SOURce{prefix_ch}:SWEep:SPACing LOGarithmic": (
                "sweep_spacing",
                "logarithmic",
            ),
            f"SOURce{prefix_ch}:FUNCtion:PULSe:WIDTh MINimum": (
                "pulse_width_s",
                16e-9,
            ),
        }
        update = exact_updates.get(command)
        if update is not None:
            setattr(ch_state, update[0], update[1])
            return

        edge_prefixes = (
            (
                f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition:LEADing ",
                "pulse_leading_edge_s",
            ),
            (
                f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition:TRAiling ",
                "pulse_trailing_edge_s",
            ),
        )
        for prefix, field in edge_prefixes:
            if command.startswith(prefix):
                ch_state.pulse_edge_time_s = None
                setattr(ch_state, field, _parse_finite_number(command[len(prefix) :]))
                return

        numeric_updates = (
            (f"SOURce{prefix_ch}:AM:INTernal:FREQuency ", "am_internal_frequency_hz"),
            (f"SOURce{prefix_ch}:AM:DEPTh ", "am_depth_percent"),
            (f"SOURce{prefix_ch}:FM:INTernal:FREQuency ", "fm_internal_frequency_hz"),
            (f"SOURce{prefix_ch}:FM:DEViation ", "fm_deviation_hz"),
            (f"SOURce{prefix_ch}:PM:INTernal:FREQuency ", "pm_internal_frequency_hz"),
            (f"SOURce{prefix_ch}:PM:DEViation ", "pm_deviation_deg"),
            (f"SOURce{prefix_ch}:FSKey:FREQuency ", "fsk_hop_frequency_hz"),
            (f"SOURce{prefix_ch}:FSKey:INTernal:RATE ", "fsk_rate_hz"),
            (f"SOURce{prefix_ch}:BPSK:PHASe ", "bpsk_phase_shift_deg"),
            (f"SOURce{prefix_ch}:BPSK:INTernal:RATE ", "bpsk_rate_hz"),
            (f"SOURce{prefix_ch}:FREQuency ", "frequency_hz"),
            (f"SOURce{prefix_ch}:FREQuency:STARt ", "sweep_start_frequency_hz"),
            (f"SOURce{prefix_ch}:FREQuency:STOP ", "sweep_stop_frequency_hz"),
            (f"SOURce{prefix_ch}:PHASe ", "phase_deg"),
            (f"SOURce{prefix_ch}:VOLTage:OFFSet ", "offset_v"),
            (f"SOURce{prefix_ch}:VOLTage ", "amplitude_vpp"),
            (f"SOURce{prefix_ch}:SWEep:TIME ", "sweep_time_s"),
            (f"SOURce{prefix_ch}:SWEep:HTIMe ", "sweep_hold_time_s"),
            (f"SOURce{prefix_ch}:SWEep:RTIMe ", "sweep_return_time_s"),
            (f"SOURce{prefix_ch}:FUNCtion:SQUare:DCYCle ", "square_duty_cycle_percent"),
            (f"SOURce{prefix_ch}:FUNCtion:RAMP:SYMMetry ", "ramp_symmetry_percent"),
            (f"SOURce{prefix_ch}:FUNCtion:PULSe:WIDTh ", "pulse_width_s"),
            (f"SOURce{prefix_ch}:FUNCtion:NOISe:BANDwidth ", "noise_bandwidth_hz"),
            (f"SOURce{prefix_ch}:FUNCtion:PRBS:BRATe ", "prbs_bit_rate_bps"),
            (
                f"SOURce{prefix_ch}:FUNCtion:PRBS:TRANsition:BOTH ",
                "prbs_edge_time_s",
            ),
        )
        for prefix, field in numeric_updates:
            if command.startswith(prefix):
                setattr(ch_state, field, _parse_finite_number(command[len(prefix) :]))
                return

        both_edge_prefix = f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition:BOTH "
        if command.startswith(both_edge_prefix):
            edge_time = _parse_finite_number(command[len(both_edge_prefix) :])
            ch_state.pulse_edge_time_s = edge_time
            ch_state.pulse_leading_edge_s = edge_time
            ch_state.pulse_trailing_edge_s = edge_time
            return

        pattern_prefix = f"SOURce{prefix_ch}:FUNCtion:PRBS:DATA "
        if command.startswith(pattern_prefix):
            pattern = command[len(pattern_prefix) :]
            if pattern not in {"PN7", "PN9", "PN11", "PN15", "PN20", "PN23"}:
                raise ValueError("Unsupported simulated PRBS pattern.")
            ch_state.prbs_pattern = pattern
            return

        raise ValueError("Unsupported simulated SCPI write.")

    def _query_response(self, command: str) -> str:
        if command == "*IDN?":
            return self.state.idn_response

        if command == "SYSTem:ERRor?":
            if self.state.error_queue:
                return self.state.error_queue.pop(0)
            return '+0,"No error"'

        # Handle coupling & tracking queries:
        if command in {
            "SOURce1:FREQuency:COUPle:STATe?",
            "SOURce1:FREQuency:COUPle?",
            "FREQuency:COUPle:STATe?",
            "FREQuency:COUPle?",
        }:
            return "1" if self.state.frequency_coupling else "0"
        if command in {
            "SOURce1:VOLTage:COUPle:STATe?",
            "SOURce1:VOLTage:COUPle?",
            "VOLTage:COUPle:STATe?",
            "VOLTage:COUPle?",
        }:
            return "1" if self.state.voltage_coupling else "0"
        if command in {
            "SOURce1:TRACk?",
            "SOURce2:TRACk?",
            "TRACk?",
        }:
            if command == "SOURce2:TRACk?":
                self._target_channel_and_state(command)
            return self.state.tracking

        ch_num, ch_state = self._target_channel_and_state(command)
        prefix_ch = f"{ch_num}"

        responses = {
            f"OUTPut{prefix_ch}?": "1" if ch_state.output_enabled else "0",
            f"SOURce{prefix_ch}:FUNCtion?": ch_state.active_function,
            f"SOURce{prefix_ch}:FREQuency?": _format_number(ch_state.frequency_hz),
            f"SOURce{prefix_ch}:FREQuency:MODE?": ch_state.frequency_mode,
            f"SOURce{prefix_ch}:FREQuency:STARt?": _format_number(
                ch_state.sweep_start_frequency_hz
            ),
            f"SOURce{prefix_ch}:FREQuency:STOP?": _format_number(
                ch_state.sweep_stop_frequency_hz
            ),
            f"SOURce{prefix_ch}:SWEep:SPACing?": ch_state.sweep_spacing,
            f"SOURce{prefix_ch}:SWEep:TIME?": _format_number(ch_state.sweep_time_s),
            f"SOURce{prefix_ch}:SWEep:HTIMe?": _format_number(ch_state.sweep_hold_time_s),
            f"SOURce{prefix_ch}:SWEep:RTIMe?": _format_number(ch_state.sweep_return_time_s),
            f"TRIGger{prefix_ch}:SOURce?": ch_state.trigger_source,
            f"SOURce{prefix_ch}:PHASe?": _format_number(ch_state.phase_deg),
            f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition? MAXimum": "1e-6",
            f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition:LEADing? MAXimum": "1e-6",
            f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition:TRAiling? MAXimum": "1e-6",
            f"SOURce{prefix_ch}:FUNCtion:PULSe:WIDTh?": _format_number(
                ch_state.pulse_width_s
            ),
            f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition?": _format_number(
                ch_state.pulse_edge_time_s
                if ch_state.pulse_edge_time_s is not None
                else ch_state.pulse_leading_edge_s
            ),
            f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition:LEADing?": _format_number(
                ch_state.pulse_leading_edge_s
            ),
            f"SOURce{prefix_ch}:FUNCtion:PULSe:TRANsition:TRAiling?": _format_number(
                ch_state.pulse_trailing_edge_s
            ),
            f"SOURce{prefix_ch}:FUNCtion:NOISe:BANDwidth?": _format_number(
                ch_state.noise_bandwidth_hz
            ),
            f"SOURce{prefix_ch}:FUNCtion:PRBS:BRATe?": _format_number(
                ch_state.prbs_bit_rate_bps
            ),
            f"SOURce{prefix_ch}:VOLTage:UNIT?": ch_state.voltage_unit,
            f"SOURce{prefix_ch}:VOLTage?": _format_number(ch_state.amplitude_vpp),
            f"SOURce{prefix_ch}:VOLTage:OFFSet?": _format_number(ch_state.offset_v),
            f"OUTPut{prefix_ch}:LOAD?": (
                "9.9E37"
                if ch_state.output_load == "high-z"
                else ch_state.output_load
            ),
            f"SOURce{prefix_ch}:FM:STATe?": "1" if ch_state.fm_enabled else "0",
            f"SOURce{prefix_ch}:FM:SOURce?": ch_state.fm_source,
            f"SOURce{prefix_ch}:FM:INTernal:FUNCtion?": ch_state.fm_internal_function,
            f"SOURce{prefix_ch}:FM:INTernal:FREQuency?": _format_number(
                ch_state.fm_internal_frequency_hz
            ),
            f"SOURce{prefix_ch}:FM:DEViation?": _format_number(
                ch_state.fm_deviation_hz
            ),
            f"SOURce{prefix_ch}:PM:STATe?": "1" if ch_state.pm_enabled else "0",
            f"SOURce{prefix_ch}:PM:SOURce?": ch_state.pm_source,
            f"SOURce{prefix_ch}:PM:INTernal:FUNCtion?": ch_state.pm_internal_function,
            f"SOURce{prefix_ch}:PM:INTernal:FREQuency?": _format_number(
                ch_state.pm_internal_frequency_hz
            ),
            f"SOURce{prefix_ch}:PM:DEViation?": _format_number(
                ch_state.pm_deviation_deg
            ),
            f"SOURce{prefix_ch}:FSKey:STATe?": "1" if ch_state.fsk_enabled else "0",
            f"SOURce{prefix_ch}:FSKey:SOURce?": ch_state.fsk_source,
            f"SOURce{prefix_ch}:FSKey:FREQuency?": _format_number(
                ch_state.fsk_hop_frequency_hz
            ),
            f"SOURce{prefix_ch}:FSKey:INTernal:RATE?": _format_number(
                ch_state.fsk_rate_hz
            ),
            f"SOURce{prefix_ch}:BPSK:STATe?": "1" if ch_state.bpsk_enabled else "0",
            f"SOURce{prefix_ch}:BPSK:SOURce?": ch_state.bpsk_source,
            f"SOURce{prefix_ch}:BPSK:PHASe?": _format_number(
                ch_state.bpsk_phase_shift_deg
            ),
            f"SOURce{prefix_ch}:BPSK:INTernal:RATE?": _format_number(
                ch_state.bpsk_rate_hz
            ),
        }
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


def _require_model_info(model_id: str) -> ModelInfo:
    model_info = model_info_for_model_id(model_id)
    if model_info is None:
        raise ValueError(f"Unsupported simulated model ID {model_id!r}.")
    return model_info
