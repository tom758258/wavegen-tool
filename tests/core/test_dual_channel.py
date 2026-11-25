"""Hardware-free behavior and safety checks for explicit dual-channel control."""

import pytest

from wavegen_tool_core import Simulated33521BState, run_dual_channel, visa
from wavegen_tool_core.errors import (
    UnsupportedInstrumentError, WaveformParameterError, WaveformVerificationError,
    WavegenError,
)
from wavegen_tool_core.identity import SUPPORT_POLICY_MODE_VALIDATION
from wavegen_tool_core.simulator import (
    SimulatedResource, SimulatedResourceManager, SimulatedResourceManagerFactory,
)
from wavegen_tool_core.software_sequence import run_software_sequence


MODEL = "keysight-33512b"


@pytest.fixture
def joint():
    state = Simulated33521BState(model_id=MODEL)
    options = dict(resource=state.resource_name,
                   resource_manager_factory=SimulatedResourceManagerFactory(state))

    def run(action, **arguments):
        return run_dual_channel(action, arguments, **options)

    return state, options, run


def test_phase_sync_single_and_dual_preserves_requested_phase(joint):
    state, options, run = joint
    visa.configure_sine(frequency_hz=1000, amplitude_vpp=0.1, channel=2,
                        phase_deg=90, **options)
    result = run("phase-sync")
    assert result["phase_settings_preserved"]
    assert state.phase_sync_count == 1
    assert state.ch2.phase_deg == 90 and state.ch1.phase_deg == 0
    assert result["channels"] == [1, 2]
    single = Simulated33521BState()
    result = run_dual_channel("phase-sync", {}, resource=single.resource_name,
                              resource_manager_factory=SimulatedResourceManagerFactory(single))
    assert result["channels"] == [1] and single.phase_sync_count == 1
    dual = Simulated33521BState(model_id="keysight-33510b")
    result = run_dual_channel("phase-sync", {}, resource=dual.resource_name,
                              resource_manager_factory=SimulatedResourceManagerFactory(dual))
    assert result["channels"] == [1, 2] and dual.phase_sync_count == 1


@pytest.mark.parametrize("model", ["keysight-33510b", MODEL])
def test_group_plan_admitted_only_on_dual_channel_models(model):
    plan = run_dual_channel("output", {"channel": "all", "enabled": True}, model=model)
    assert plan["commands"] == ["OUTPut1 ON", "OUTPut2 ON"]
    assert not plan["executed"]
    with pytest.raises(WaveformParameterError):
        run_dual_channel("output", {"channel": "all", "enabled": True}, model="keysight-33521b")
    with pytest.raises(UnsupportedInstrumentError):
        run_dual_channel("output", {"channel": "all", "enabled": True},
                         resource="USB0::0x0000::0x0000::FAKE::INSTR",
                         resource_manager_factory=lambda _: pytest.fail("Product Live opened VISA"))


def test_frequency_coupling_ratio_offset_and_independent_guard(joint):
    state, options, run = joint
    run("configure-frequency-coupling", state="on", mode="ratio", ratio=2)
    assert state.ch2.frequency_hz == 2000
    run("update-coupled-frequency", channel=2, frequency_hz=4000)
    assert state.ch1.frequency_hz == 2000
    with pytest.raises(WavegenError):
        visa.configure_sine(frequency_hz=3000, amplitude_vpp=0.1, **options)
    with pytest.raises(WavegenError):
        run("configure-tracking", mode="on")
    with pytest.raises(WavegenError):
        run("update-coupled-frequency", channel=1, frequency_hz=20e6)
    assert state.ch1.frequency_hz == 2000
    run("configure-frequency-coupling", state="off")
    run("configure-frequency-coupling", state="on", mode="offset", offset_hz=500,
        reference_channel=2)
    assert state.ch1.frequency_hz == 4500
    run("update-coupled-frequency", channel=2, frequency_hz=1000)
    assert state.ch1.frequency_hz == 1500
    run("configure-frequency-coupling", state="off")
    visa.configure_sine(frequency_hz=3000, amplitude_vpp=0.1, **options)
    assert state.ch1.frequency_hz == 3000 and state.ch2.frequency_hz == 1000


def test_voltage_coupling_group_off_and_tracking(joint):
    state, options, run = joint
    run("configure-voltage-coupling", state="on")
    run("update-coupled-voltage", channel=2, amplitude_vpp=0.2, offset_v=0.05)
    assert state.ch1.amplitude_vpp == pytest.approx(0.2)
    assert state.ch2.offset_v == pytest.approx(0.05)
    assert run("output", channel="all", enabled=True)["output_states"] == {"1": "on", "2": "on"}
    with pytest.raises(WavegenError):
        run("update-coupled-voltage", amplitude_vpp=0.3, offset_v=0)
    run("output", channel="all", enabled=False)
    assert state.voltage_coupling
    run("configure-voltage-coupling", state="off")
    run("configure-tracking", mode="invert")
    run("configure-tracked-waveform", waveform="sine",
        arguments={"channel": 1, "frequency_hz": 2000, "amplitude_vpp": 0.3, "offset_v": 0.1})
    assert state.ch1.frequency_hz == state.ch2.frequency_hz == 2000
    assert state.ch1.offset_v == state.ch2.offset_v == pytest.approx(0.1)
    assert state.ch1.output_polarity != state.ch2.output_polarity
    result = run("configure-tracked-waveform", waveform="pulse", arguments={
        "channel": 2, "frequency_hz": 2000, "amplitude_vpp": 0.3,
        "pulse_width_s": 0.0001, "edge_time_s": 20e-9,
    })
    assert result["readback_verified"]
    assert state.ch1.pulse_width_s == state.ch2.pulse_width_s == 0.0001
    assert state.ch1.pulse_edge_time_s == state.ch2.pulse_edge_time_s == pytest.approx(20e-9)
    state.ch2.pulse_width_s = 0.00015
    with pytest.raises(WaveformVerificationError):
        run("output", channel="all", enabled=True)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    run("configure-tracking", mode="off")
    assert state.tracking == "OFF"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


def test_combine_routes_and_safety(joint):
    state, options, run = joint
    for receiver in (1, 2):
        source = 3 - receiver
        run("configure-combine", channel=receiver, source=f"ch{source}")
        assert state.channels[receiver].combine_source == f"CH{source}"
        with pytest.raises(WavegenError):
            run("configure-combine", channel=source, source=f"ch{receiver}")
        with pytest.raises(WavegenError):
            visa.configure_sine(frequency_hz=2000, amplitude_vpp=0.1, channel=source, **options)
        run("output", channel="all", enabled=True)
        run("output", channel="all", enabled=False)
        run("configure-combine", channel=receiver, source="none")
    assert all(ch.combine_source == "NONE" for ch in state.channels.values())


@pytest.mark.parametrize("kind,config", [
    ("am", visa.AMConfig(depth_percent=40, source="ch2")),
    ("fm", visa.FMConfig(deviation_hz=50, source="ch2")),
    ("pm", visa.PMConfig(deviation_deg=30, source="ch2")),
    ("sum", visa.SumConfig(amplitude_percent=20, source="ch2")),
    ("pwm", visa.PWMConfig(deviation_s=1e-6, source="ch2")),
])
def test_other_channel_modulation_dry_run_simulate_and_readback(joint, kind, config):
    state, options, run = joint
    visa.configure_sine(frequency_hz=100, amplitude_vpp=0.1, channel=2, **options)
    args = dict(frequency_hz=10000, amplitude_vpp=0.1, channel=1, **{kind: config})
    if kind == "pwm":
        args.update(pulse_width_s=25e-6, edge_time_s=10e-9)
        plan = visa.dry_run_pulse(MODEL, **args)
        configure = visa.configure_pulse
    else:
        plan = visa.dry_run_sine(MODEL, **args)
        configure = visa.configure_sine
    assert f"SOURce1:{kind.upper()}:SOURce CH2" in plan.commands
    assert not any(f"{kind.upper()}:INTernal:" in cmd for cmd in plan.commands)
    configure(**args, **options)
    assert getattr(state.ch1, f"{kind}_source") == "ch2"
    if kind == "pwm":
        original_width = state.ch1.pulse_width_s
        state.ch1.pulse_width_s = 0.5e-6
        with pytest.raises(WaveformParameterError):
            run("output", channel="all", enabled=True)
        assert not state.ch1.output_enabled and not state.ch2.output_enabled
        state.ch1.pulse_width_s = original_width
    run("output", channel="all", enabled=True)
    run("output", channel="all", enabled=False)
    visa.configure_sine(frequency_hz=1000, amplitude_vpp=0.1, **options)
    with pytest.raises(WaveformParameterError):
        visa.dry_run_sine("keysight-33521b", frequency_hz=10000, amplitude_vpp=0.1,
                          am=visa.AMConfig(depth_percent=40, source="ch2"))


def test_software_sequence_preserves_coupling_and_cleans_both_outputs(joint):
    state, options, _ = joint
    def step(action, **arguments):
        return dict(action=action, arguments=arguments)
    document = {"version": 1, "steps": [
        step("configure-frequency-coupling", state="on", mode="ratio", ratio=2),
        step("output", channel="all", enabled=True),
    ]}
    result = run_software_sequence(document, **options)
    assert result["cleanup"]["confirmed_off_channels"] == [1, 2]
    assert state.frequency_coupling
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("failed_command", ["OUTPut2 ON", "OUTPut1 OFF"])
def test_group_output_failure_attempts_both_off(failed_command):
    state = Simulated33521BState(model_id=MODEL)
    writes = []

    class FaultyResource(SimulatedResource):
        def write(self, command):
            writes.append(command)
            if command == failed_command:
                raise OSError("injected failure")
            return super().write(command)

    class FaultyManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            return FaultyResource(state)

    enabling = failed_command.endswith("ON")
    if not enabling:
        state.ch1.output_enabled = state.ch2.output_enabled = True
    with pytest.raises(WavegenError):
        run_dual_channel("output", {"channel": "all", "enabled": enabling},
                         resource=state.resource_name, expected_model_id=MODEL,
                         support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
                         resource_manager_factory=lambda _: FaultyManager(state))
    assert "OUTPut1 OFF" in writes and "OUTPut2 OFF" in writes
    assert not state.ch2.output_enabled


def test_phase_sync_rejects_active_output(joint):
    state, _, run = joint
    state.ch2.output_enabled = True
    with pytest.raises(WaveformVerificationError):
        run("phase-sync")
    assert state.phase_sync_count == 0



def test_bus_trigger_routes_to_both_channels(joint):
    state, options, run = joint
    for channel in (1, 2):
        visa.configure_sine(frequency_hz=1000, amplitude_vpp=0.1, channel=channel,
                            burst=visa.CountedBurstConfig(count=5, trigger_source="bus"), **options)
    run("output", channel="all", enabled=True)
    try:
        visa.send_bus_trigger(**options)
        assert state.ch1.bus_triggers == state.ch2.bus_triggers == 1
    finally:
        run("output", channel="all", enabled=False)


def test_independent_off_keeps_original_guard(joint):
    state, options, run = joint
    run("configure-combine", channel=1, source="ch2")
    run("output", channel="all", enabled=True)
    visa.set_output(state.resource_name, "off", channel=1,
                    resource_manager_factory=options["resource_manager_factory"])
    assert not state.ch1.output_enabled and state.ch2.output_enabled
    with pytest.raises(WavegenError):
        visa.set_output(state.resource_name, "on", channel=1,
                        resource_manager_factory=options["resource_manager_factory"])
    run("output", channel="all", enabled=False)


def test_group_on_rejects_inconsistent_topology(joint):
    state, _, run = joint
    state.ch1.combine_source, state.ch2.combine_source = "CH2", "CH1"
    with pytest.raises(WaveformVerificationError):
        run("output", channel="all", enabled=True)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    with pytest.raises(WaveformVerificationError):
        run("configure-voltage-coupling", state="on")
    assert not state.voltage_coupling
    # Explicit release must remain usable even after rejecting the conflict.
    run("configure-combine", channel=1, source="none")
    assert state.ch1.combine_source == "NONE"


@pytest.mark.parametrize("channel", [1, 2])
def test_tracking_rejects_volatile_memory(joint, channel):
    state, _, run = joint
    state.channels[channel].arbitrary_points["existing"] = 16
    before = {ch: dict(state.channels[ch].arbitrary_points) for ch in (1, 2)}
    with pytest.raises(WaveformParameterError):
        run("configure-tracking", mode="on")
    assert state.tracking == "OFF"
    assert {ch: state.channels[ch].arbitrary_points for ch in (1, 2)} == before


def test_coupled_frequency_preserves_pm_frequency_ratio(joint, monkeypatch):
    state, options, run = joint
    visa.configure_sine(frequency_hz=100, amplitude_vpp=0.1, channel=2, **options)
    write = SimulatedResource.write

    def ignore_carrier(self, command):
        if command != "SOURce1:FREQuency 10000":
            return write(self, command)

    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "write", ignore_carrier)
        with pytest.raises(WaveformVerificationError):
            visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                                pm=visa.PMConfig(deviation_deg=30, source="ch2"), **options)
    assert state.ch1.frequency_hz == 1000
    with pytest.raises(WavegenError):
        run("output", channel="all", enabled=True)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                        pm=visa.PMConfig(deviation_deg=30, source="ch2"), **options)
    with pytest.raises(WaveformParameterError):
        run("configure-frequency-coupling", state="on", mode="ratio", ratio=1)
    assert not state.frequency_coupling
    assert state.ch1.frequency_hz == 10000 and state.ch2.frequency_hz == 100
    run("configure-frequency-coupling", state="on", mode="offset", offset_hz=-9900)
    with pytest.raises(WaveformParameterError):
        run("update-coupled-frequency", channel=2, frequency_hz=1000)
    assert state.frequency_coupling
    assert state.ch1.frequency_hz == 10000 and state.ch2.frequency_hz == 100


@pytest.mark.parametrize("kind,config,ignored", [
    ("am", visa.AMConfig(depth_percent=40, source="ch2"), "SOURce1:VOLTage 0.1"),
    ("fm", visa.FMConfig(deviation_hz=2000, source="ch2"), "SOURce1:FREQuency 10000"),
    ("sum", visa.SumConfig(amplitude_percent=20, source="ch2"), "SOURce1:VOLTage 0.1"),
])
def test_other_channel_modulation_rejects_ignored_carrier_write(joint, monkeypatch, kind, config, ignored):
    state, options, run = joint
    visa.configure_sine(frequency_hz=1000, amplitude_vpp=0.2, **options)
    visa.configure_sine(frequency_hz=100, amplitude_vpp=0.1, channel=2, **options)
    write = SimulatedResource.write

    def ignore_carrier(self, command):
        if command != ignored:
            return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", ignore_carrier)
    with pytest.raises(WaveformVerificationError):
        visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1, **{kind: config}, **options)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    if kind == "fm":
        assert state.ch1.frequency_hz == 1000
        with pytest.raises(WaveformParameterError):
            run("output", channel="all", enabled=True)
        assert not state.ch1.output_enabled and not state.ch2.output_enabled
    else:
        assert state.ch1.amplitude_vpp == pytest.approx(0.2)


@pytest.mark.parametrize("waveform, settings, ignored", [
    ("square", {"frequency_hz": 1000, "duty_cycle_percent": 30}, "SOURce1:FUNCtion SQUare"),
    ("square", {"frequency_hz": 1000, "duty_cycle_percent": 30}, "SOURce1:FUNCtion:SQUare:DCYCle 30"),
    ("prbs", {"bit_rate_bps": 1000, "pattern": "PN23"}, "SOURce1:FUNCtion:PRBS:BRATe 1000"),
    ("prbs", {"bit_rate_bps": 1000, "pattern": "PN23"}, "SOURce1:FUNCtion:PRBS:DATA PN23"),
    ("square", {"frequency_hz": 1000, "duty_cycle_percent": 30}, "tracking-copy"),
])
def test_tracking_rejects_ignored_waveform_write(waveform, settings, ignored):
    state = Simulated33521BState(model_id=MODEL)

    class FaultyResource(SimulatedResource):
        def write(self, command):
            if command != ignored:
                super().write(command)
            if ignored == "tracking-copy" and command == "SOURce1:TRACk ON":
                state.ch2.square_duty_cycle_percent = 50

    class FaultyManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            return FaultyResource(state)

    def run(action, **arguments):
        return run_dual_channel(action, arguments, resource=state.resource_name,
                                expected_model_id=MODEL,
                                support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
                                resource_manager_factory=lambda _: FaultyManager(state))

    if ignored == "tracking-copy":
        visa.configure_square(
            resource=state.resource_name, resource_manager_factory=SimulatedResourceManagerFactory(state),
            amplitude_vpp=0.1, **settings,
        )
        with pytest.raises(WaveformVerificationError):
            run("configure-tracking", mode="on")
    else:
        run("configure-tracking", mode="on")
        with pytest.raises(WaveformVerificationError):
            run("configure-tracked-waveform", waveform=waveform,
                arguments={"amplitude_vpp": 0.1, **settings})
    assert not state.ch1.output_enabled and not state.ch2.output_enabled



def test_dual_status_rejects_unknown_polarity():
    state = Simulated33521BState(model_id=MODEL)

    class FaultyResource(SimulatedResource):
        def query(self, command):
            if command == "OUTPut2:POLarity?":
                return "UNKNOWN"
            return super().query(command)

    class FaultyManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            return FaultyResource(state)

    with pytest.raises(WaveformVerificationError):
        run_dual_channel("dual-channel-status", {}, resource=state.resource_name,
                         expected_model_id=MODEL, support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
                         resource_manager_factory=lambda _: FaultyManager(state))


def test_voltage_coupling_copies_units_load_and_range_with_frequency_coupling(joint):
    state, _, run = joint
    state.ch1.output_load = "high-z"
    state.ch1.voltage_unit = "VRMS"
    state.ch1.autorange_enabled = False
    run("configure-frequency-coupling", state="on", mode="ratio", ratio=2)
    result = run("configure-voltage-coupling", state="on")
    assert result["frequency_coupling"]["enabled"]
    for ch in ("1", "2"):
        actual = result["channel_status"][ch]
        assert actual["load"] == "high-z"
        assert actual["amplitude_unit"] == "VRMS"
        assert actual["autorange_enabled"] is False
    result = run("update-coupled-voltage", amplitude_vpp=0.2, offset_v=0.05)
    assert result["voltage_coupling"] and result["frequency_coupling"]["enabled"]
    assert result["channel_status"]["2"]["amplitude_unit"] == "VPP"


def test_combine_rejects_excessive_output(joint):
    state, options, run = joint
    for ch in (1, 2):
        visa.configure_sine(frequency_hz=1000, amplitude_vpp=6, channel=ch, **options)
    with pytest.raises(WaveformParameterError):
        run("configure-combine", channel=1, source="ch2")
    assert state.ch1.combine_source == "NONE"


@pytest.mark.parametrize("enabled", [True, False])
def test_group_output_readback_failure_cleans_both_outputs(enabled):
    state = Simulated33521BState(model_id=MODEL)
    state.ch1.output_enabled = state.ch2.output_enabled = not enabled
    reads = 0

    class FaultyResource(SimulatedResource):
        def query(self, command):
            nonlocal reads
            if command == "OUTPut2?":
                reads += 1
                if enabled and state.ch2.output_enabled:
                    return "UNKNOWN"
                if not enabled and reads == 2:
                    return "1"
            return super().query(command)

    class FaultyManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            return FaultyResource(state)

    with pytest.raises(WaveformVerificationError):
        run_dual_channel("output", {"channel": "all", "enabled": enabled},
                         resource=state.resource_name, expected_model_id=MODEL,
                         support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
                         resource_manager_factory=lambda _: FaultyManager(state))
    assert not state.ch1.output_enabled and not state.ch2.output_enabled



def test_tracking_rejects_modulated_envelope_above_other_channel_limit(joint):
    state, _, run = joint
    run("configure-tracking", mode="on")
    state.ch2.voltage_limit_enabled = True
    state.ch2.voltage_limit_low, state.ch2.voltage_limit_high = -0.2, 0.2
    with pytest.raises(WaveformParameterError):
        run("configure-tracked-waveform", waveform="sine", arguments={
            "frequency_hz": 1000, "amplitude_vpp": 0.3,
            "am": {"modulation_frequency_hz": 100, "depth_percent": 100},
        })
    assert state.ch1.amplitude_vpp == state.ch2.amplitude_vpp == 0.1
    assert not state.ch1.am_enabled and not state.ch2.am_enabled
