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
