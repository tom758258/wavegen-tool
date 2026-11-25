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
from wavegen_tool_core.software_sequence import SoftwareSequenceError, run_software_sequence


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
    # A 5 ns mismatch passed the generic tolerance but not Pulse's 100 ps.
    state.ch2.pulse_width_s = 0.000100005
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


@pytest.mark.parametrize("failed_command", [
    "OUTPut2 ON", "OUTPut1 OFF", "partial-on-off-failure", "cleanup-error-queue",
])
def test_group_output_failure_attempts_both_off(failed_command):
    state = Simulated33521BState(model_id=MODEL)
    writes = []

    class FaultyResource(SimulatedResource):
        def write(self, command):
            writes.append(command)
            if (
                command == failed_command
                or (failed_command in {"partial-on-off-failure", "cleanup-error-queue"}
                    and command == "OUTPut2 ON")
                or (failed_command == "partial-on-off-failure" and command == "OUTPut1 OFF")
            ):
                raise OSError("injected failure")
            result = super().write(command)
            if failed_command == "cleanup-error-queue" and command == "OUTPut2 OFF":
                state.error_queue.append('-221,"Settings conflict"')
            return result

    class FaultyManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            return FaultyResource(state)

    enabling = failed_command != "OUTPut1 OFF"
    if not enabling:
        state.ch1.output_enabled = state.ch2.output_enabled = True
    with pytest.raises(WavegenError) as caught:
        run_dual_channel("output", {"channel": "all", "enabled": enabling},
                         resource=state.resource_name, expected_model_id=MODEL,
                         support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
                         resource_manager_factory=lambda _: FaultyManager(state))
    assert "OUTPut1 OFF" in writes and "OUTPut2 OFF" in writes
    assert not state.ch2.output_enabled
    if failed_command == "OUTPut2 ON":
        assert caught.value.output_state == "off"
        assert not caught.value.cleanup_errors and not state.ch1.output_enabled
    else:
        assert caught.value.output_state is None
        assert caught.value.cleanup_errors
    if failed_command == "partial-on-off-failure":
        assert state.ch1.output_enabled
        assert any("Channel 1 OFF" in message for message in caught.value.cleanup_errors)
    if failed_command == "cleanup-error-queue":
        assert caught.value.code == "visa_write_error"
        assert any("-221" in message for message in caught.value.cleanup_errors)
        assert not state.error_queue and not state.ch1.output_enabled


@pytest.mark.parametrize("model,channel,mode,source,allowed", [
    ("keysight-33521b", 1, "am", "ch2", False),
    (MODEL, 1, "am", "ch1", False),
    (MODEL, 2, "am", "ch2", False),
    (MODEL, 1, "fsk", "ch2", False),
    (MODEL, 1, "bpsk", "ch2", False),
    (MODEL, 1, "am", "ch2", True),
    (MODEL, 2, "am", "ch1", True),
    ("keysight-33521b", 1, "am", "external", True),
])
def test_advanced_readback_source_matches_model_mode_and_channel(model, channel, mode, source, allowed):
    state = Simulated33521BState(model_id=model)
    selected = state.channels[channel]
    setattr(selected, mode + "_enabled", True)
    setattr(selected, mode + "_source", source)
    options = dict(resource=state.resource_name, channel=channel,
                   resource_manager_factory=SimulatedResourceManagerFactory(state))
    if allowed:
        assert visa.query_advanced_mode(**options).settings["source"] == source
    else:
        with pytest.raises(visa.StatusQueryError):
            visa.query_advanced_mode(**options)


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


@pytest.mark.parametrize("fault", ["FSKey:STATe", "BPSK:STATe", "BURSt:STATe", "FREQuency:MODE"])
def test_group_on_rejects_conflicting_advanced_modes(joint, monkeypatch, fault):
    state, options, run = joint
    visa.configure_sine(frequency_hz=100, amplitude_vpp=0.1, channel=2, **options)
    visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                        am=visa.AMConfig(depth_percent=40, source="ch2"), **options)
    query = SimulatedResource.query
    write = SimulatedResource.write
    writes = []

    def conflicting_mode(self, command):
        if command == f"SOURce1:{fault}?":
            return "SWE" if fault == "FREQuency:MODE" else "1"
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "query", conflicting_mode)
        patch.setattr(SimulatedResource, "write", record)
        with pytest.raises(visa.StatusQueryError):
            visa.query_advanced_mode(**options)
        with pytest.raises(WaveformVerificationError):
            run("output", channel="all", enabled=True)
        assert writes == []
        assert not state.ch1.output_enabled and not state.ch2.output_enabled
    # Normal single AM still passes; the existing dual Burst test covers normal Burst.
    run("output", channel="all", enabled=True)
    run("output", channel="all", enabled=False)


@pytest.mark.parametrize("reading", [-100, 0])
def test_other_channel_pm_rejects_invalid_source_frequency(joint, monkeypatch, reading):
    state, options, run = joint
    visa.configure_sine(frequency_hz=100, amplitude_vpp=0.1, channel=2, **options)
    carrier = dict(frequency_hz=10000, amplitude_vpp=0.1,
                   pm=visa.PMConfig(deviation_deg=30, source="ch2"))
    query = SimulatedResource.query
    write = SimulatedResource.write
    writes = []

    def invalid_frequency(self, command):
        if command == "SOURce2:FREQuency?":
            return str(reading)
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "query", invalid_frequency)
        patch.setattr(SimulatedResource, "write", record)
        with pytest.raises(WaveformVerificationError):
            visa.configure_sine(**carrier, **options)
        assert writes == []
    visa.configure_sine(**carrier, **options)
    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "query", invalid_frequency)
        patch.setattr(SimulatedResource, "write", record)
        with pytest.raises(WaveformVerificationError):
            run("output", channel="all", enabled=True)
        assert writes == []
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    run("output", channel="all", enabled=True)
    run("output", channel="all", enabled=False)


@pytest.mark.parametrize("reading", [-1, 361])
def test_joint_pm_rejects_invalid_deviation(joint, monkeypatch, reading):
    state, options, run = joint
    visa.configure_sine(frequency_hz=100, amplitude_vpp=0.1, channel=2, **options)
    visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                        pm=visa.PMConfig(deviation_deg=30, source="ch2"), **options)
    query = SimulatedResource.query
    write = SimulatedResource.write
    writes = []

    def invalid_deviation(self, command):
        if command == "SOURce1:PM:DEViation?":
            return str(reading)
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "query", invalid_deviation)
        patch.setattr(SimulatedResource, "write", record)
        with pytest.raises(WaveformParameterError):
            run("output", channel="all", enabled=True)
        with pytest.raises(WaveformParameterError):
            run("configure-frequency-coupling", state="on", mode="offset", offset_hz=-9900)
        assert writes == []
    run("configure-frequency-coupling", state="on", mode="offset", offset_hz=-9900)
    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "query", invalid_deviation)
        patch.setattr(SimulatedResource, "write", record)
        with pytest.raises(WaveformParameterError):
            run("update-coupled-frequency", channel=1, frequency_hz=10001)
        assert writes == []
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    run("output", channel="all", enabled=True)
    run("output", channel="all", enabled=False)


@pytest.mark.parametrize("kind", ["fm", "pm", "pwm"])
@pytest.mark.parametrize(
    ("shape", "frequency"),
    [("RAMP", 300000), ("TRI", 300000), ("NRAMP", 300000), ("UNKNOWN", 100000)],
)
def test_coupled_frequency_validates_internal_modulation_shape(joint, monkeypatch, kind, shape, frequency):
    state, options, run = joint
    if kind == "pwm":
        carrier = 1000
        visa.configure_pulse(
            frequency_hz=carrier, amplitude_vpp=0.1, pulse_width_s=0.0001,
            edge_time_s=1e-8,
            pwm=visa.PWMConfig(modulation_frequency_hz=100000, deviation_s=1e-6, shape="up-ramp"),
            **options,
        )
    else:
        carrier = 10000000
        config = (
            visa.FMConfig(modulation_frequency_hz=100000, deviation_hz=50, shape="up-ramp")
            if kind == "fm" else
            visa.PMConfig(modulation_frequency_hz=100000, deviation_deg=30, shape="up-ramp")
        )
        visa.configure_sine(frequency_hz=carrier, amplitude_vpp=0.1, **{kind: config}, **options)
    query = SimulatedResource.query
    write = SimulatedResource.write
    writes = []
    prefix = f"SOURce1:{kind.upper()}:INTernal:"

    def invalid_shape(self, command):
        if command == prefix + "FUNCtion?":
            return shape
        if command == prefix + "FREQuency?":
            return str(frequency)
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "query", invalid_shape)
        patch.setattr(SimulatedResource, "write", record)
        with pytest.raises(WavegenError):
            run("configure-frequency-coupling", state="on", mode="ratio", ratio=1)
        assert writes == []
        assert not state.frequency_coupling
    # The genuine supported non-sine configuration is a positive control.
    run("configure-frequency-coupling", state="on", mode="ratio", ratio=1)
    with monkeypatch.context() as patch:
        patch.setattr(SimulatedResource, "query", invalid_shape)
        patch.setattr(SimulatedResource, "write", record)
        with pytest.raises(WavegenError):
            run("update-coupled-frequency", channel=1, frequency_hz=carrier + 1)
        with pytest.raises(WavegenError):
            run("output", channel="all", enabled=True)
        assert writes == []
        assert state.frequency_coupling
        assert state.ch1.frequency_hz == state.ch2.frequency_hz == carrier
        assert not state.ch1.output_enabled and not state.ch2.output_enabled
    run("update-coupled-frequency", channel=1, frequency_hz=carrier + 1)
    run("output", channel="all", enabled=True)
    run("output", channel="all", enabled=False)


def test_other_channel_pm_allows_dc_source(joint):
    state, options, run = joint
    visa.configure_dc(voltage_v=0.1, channel=2, **options)
    visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                        pm=visa.PMConfig(deviation_deg=30, source="ch2"), **options)
    try:
        assert run("output", channel="all", enabled=True)["output_states"] == {"1": "on", "2": "on"}
    finally:
        run("output", channel="all", enabled=False)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


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



@pytest.mark.parametrize("advanced, edges, field, delta", [
    ({}, {"edge_time_s": 20e-9}, "pulse_width_s", 5e-9),
    ({"pwm": {"modulation_frequency_hz": 100, "deviation_s": 1e-6}},
     {"edge_time_s": 20e-9}, "frequency_hz", 0.005),
    ({"burst": {"mode": "counted", "count": 5, "trigger_source": "bus"}},
     {"leading_edge_s": 20e-9, "trailing_edge_s": 30e-9}, "pulse_trailing_edge_s", 0.5e-9),
    ({}, {"edge_time_s": 20e-9}, "pulse_trailing_edge_s", 0.5e-9),
])
def test_tracked_pulse_rejects_common_readback_error(joint, monkeypatch, advanced, edges, field, delta):
    state, _, run = joint
    run("configure-tracking", mode="on")
    arguments = {"waveform": "pulse", "arguments": {
        "frequency_hz": 1000, "amplitude_vpp": 0.1, "pulse_width_s": 100e-6,
        **edges, **advanced,
    }}
    assert run("configure-tracked-waveform", **arguments)["readback_verified"]
    last_write = run_dual_channel("configure-tracked-waveform", arguments, model=MODEL)["commands"][-1]
    write = SimulatedResource.write

    def change_both_after_configuration(self, command):
        result = write(self, command)
        if command == last_write:
            for channel in (state.ch1, state.ch2):
                setattr(channel, field, getattr(channel, field) + delta)
        return result

    monkeypatch.setattr(SimulatedResource, "write", change_both_after_configuration)
    with pytest.raises(WaveformVerificationError):
        run("configure-tracked-waveform", **arguments)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert state.tracking == "ON"


def test_tracking_activation_rejects_small_pulse_copy_error(joint, monkeypatch):
    state, options, run = joint
    visa.configure_pulse(frequency_hz=1000, amplitude_vpp=0.1,
                         pulse_width_s=100e-6, edge_time_s=20e-9, **options)
    write = SimulatedResource.write

    def change_copy(self, command):
        result = write(self, command)
        if command == "SOURce1:TRACk ON":
            state.ch2.pulse_width_s += 5e-9
        return result

    monkeypatch.setattr(SimulatedResource, "write", change_copy)
    with pytest.raises(WaveformVerificationError):
        run("configure-tracking", mode="on")
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    run("configure-tracking", mode="off")
    assert state.tracking == "OFF"


@pytest.mark.parametrize("mode, fault", [
    ("on", None), ("invert", None), ("on", "source"), ("invert", "enabled"),
    ("on", "off-ignored"),
])
def test_tracking_verifies_sync_side_effect(joint, monkeypatch, mode, fault):
    state, options, run = joint
    assert state.sync_source_channel == 1 and not state.sync_enabled
    write = SimulatedResource.write
    writes = []

    def change_sync(self, command):
        writes.append(command)
        if fault == "off-ignored" and command == "OUTPut:SYNC OFF":
            return None
        result = write(self, command)
        if command in {"SOURce2:TRACk ON", "SOURce2:TRACk INVerted"}:
            if fault == "source":
                state.sync_source_channel = 1
            elif fault in {"enabled", "off-ignored"}:
                state.sync_enabled = True
        return result

    monkeypatch.setattr(SimulatedResource, "write", change_sync)
    if fault is None:
        assert run("configure-tracking", mode=mode, reference_channel=2)["readback_verified"]
        assert state.sync_source_channel == 2
    else:
        with pytest.raises(WaveformVerificationError) as caught:
            run("configure-tracking", mode=mode, reference_channel=2)
        assert {"OUTPut1 OFF", "OUTPut2 OFF"}.issubset(writes)
        if fault == "off-ignored":
            assert caught.value.cleanup_errors
    assert state.sync_enabled == (fault == "off-ignored")
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    monkeypatch.setattr(SimulatedResource, "write", write)
    assert visa.set_sync_output(state="off", **options).readback_verified
    run("configure-tracking", mode="off")
    assert state.tracking == "OFF"


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


@pytest.mark.parametrize(
    ("action", "arguments", "load_response", "allowed"),
    [
        ("output", {"channel": "all", "enabled": True}, "1", True),
        ("output", {"channel": "all", "enabled": True}, "10000", True),
        ("output", {"channel": "all", "enabled": True}, "9.9E+37", True),
        ("output", {"channel": "all", "enabled": True}, "-100", False),
        ("output", {"channel": "all", "enabled": True}, "-51", False),
        ("output", {"channel": "all", "enabled": True}, "0", False),
        ("output", {"channel": "all", "enabled": True}, "10001", False),
        ("output", {"channel": "all", "enabled": True}, "1e308", False),
        ("configure-combine", {"channel": 1, "source": "ch2"}, "-100", False),
    ],
)
def test_joint_operation_validates_load_before_writes(action, arguments, load_response, allowed):
    state = Simulated33521BState(model_id=MODEL)
    writes = []

    class FaultyResource(SimulatedResource):
        def query(self, command):
            if command == "OUTPut2:LOAD?":
                return load_response
            return super().query(command)

        def write(self, command):
            writes.append(command)
            return super().write(command)

    class FaultyManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            return FaultyResource(state)

    options = dict(
        resource=state.resource_name, expected_model_id=MODEL,
        support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
        resource_manager_factory=lambda _: FaultyManager(state),
    )
    if allowed:
        try:
            assert run_dual_channel(action, arguments, **options)["output_states"] == {"1": "on", "2": "on"}
        finally:
            run_dual_channel("output", {"channel": "all", "enabled": False}, **options)
    else:
        with pytest.raises(visa.StatusQueryError):
            run_dual_channel(action, arguments, **options)
        assert writes == []
        assert state.ch1.combine_source == "NONE"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("action,arguments", [
    ("output", {"channel": "all", "enabled": True}),
    ("configure-combine", {"channel": 1, "source": "ch2"}),
    ("configure-voltage-coupling", {"state": "on"}),
    ("update-coupled-voltage", {"channel": 2, "amplitude_vpp": 0.2, "offset_v": 0.05}),
    ("configure-tracking", {"mode": "on", "reference_channel": 2}),
    ("dual-channel-status", {}),
])
@pytest.mark.parametrize("low,high,offset", [
    (0, 0, 0),
    (-0.05, 0.05, 5.0),
])
def test_joint_operation_rejects_inconsistent_voltage_readback(joint, monkeypatch, action, arguments, low, high, offset):
    state, _, run = joint
    coupled = action == "update-coupled-voltage"
    if coupled:
        run("configure-voltage-coupling", state="on")
    query, write = SimulatedResource.query, SimulatedResource.write
    readings = {
        "SOURce2:VOLTage:LOW?": str(low),
        "SOURce2:VOLTage:HIGH?": str(high),
        "SOURce2:VOLTage:OFFSet?": str(offset),
    }
    writes = []

    def faulty(self, command):
        return readings[command] if command in readings else query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "query", faulty)
    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WaveformVerificationError):
        run(action, **arguments)
    assert writes == []
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert state.ch1.combine_source == "NONE"
    assert state.voltage_coupling is coupled and state.tracking == "OFF"
    # Faulty voltage settings must not prevent explicit safe OFF.
    assert run("output", channel="all", enabled=False)["output_states"] == {"1": "off", "2": "off"}
    assert state.voltage_coupling is coupled


@pytest.mark.parametrize("dc_channels", [(1,), (2,), (1, 2)])
def test_voltage_coupling_dc_scope_rejects_before_writes(joint, monkeypatch, dc_channels):
    state, options, run = joint
    for ch in dc_channels:
        visa.configure_dc(voltage_v=0.1, channel=ch, **options)
    if len(dc_channels) == 2:
        run("configure-voltage-coupling", state="on")
        action = "update-coupled-voltage"
        arguments = {"amplitude_vpp": 0.2, "offset_v": 0.05}
    else:
        action = "configure-voltage-coupling"
        arguments = {"state": "on"}
    write = SimulatedResource.write
    writes = []

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WaveformParameterError):
        run(action, **arguments)
    assert writes == []
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert state.voltage_coupling is (len(dc_channels) == 2)
    if len(dc_channels) == 2:
        assert run("output", channel="all", enabled=True)["output_state"] == "on"
        run("output", channel="all", enabled=False)
        run("configure-voltage-coupling", state="off")
        assert not state.voltage_coupling


def test_other_channel_modulation_rejects_inconsistent_carrier_range(joint, monkeypatch):
    state, options, _ = joint
    query, write = SimulatedResource.query, SimulatedResource.write
    writes = []

    def faulty(self, command):
        if command in {"SOURce1:VOLTage:LOW?", "SOURce1:VOLTage:HIGH?"}:
            return "0"
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "query", faulty)
    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WaveformVerificationError) as caught:
        visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                            am=visa.AMConfig(depth_percent=40, source="ch2"), **options)
    assert "SOURce1:AM:STATe ON" in writes
    assert "OUTPut1 OFF" in writes and "OUTPut2 OFF" in writes
    assert not any(command in {"OUTPut1 ON", "OUTPut2 ON"} for command in writes)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert caught.value.output_state == "off"


@pytest.mark.parametrize("load,amplitude,allowed", [
    ("50", 0.001, True), ("50", 0.0005, False),
    ("high-z", 0.002, True), ("high-z", 0.001, False),
    ("1", 0.0001, True),
])
def test_group_on_validates_raw_amplitude_without_rejecting_dc(joint, load, amplitude, allowed):
    state, options, run = joint
    visa.configure_dc(voltage_v=0.1, channel=1, **options)
    # Synthetic readback, including the non-nominal load which has no
    # explicit minimum in the registered nominal-load specifications.
    state.ch2.output_load = load
    state.ch2.amplitude_vpp = amplitude
    state.ch2.offset_v = 0.1
    if allowed:
        assert run("output", channel="all", enabled=True)["output_state"] == "on"
        assert run("output", channel="all", enabled=False)["output_state"] == "off"
    else:
        with pytest.raises(WaveformVerificationError):
            run("output", channel="all", enabled=True)
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


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



@pytest.mark.parametrize("mode,reading,allowed", [
    ("am", -200, False), ("dssc", -100, False), ("sum", -200, False),
    ("am", 121, False), ("sum", 101, False),
    ("am", 120, True), ("sum", 100, True), ("dssc", 0, True),
])
def test_group_on_validates_modulation_readback(joint, monkeypatch, mode, reading, allowed):
    state, _, run = joint
    state.ch1.amplitude_vpp = 8 if reading < 0 else 0.1
    state.ch1.am_enabled = mode != "sum"
    state.ch1.am_type = "dssc" if mode == "dssc" else "normal"
    state.ch1.sum_enabled = mode == "sum"
    query = SimulatedResource.query
    write = SimulatedResource.write
    writes = []
    header = "SOURce1:SUM:AMPLitude?" if mode == "sum" else "SOURce1:AM:DEPTh?"

    def bad_readback(self, command):
        if command == header:
            return str(reading)
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "query", bad_readback)
    monkeypatch.setattr(SimulatedResource, "write", record)
    if allowed:
        try:
            assert run("output", channel="all", enabled=True)["output_states"] == {"1": "on", "2": "on"}
        finally:
            run("output", channel="all", enabled=False)
    else:
        with pytest.raises(WaveformVerificationError):
            run("output", channel="all", enabled=True)
        assert writes == []
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("operation,model,trigger,fail_off", [
    ("phase", "keysight-33521b", "PHASe:SYNChronize", False),
    ("phase", MODEL, "PHASe:SYNChronize", False),
    ("coupling", MODEL, "SOURce1:FREQuency:COUPle:STATe ON", True),
    ("am", MODEL, "SOURce1:AM:STATe ON", False),
    ("pwm", MODEL, "SOURce1:PWM:STATe ON", False),
    ("release", MODEL, "SOURce1:AM:STATe OFF", False),
])
def test_direct_joint_configuration_failure_cleans_affected_outputs(
    monkeypatch, operation, model, trigger, fail_off,
):
    state = Simulated33521BState(model_id=model)
    options = dict(resource=state.resource_name,
                   resource_manager_factory=SimulatedResourceManagerFactory(state))
    channels = [1] if model == "keysight-33521b" else [1, 2]
    if operation == "release":
        visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                            am=visa.AMConfig(depth_percent=40, source="ch2"), **options)
    write = SimulatedResource.write
    writes = []

    def unexpected_output(self, command):
        writes.append(command)
        if fail_off and command == "OUTPut2 OFF":
            raise OSError("injected OFF failure")
        result = write(self, command)
        if command == trigger:
            state.channels[channels[-1]].output_enabled = True
        return result

    monkeypatch.setattr(SimulatedResource, "write", unexpected_output)
    with pytest.raises(WaveformVerificationError) as caught:
        if operation == "phase":
            run_dual_channel("phase-sync", {}, **options)
        elif operation == "coupling":
            run_dual_channel("configure-frequency-coupling",
                             {"state": "on", "mode": "ratio", "ratio": 2}, **options)
        elif operation == "am":
            visa.configure_sine(frequency_hz=10000, amplitude_vpp=0.1,
                                am=visa.AMConfig(depth_percent=40, source="ch2"), **options)
        elif operation == "pwm":
            visa.configure_pulse(frequency_hz=10000, amplitude_vpp=0.1,
                                 pulse_width_s=25e-6, edge_time_s=10e-9,
                                 pwm=visa.PWMConfig(deviation_s=1e-6, source="ch2"), **options)
        else:
            visa.configure_sine(frequency_hz=1000, amplitude_vpp=0.1, **options)
    assert {command for command in writes if command in {"OUTPut1 OFF", "OUTPut2 OFF"}} == {
        f"OUTPut{channel} OFF" for channel in channels
    }
    assert not state.ch1.output_enabled
    if fail_off:
        assert state.ch2.output_enabled and state.frequency_coupling
        assert caught.value.cleanup_errors and caught.value.output_state is None
    else:
        assert all(not state.channels[channel].output_enabled for channel in channels)
        assert caught.value.output_state == "off"
    if operation == "am":
        assert state.ch1.am_enabled and state.ch1.am_source == "ch2"
    elif operation == "pwm":
        assert state.ch1.pwm_enabled and state.ch1.pwm_source == "ch2"


def test_joint_configuration_rejects_late_output_on(joint, monkeypatch):
    state, _, run = joint
    run("configure-combine", channel=1, source="ch2")
    query = SimulatedResource.query
    reads = 0

    def late_on(self, command):
        nonlocal reads
        if command == "OUTPut2?" and state.ch1.combine_source == "NONE":
            reads += 1
            if reads == 2:
                state.ch2.output_enabled = True
        return query(self, command)

    monkeypatch.setattr(SimulatedResource, "query", late_on)
    with pytest.raises(WaveformVerificationError) as caught:
        run("configure-combine", channel=1, source="none")
    assert caught.value.output_state == "off"
    assert state.ch1.combine_source == "NONE"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    # Read-only status must still report an actual ON without writing OFF.
    state.ch2.output_enabled = True
    assert run("dual-channel-status")["channel_status"]["2"]["output_state"] == "on"
    assert state.ch2.output_enabled


@pytest.mark.parametrize("operation", ["phase-sync", "output"])
def test_interrupted_joint_preflight_does_not_write(joint, monkeypatch, operation):
    state, _, run = joint
    query = SimulatedResource.query
    write = SimulatedResource.write
    writes = []

    def interrupted(self, command):
        if command == "OUTPut2?":
            raise KeyboardInterrupt
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "query", interrupted)
    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(KeyboardInterrupt):
        run(operation, **({"channel": "all", "enabled": True} if operation == "output" else {}))
    assert writes == []
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("operation,fault", [
    ("output", "output"), ("configure-tracking", "output"), ("output", "topology"),
])
def test_joint_preflight_rejects_changed_status(joint, monkeypatch, operation, fault):
    state, _, run = joint
    if operation == "output":
        run("configure-frequency-coupling", state="on", mode="ratio", ratio=2)
    query = SimulatedResource.query
    write = SimulatedResource.write
    reads = 0
    writes = []

    def changed_status(self, command):
        nonlocal reads
        if fault == "output" and command == "OUTPut2?":
            reads += 1
            if reads == 2:
                state.ch2.output_enabled = True
        elif fault == "topology" and command == "SOURce2:COMBine:FEED?":
            reads += 1
            if reads == 2:
                state.ch2.combine_source = "CH1"
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "query", changed_status)
    monkeypatch.setattr(SimulatedResource, "write", record)
    arguments = ({"channel": "all", "enabled": True} if operation == "output"
                 else {"mode": "on"})
    with pytest.raises(WaveformVerificationError):
        run(operation, **arguments)
    assert writes == []
    assert not state.ch1.output_enabled
    assert state.ch2.output_enabled is (fault == "output")
    if fault == "topology":
        assert state.ch2.combine_source == "CH1"


@pytest.mark.parametrize("operation", ["output", "update-coupled-frequency"])
def test_joint_operation_rejects_invalid_active_ratio(joint, monkeypatch, operation):
    state, _, run = joint
    run("configure-frequency-coupling", state="on", mode="ratio", ratio=2)
    # Both carrier frequencies are legal, but the coupling ratio is not.
    state.frequency_coupling_ratio = 2000
    state.ch2.frequency_hz = 2e6
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    arguments = ({"channel": "all", "enabled": True} if operation == "output"
                 else {"channel": 1, "frequency_hz": 2000})
    with pytest.raises(WaveformVerificationError):
        run(operation, **arguments)
    assert writes == []
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    run("configure-frequency-coupling", state="off")
    assert not state.frequency_coupling


def test_joint_frequency_update_rejects_changed_coupling_mode(joint, monkeypatch):
    state, _, run = joint
    run("configure-frequency-coupling", state="on", mode="ratio", ratio=2)
    write = SimulatedResource.write

    def change_mode(self, command):
        result = write(self, command)
        if command.startswith("SOURce1:FREQuency "):
            # The requested frequencies still match, but the active relation changed.
            state.frequency_coupling_mode = "OFFS"
            state.frequency_coupling_offset = 2000
        return result

    monkeypatch.setattr(SimulatedResource, "write", change_mode)
    with pytest.raises(WaveformVerificationError) as caught:
        run("update-coupled-frequency", channel=1, frequency_hz=2000)
    assert caught.value.output_state == "off"
    assert (state.ch1.frequency_hz, state.ch2.frequency_hz) == (2000, 4000)
    assert state.frequency_coupling and state.frequency_coupling_mode == "OFFS"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


def test_joint_configuration_rejects_conflicting_final_topology(joint, monkeypatch):
    state, _, run = joint
    write = SimulatedResource.write

    def conflicting_combine(self, command):
        result = write(self, command)
        if command == "SOURce1:COMBine:FEED CH2":
            state.ch2.combine_source = "CH1"
        return result

    monkeypatch.setattr(SimulatedResource, "write", conflicting_combine)
    with pytest.raises(WaveformVerificationError) as caught:
        run("configure-combine", channel=1, source="ch2")
    assert caught.value.output_state == "off"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert (state.ch1.combine_source, state.ch2.combine_source) == ("CH2", "CH1")
    # An explicit release must still be possible, including from a conflict.
    run("configure-combine", channel=1, source="none")
    assert state.ch1.combine_source == "NONE"


@pytest.mark.parametrize("inject_fault", [False, True])
@pytest.mark.parametrize("initial,action,arguments,command,attribute,replacement", [
    (("voltage",), "configure-frequency-coupling", {"state": "on", "mode": "ratio", "ratio": 2},
     "SOURce1:FREQuency:COUPle:STATe ON", "voltage_coupling", False),
    (("frequency",), "configure-voltage-coupling", {"state": "on"},
     "SOURce1:VOLTage:COUPle:STATe ON", "frequency_coupling", False),
    (("frequency", "voltage"), "update-coupled-frequency", {"frequency_hz": 3000},
     "SOURce1:FREQuency 3000", "voltage_coupling", False),
    (("frequency", "voltage"), "update-coupled-voltage", {"amplitude_vpp": 0.2, "offset_v": 0.05},
     "SOURce1:VOLTage:OFFSet 0.05", "frequency_coupling", False),
    (("frequency", "voltage"), "configure-frequency-coupling", {"state": "off"},
     "SOURce1:FREQuency:COUPle:STATe OFF", "voltage_coupling", False),
    (("frequency", "voltage"), "configure-voltage-coupling", {"state": "off"},
     "SOURce1:VOLTage:COUPle:STATe OFF", "frequency_coupling", False),
    (("frequency",), "configure-combine", {"channel": 1, "source": "ch2"},
     "SOURce1:COMBine:FEED CH2", "frequency_coupling", False),
    (("frequency", "combine"), "configure-combine", {"channel": 1, "source": "none"},
     "SOURce1:COMBine:FEED NONE", "frequency_coupling", False),
    (("frequency", "tracking-conflict"), "configure-tracking", {"mode": "off"},
     "SOURce1:TRACk OFF", "frequency_coupling", False),
    (("frequency", "voltage"), "update-coupled-voltage", {"amplitude_vpp": 0.2, "offset_v": 0.05},
     "SOURce1:VOLTage:OFFSet 0.05", "frequency_coupling_mode", "OFFS"),
    (("frequency", "voltage"), "configure-voltage-coupling", {"state": "off"},
     "SOURce1:VOLTage:COUPle:STATe OFF", "frequency_coupling_mode", "OFFS"),
    (("frequency", "voltage"), "update-coupled-frequency", {"frequency_hz": 3000},
     "SOURce1:FREQuency 3000", "output_load", "high-z"),
])
def test_joint_configuration_preserves_unrequested_topology(
    joint, monkeypatch, inject_fault, initial, action, arguments, command, attribute, replacement,
):
    state, _, run = joint
    if "frequency" in initial:
        run("configure-frequency-coupling", state="on", mode="ratio", ratio=2)
    if "voltage" in initial:
        run("configure-voltage-coupling", state="on")
    if "combine" in initial:
        run("configure-combine", channel=1, source="ch2")
    if "tracking-conflict" in initial:
        # Explicit release must work even from a known conflicting topology.
        state.tracking = "ON"
    if attribute == "frequency_coupling_mode":
        # The replacement relation is also valid for the existing frequencies.
        state.frequency_coupling_offset = state.ch2.frequency_hz - state.ch1.frequency_hz
    target = state.ch2 if attribute == "output_load" else state
    original_value = getattr(target, attribute)
    assert original_value != replacement
    writes = []
    write = SimulatedResource.write

    def mutate_after_write(self, sent):
        result = write(self, sent)
        writes.append(sent)
        if inject_fault and sent == command:
            setattr(target, attribute, replacement)
        return result

    monkeypatch.setattr(SimulatedResource, "write", mutate_after_write)
    if inject_fault:
        with pytest.raises(WaveformVerificationError) as caught:
            run(action, **arguments)
        assert caught.value.output_state == "off"
        assert {"OUTPut1 OFF", "OUTPut2 OFF"}.issubset(writes)
        assert getattr(target, attribute) == replacement
    else:
        result = run(action, **arguments)
        assert result["readback_verified"]
        assert getattr(target, attribute) == original_value
    assert command in writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


def test_sequence_stops_on_unrequested_coupling_change(joint, monkeypatch):
    state, options, _ = joint
    write = SimulatedResource.write
    writes = []

    def lose_voltage_coupling(self, command):
        result = write(self, command)
        writes.append(command)
        if command == "SOURce1:FREQuency:COUPle:STATe ON":
            state.voltage_coupling = False
        return result

    monkeypatch.setattr(SimulatedResource, "write", lose_voltage_coupling)
    document = {"version": 1, "steps": [
        {"action": "configure-voltage-coupling", "arguments": {"state": "on"}},
        {"action": "configure-frequency-coupling", "arguments": {"state": "on", "mode": "ratio", "ratio": 2}},
        {"action": "output", "arguments": {"channel": "all", "enabled": True}},
    ]}
    with pytest.raises(SoftwareSequenceError) as caught:
        run_software_sequence(document, **options)
    details = caught.value.details
    assert details["failed_step"]["step"] == 2
    assert details["completed_step_executions"] == 1
    assert details["cleanup"]["confirmed_off_channels"] == [1, 2]
    assert not details["cleanup"]["errors"]
    assert "OUTPut1 ON" not in writes and "OUTPut2 ON" not in writes
    assert state.frequency_coupling and not state.voltage_coupling
    assert not state.ch1.output_enabled and not state.ch2.output_enabled

@pytest.mark.parametrize("channel,delay,fault", [
    (1, 0, "before"),
    (2, 4e-9, "before"),
    (2, 4e-9, "after"),
    (2, 4e-9, None),
    (1, None, None),
])
def test_tracked_burst_trigger_delay_checks_error_queue(joint, monkeypatch, channel, delay, fault):
    state, _, run = joint
    run("configure-tracking", mode="on")
    burst = {"mode": "counted", "count": 3, "trigger_source": "bus"}
    if delay is not None:
        burst["trigger_delay_s"] = delay
    arguments = {"waveform": "sine", "arguments": {
        "channel": channel, "frequency_hz": 1000, "amplitude_vpp": 0.1, "burst": burst,
    }}
    last_write = run_dual_channel("configure-tracked-waveform", arguments, model=MODEL)["commands"][-1]
    write, query = SimulatedResource.write, SimulatedResource.query
    events = []
    queued_error = '-221,"Settings conflict"'

    def record_write(self, command):
        events.append(("write", command))
        result = write(self, command)
        if fault == "after" and command == last_write:
            state.error_queue.append(queued_error)
        return result

    def record_query(self, command):
        if command == "SYSTem:ERRor?":
            events.append(("query", command))
        return query(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record_write)
    monkeypatch.setattr(SimulatedResource, "query", record_query)
    if fault == "before":
        remaining = '-222,"Data out of range"'
        state.error_queue.extend([queued_error, remaining])
        with pytest.raises(WavegenError, match="pre-existing"):
            run("configure-tracked-waveform", **arguments)
        assert events == [("query", "SYSTem:ERRor?")]
        assert state.error_queue == [remaining]
    elif fault == "after":
        with pytest.raises(WavegenError, match="-221"):
            run("configure-tracked-waveform", **arguments)
        assert events[0] == ("query", "SYSTem:ERRor?")
        assert ("write", last_write) in events
        assert {("write", "OUTPut1 OFF"), ("write", "OUTPut2 OFF")}.issubset(events)
    else:
        assert run("configure-tracked-waveform", **arguments)["readback_verified"]
        assert events[-1] == ("query", "SYSTem:ERRor?")
        assert sum(kind == "query" for kind, _ in events) == (1 if delay is None else 2)
        if delay is not None:
            assert events[0] == ("query", "SYSTem:ERRor?")
            assert state.ch1.trigger_delay_s == state.ch2.trigger_delay_s == pytest.approx(delay, abs=1e-12)
        assert state.ch1.trigger_source == state.ch2.trigger_source == "bus"
    assert state.tracking == "ON"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert not {("write", "OUTPut1 ON"), ("write", "OUTPut2 ON")}.intersection(events)


@pytest.mark.parametrize("field,delta", [
    ("burst_count", 1),
    ("trigger_delay_s", 4e-9),
])
@pytest.mark.parametrize("operation", ["configure-tracking", "output"])
def test_tracking_rejects_discrete_burst_copy_error(joint, monkeypatch, field, delta, operation):
    state, options, run = joint
    visa.configure_sine(
        frequency_hz=1000, amplitude_vpp=0.1,
        burst=visa.CountedBurstConfig(count=100_000, trigger_source="bus", trigger_delay_s=0.1),
        **options,
    )
    assert run("configure-tracking", mode="on")["readback_verified"]
    if operation == "configure-tracking":
        run("configure-tracking", mode="off")
        arguments = {"mode": "on"}
    else:
        run("output", channel="all", enabled=True)
        run("output", channel="all", enabled=False)
        setattr(state.ch2, field, getattr(state.ch2, field) + delta)
        arguments = {"channel": "all", "enabled": True}
    writes = []
    write = SimulatedResource.write

    def corrupt_copy(self, command):
        writes.append(command)
        result = write(self, command)
        if operation == "configure-tracking" and command == "SOURce1:TRACk ON":
            setattr(state.ch2, field, getattr(state.ch2, field) + delta)
        return result

    monkeypatch.setattr(SimulatedResource, "write", corrupt_copy)
    with pytest.raises(WaveformVerificationError):
        run(operation, **arguments)
    if operation == "configure-tracking":
        assert {"OUTPut1 OFF", "OUTPut2 OFF"}.issubset(writes)
    else:
        assert not writes
    assert state.tracking == "ON"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("field,initial,ignored", [
    ("frequency_mode", "SWEep", "SOURce1:FREQuency:MODE CW"),
    ("voltage_unit", "VRMS", "SOURce1:VOLTage:UNIT VPP"),
])
def test_tracked_waveform_rejects_shared_wrong_mode_or_unit(joint, monkeypatch, field, initial, ignored):
    state, _, run = joint
    run("configure-tracking", mode="on")
    arguments = {"waveform": "sine", "arguments": {
        "frequency_hz": 1000, "amplitude_vpp": 0.1,
    }}
    # Both channels start from the same old setting. Pair equality alone
    # cannot verify that the explicitly requested setting was applied.
    for channel in (state.ch1, state.ch2):
        setattr(channel, field, initial)
    assert run("configure-tracked-waveform", **arguments)["readback_verified"]
    for channel in (state.ch1, state.ch2):
        setattr(channel, field, initial)
    write = SimulatedResource.write

    def ignore_setting(self, command):
        if command != ignored:
            return write(self, command)
        return None

    monkeypatch.setattr(SimulatedResource, "write", ignore_setting)
    with pytest.raises(WaveformVerificationError):
        run("configure-tracked-waveform", **arguments)
    assert state.tracking == "ON"
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("topology,field,invalid", [
    ("on", "frequency_hz", -1),
    ("invert", "frequency_hz", 21e6),
    ("on", "fm_deviation_hz", 1500),
    ("voltage", "pm_internal_frequency_hz", 100),
    ("independent", "pwm_deviation_s", 0.0001),
    ("independent", "frequency_hz", 0),
    ("independent", "fsk_hop_frequency_hz", 21e6),
    ("on", "bpsk_rate_hz", 2e6),
    ("independent", "bpsk_phase_shift_deg", 361),
])
def test_group_on_rejects_invalid_current_frequency_settings(joint, monkeypatch, topology, field, invalid):
    state, options, run = joint
    for ch in (1, 2):
        if field == "pwm_deviation_s":
            visa.configure_pulse(frequency_hz=1000, amplitude_vpp=0.1,
                                 pulse_width_s=0.0001, edge_time_s=1e-8, channel=ch,
                                 pwm=visa.PWMConfig(10, 1e-6), **options)
        else:
            modulation = {}
            if field == "fm_deviation_hz":
                modulation["fm"] = visa.FMConfig(10, 100)
            elif field == "pm_internal_frequency_hz":
                modulation["pm"] = visa.PMConfig(10, 30)
            elif field == "fsk_hop_frequency_hz":
                modulation["fsk"] = visa.FSKConfig(hop_frequency_hz=2000, rate_hz=10)
            elif field.startswith("bpsk_"):
                modulation["bpsk"] = visa.BPSKConfig(phase_shift_deg=180, rate_hz=10)
            visa.configure_sine(frequency_hz=1000, amplitude_vpp=0.1, channel=ch,
                                **modulation, **options)
    if topology == "voltage":
        run("configure-voltage-coupling", state="on")
    elif topology != "independent":
        run("configure-tracking", mode=topology)
    run("output", channel="all", enabled=True)
    run("output", channel="all", enabled=False)
    # A tracking pair can agree on an invalid setting. Other topologies
    # must also reject a fault on either channel before either ON write.
    affected = (state.ch1, state.ch2) if topology in {"on", "invert"} else (state.ch2,)
    for channel in affected:
        setattr(channel, field, invalid)
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WavegenError):
        run("output", channel="all", enabled=True)
    assert not writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    run("output", channel="all", enabled=False)
    assert {"OUTPut1 OFF", "OUTPut2 OFF"}.issubset(writes)
    assert state.tracking == ({"on": "ON", "invert": "INV"}.get(topology, "OFF"))
    assert state.voltage_coupling == (topology == "voltage")


def test_tracking_rejects_invalid_reference_frequency_before_copy(joint, monkeypatch):
    state, _, run = joint
    state.ch2.frequency_hz = 21e6
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WavegenError):
        run("configure-tracking", mode="on", reference_channel=2)
    assert not writes
    assert state.tracking == "OFF"
    state.ch2.frequency_hz = 1000
    assert run("configure-tracking", mode="on", reference_channel=2)["readback_verified"]


@pytest.mark.parametrize("mode", ["SWEEP", "LIST", "noise", "prbs"])
def test_group_on_preserves_current_non_cw_and_nonperiodic_modes(joint, monkeypatch, mode):
    state, _, run = joint
    channel = state.ch2
    if mode == "SWEEP":
        channel.frequency_mode = "SWEep"
        field, invalid = "sweep_stop_frequency_hz", 21e6
    elif mode == "LIST":
        channel.frequency_mode = "LIST"
        channel.list_frequencies_hz = (1000, 2000)
        field, invalid = "list_frequencies_hz", (1000, 21e6)
    elif mode == "noise":
        channel.active_function = "NOIS"
        field, invalid = "noise_bandwidth_hz", 21e6
    else:
        channel.active_function = "PRBS"
        field, invalid = "prbs_bit_rate_bps", 51e6
    run("output", channel="all", enabled=True)
    run("output", channel="all", enabled=False)
    setattr(channel, field, invalid)
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WavegenError):
        run("output", channel="all", enabled=True)
    assert not writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("operation,kind,source,field,invalid", [
    ("tracking", "bpsk", "internal", "bpsk_rate_hz", 2e6),
    ("tracking", "bpsk", "external", "bpsk_phase_shift_deg", 361),
    ("activation", "bpsk", "internal", "bpsk_phase_shift_deg", -1),
    ("update", "bpsk", "internal", "bpsk_rate_hz", 2e6),
    ("output", "bpsk", "external", "bpsk_phase_shift_deg", 361),
    ("output", "fsk", "external", "fsk_hop_frequency_hz", 21e6),
    ("update", "fsk", "external", "fsk_hop_frequency_hz", 0),
    ("output", "fm", "external", "fm_deviation_hz", 2000),
    ("tracking", "fm", "external", "fm_deviation_hz", 2000),
    ("output", "pm", "external", "pm_deviation_deg", 361),
    ("tracking", "pm", "external", "pm_deviation_deg", 361),
    ("output", "pwm", "external", "pwm_deviation_s", 0.0002),
    ("tracking", "pwm", "external", "pwm_deviation_s", 0.0002),
])
def test_joint_modulation_validates_active_parameters_before_writes(
    joint, monkeypatch, operation, kind, source, field, invalid,
):
    state, options, run = joint
    config = {
        "bpsk": visa.BPSKConfig(phase_shift_deg=180, rate_hz=10),
        "fsk": visa.FSKConfig(hop_frequency_hz=2000, rate_hz=10),
        "fm": visa.FMConfig(10, 100),
        "pm": visa.PMConfig(10, 30),
        "pwm": visa.PWMConfig(10, 1e-6),
    }[kind]
    if kind == "pwm":
        visa.configure_pulse(frequency_hz=1000, amplitude_vpp=0.1,
                             pulse_width_s=0.0001, edge_time_s=1e-8,
                             channel=2, pwm=config, **options)
    else:
        visa.configure_sine(frequency_hz=1000, amplitude_vpp=0.1, channel=2,
                            **{kind: config}, **options)
    channel = state.ch2
    setattr(channel, kind + "_source", source)
    if source == "external":
        # An external source does not use the programmed internal rate.
        rate_field = "_rate_hz" if kind in {"fsk", "bpsk"} else "_internal_frequency_hz"
        setattr(channel, kind + rate_field, 2e6)
    if operation == "update":
        run("configure-frequency-coupling", state="on", mode="ratio", ratio=1)
    action, arguments = {
        "tracking": ("configure-tracking", {"mode": "on", "reference_channel": 2}),
        "activation": ("configure-frequency-coupling", {"state": "on", "mode": "ratio", "ratio": 1}),
        "update": ("update-coupled-frequency", {"channel": 2, "frequency_hz": 2000}),
        "output": ("output", {"channel": "all", "enabled": True}),
    }[operation]
    valid = getattr(channel, field)
    setattr(channel, field, invalid)
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WavegenError):
        run(action, **arguments)
    assert not writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert state.tracking == "OFF"
    assert state.frequency_coupling == (operation == "update")
    setattr(channel, field, valid)
    assert run(action, **arguments)["executed"]
    run("output", channel="all", enabled=False)


@pytest.mark.parametrize("kind", ["fm", "pm", "fsk", "bpsk", "pwm"])
def test_joint_external_modulation_rejects_unsupported_carrier(joint, monkeypatch, kind):
    state, _, run = joint
    channel = state.ch2
    setattr(channel, kind + "_enabled", True)
    setattr(channel, kind + "_source", "external")
    channel.active_function = "SIN" if kind == "pwm" else "PULS"
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WavegenError):
        run("output", channel="all", enabled=True)
    assert not writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    channel.active_function = "PULS" if kind == "pwm" else "SIN"
    assert run("output", channel="all", enabled=True)["output_state"] == "on"
    run("output", channel="all", enabled=False)
    if kind in {"fm", "pm", "pwm"}:
        writes.clear()
        with pytest.raises(WavegenError):
            run("configure-frequency-coupling", state="on", mode="ratio", ratio=1)
        assert not writes
        assert not state.frequency_coupling


def test_external_fm_joint_on_checks_square_duty_at_peak_frequency(joint, monkeypatch):
    state, _, run = joint
    channel = state.ch2
    channel.active_function = "SQU"
    channel.frequency_hz = 10e6
    channel.fm_enabled = True
    channel.fm_source = "external"
    channel.fm_deviation_hz = 5e6
    channel.square_duty_cycle_percent = 20
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WavegenError):
        run("output", channel="all", enabled=True)
    assert not writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    channel.square_duty_cycle_percent = 30
    assert run("output", channel="all", enabled=True)["output_state"] == "on"
    run("output", channel="all", enabled=False)


@pytest.mark.parametrize("operation,carrier,mode,field,valid,invalid", [
    ("output", "SIN", "triggered", "burst_phase_deg", 45.1, 361),
    ("tracking", "SIN", "triggered", "trigger_delay_s", 4e-9, 2e-9),
    ("activation", "SIN", "triggered", "burst_trigger_slope", "negative", "unknown"),
    ("update", "SIN", "triggered", "trigger_delay_s", 0.1, 1001),
    ("output", "SIN", "gated", "burst_phase_deg", -45.1, 45.15),
    ("output", "SIN", "gated", "burst_gate_polarity", "inverted", "unknown"),
    ("output", "PRBS", "triggered", "trigger_delay_s", 4e-9, 2e-9),
    ("output", "NOIS", "gated", "burst_gate_polarity", "normal", "unknown"),
])
def test_joint_burst_validates_active_parameters_before_writes(
    joint, monkeypatch, operation, carrier, mode, field, valid, invalid,
):
    state, _, run = joint
    channel = state.ch2
    channel.active_function = carrier
    channel.burst_enabled = True
    channel.burst_mode = mode
    channel.burst_count = 5
    channel.burst_phase_deg = 45.1
    channel.trigger_source = "external" if field == "burst_trigger_slope" else "bus"
    setattr(channel, field, valid)
    if operation == "update":
        run("configure-frequency-coupling", state="on", mode="ratio", ratio=1)
    action, arguments = {
        "tracking": ("configure-tracking", {"mode": "on", "reference_channel": 2}),
        "activation": ("configure-frequency-coupling", {"state": "on", "mode": "ratio", "ratio": 1}),
        "update": ("update-coupled-frequency", {"channel": 2, "frequency_hz": 2000}),
        "output": ("output", {"channel": "all", "enabled": True}),
    }[operation]
    setattr(channel, field, invalid)
    writes = []
    write = SimulatedResource.write

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "write", record)
    with pytest.raises(WavegenError):
        run(action, **arguments)
    assert not writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled
    assert state.tracking == "OFF"
    assert state.frequency_coupling == (operation == "update")
    setattr(channel, field, valid)
    assert run(action, **arguments)["executed"]
    run("output", channel="all", enabled=False)


def test_joint_burst_phase_uses_current_angle_unit(joint, monkeypatch):
    import math

    state, _, run = joint
    state.ch2.burst_enabled = True
    state.ch2.trigger_source = "bus"
    state.ch2.burst_phase_deg = 45.1
    query = SimulatedResource.query
    writes = []
    write = SimulatedResource.write

    def radians(self, command):
        if command == "UNIT:ANGLe?":
            return "RAD"
        if command == "SOURce2:BURSt:PHASe?":
            return str(math.radians(state.ch2.burst_phase_deg))
        return query(self, command)

    def record(self, command):
        writes.append(command)
        return write(self, command)

    monkeypatch.setattr(SimulatedResource, "query", radians)
    monkeypatch.setattr(SimulatedResource, "write", record)
    assert run("output", channel="all", enabled=True)["output_state"] == "on"
    run("output", channel="all", enabled=False)
    writes.clear()
    state.ch2.burst_phase_deg = 45.15
    with pytest.raises(WavegenError):
        run("output", channel="all", enabled=True)
    assert not writes
    assert not state.ch1.output_enabled and not state.ch2.output_enabled


@pytest.mark.parametrize("fail_recovery", [False, True])
def test_group_on_teardown_failure_reports_confirmed_off(fail_recovery):
    state = Simulated33521BState(model_id=MODEL)
    writes = []
    opened = 0

    class FaultyResource(SimulatedResource):
        def __init__(self, first):
            super().__init__(state)
            self.first = first

        def write(self, command):
            writes.append(command)
            if not self.first and fail_recovery and command == "OUTPut1 OFF":
                raise OSError("injected recovery failure")
            return super().write(command)

        def close(self):
            if self.first:
                raise OSError("injected session close failure")
            super().close()

    class FaultyManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            nonlocal opened
            opened += 1
            return FaultyResource(opened == 1)

    with pytest.raises(visa.VisaCleanupError) as caught:
        run_dual_channel(
            "output", {"channel": "all", "enabled": True},
            resource=state.resource_name, expected_model_id=MODEL,
            support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
            resource_manager_factory=lambda _: FaultyManager(state),
        )
    assert opened == 2
    assert writes == ["OUTPut1 ON", "OUTPut2 ON", "OUTPut1 OFF", "OUTPut2 OFF"]
    assert not state.ch2.output_enabled
    assert state.ch1.output_enabled == fail_recovery
    assert caught.value.output_state == (None if fail_recovery else "off")
    if fail_recovery:
        assert any("Channel 1 OFF" in message for message in caught.value.cleanup_errors)
