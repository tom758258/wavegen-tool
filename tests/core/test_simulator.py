from __future__ import annotations

from collections.abc import Callable

import pytest

from wavegen_tool_core import (
    SIMULATED_33521B_IDN,
    SIMULATED_33521B_RESOURCE,
    Simulated33521BState,
    SimulatedResource,
    SimulatedResourceManager,
    configure_dc,
    configure_noise,
    configure_prbs,
    configure_pulse,
    configure_ramp,
    configure_ramp_sweep,
    configure_sine,
    configure_sine_sweep,
    configure_square,
    configure_square_sweep,
    configure_triangle,
    configure_triangle_sweep,
    identify_instrument,
    list_resources,
    query_status,
    set_output,
)
from wavegen_tool_core.simulator import SimulatedResourceManagerFactory


def _factory_for(
    state: Simulated33521BState,
) -> Callable[[str], SimulatedResourceManager]:
    return lambda _library: SimulatedResourceManager(state)


def test_simulator_exposes_one_deterministic_recognized_resource() -> None:
    state = Simulated33521BState()
    manager = SimulatedResourceManager(state)

    assert manager.list_resources() == (SIMULATED_33521B_RESOURCE,)
    session = manager.open_resource(SIMULATED_33521B_RESOURCE)
    assert session.query("*IDN?") == SIMULATED_33521B_IDN
    session.close()
    manager.close()

    result = identify_instrument(
        SIMULATED_33521B_RESOURCE,
        resource_manager_factory=_factory_for(state),
    )

    assert result.resource == SIMULATED_33521B_RESOURCE
    assert result.identity.manufacturer == "KEYSIGHT TECHNOLOGIES"
    assert result.identity.model == "33521B"
    assert result.identity.canonical_model_id == "keysight-33521b"

    listing = list_resources(
        live_only=True,
        resource_manager_factory=_factory_for(state),
    )
    assert listing.resources[0].resource == SIMULATED_33521B_RESOURCE
    assert listing.resources[0].manufacturer == "KEYSIGHT TECHNOLOGIES"
    assert listing.resources[0].model == "33521B"


@pytest.mark.parametrize(
    ("model_id", "canonical_model"),
    [
        ("keysight-33510b", "33510B"),
        ("keysight-33512b", "33512B"),
    ],
)
def test_simulator_factory_exposes_registered_model_identity(
    model_id,
    canonical_model,
) -> None:
    factory = SimulatedResourceManagerFactory(
        Simulated33521BState(model_id=model_id)
    )
    manager = factory("@sim")

    assert manager.list_resources() == (factory.resource_name,)
    session = manager.open_resource(factory.resource_name)
    assert session.query("*IDN?") == (
        f"KEYSIGHT TECHNOLOGIES,{canonical_model},SIM000001,1.0"
    )
    session.close()
    manager.close()


@pytest.mark.parametrize(
    ("configure", "expected_function", "state_field", "expected_value"),
    [
        (
            lambda factory: configure_sine(
                SIMULATED_33521B_RESOURCE,
                2000,
                0.2,
                resource_manager_factory=factory,
            ),
            "SIN",
            "frequency_hz",
            2000.0,
        ),
        (
            lambda factory: configure_sine_sweep(
                SIMULATED_33521B_RESOURCE,
                1000,
                10000,
                "linear",
                1,
                0.2,
                hold_time_s=2,
                return_time_s=3,
                resource_manager_factory=factory,
            ),
            "SIN",
            "frequency_mode",
            "SWEep",
        ),
        (
            lambda factory: configure_square_sweep(
                SIMULATED_33521B_RESOURCE,
                1000,
                10000,
                "linear",
                1,
                0.2,
                hold_time_s=2,
                return_time_s=3,
                duty_cycle_percent=25,
                resource_manager_factory=factory,
            ),
            "SQUARE",
            "frequency_mode",
            "SWEep",
        ),
        (
            lambda factory: configure_ramp_sweep(
                SIMULATED_33521B_RESOURCE,
                1000,
                10000,
                "linear",
                1,
                0.2,
                hold_time_s=2,
                return_time_s=3,
                symmetry_percent=40,
                resource_manager_factory=factory,
            ),
            "RAMP",
            "frequency_mode",
            "SWEep",
        ),
        (
            lambda factory: configure_triangle_sweep(
                SIMULATED_33521B_RESOURCE,
                1000,
                10000,
                "linear",
                1,
                0.2,
                hold_time_s=2,
                return_time_s=3,
                resource_manager_factory=factory,
            ),
            "TRIANGLE",
            "frequency_mode",
            "SWEep",
        ),
        (
            lambda factory: configure_square(
                SIMULATED_33521B_RESOURCE,
                2000,
                0.2,
                duty_cycle_percent=25,
                resource_manager_factory=factory,
            ),
            "SQUARE",
            "square_duty_cycle_percent",
            25.0,
        ),
        (
            lambda factory: configure_ramp(
                SIMULATED_33521B_RESOURCE,
                2000,
                0.2,
                symmetry_percent=40,
                resource_manager_factory=factory,
            ),
            "RAMP",
            "ramp_symmetry_percent",
            40.0,
        ),
        (
            lambda factory: configure_triangle(
                SIMULATED_33521B_RESOURCE,
                2000,
                0.2,
                0.1,
                "high-z",
                resource_manager_factory=factory,
            ),
            "TRIANGLE",
            "offset_v",
            0.1,
        ),
        (
            lambda factory: configure_pulse(
                SIMULATED_33521B_RESOURCE,
                2000,
                0.2,
                0.0002,
                edge_time_s=20e-9,
                resource_manager_factory=factory,
            ),
            "PULSE",
            "pulse_width_s",
            0.0002,
        ),
        (
            lambda factory: configure_dc(
                SIMULATED_33521B_RESOURCE,
                1.5,
                resource_manager_factory=factory,
            ),
            "DC",
            "offset_v",
            1.5,
        ),
        (
            lambda factory: configure_noise(
                SIMULATED_33521B_RESOURCE,
                0.2,
                200000,
                resource_manager_factory=factory,
            ),
            "NOISE",
            "noise_bandwidth_hz",
            200000.0,
        ),
        (
            lambda factory: configure_prbs(
                SIMULATED_33521B_RESOURCE,
                2000000,
                0.2,
                pattern="PN9",
                resource_manager_factory=factory,
            ),
            "PRBS",
            "prbs_pattern",
            "PN9",
        ),
    ],
)
def test_all_waveform_configurations_update_simulated_state_with_output_off(
    configure: Callable[[Callable[[str], SimulatedResourceManager]], object],
    expected_function: str,
    state_field: str,
    expected_value: object,
) -> None:
    state = Simulated33521BState(output_enabled=True)

    result = configure(_factory_for(state))

    assert state.active_function == expected_function
    assert getattr(state, state_field) == expected_value
    if state_field == "frequency_mode":
        assert state.frequency_hz == 1000.0
        assert state.sweep_start_frequency_hz == 1000.0
        assert state.sweep_stop_frequency_hz == 10000.0
        assert state.sweep_spacing == "linear"
        assert state.sweep_time_s == 1.0
        assert state.sweep_hold_time_s == 2.0
        assert state.sweep_return_time_s == 3.0
        assert state.trigger_source == "immediate"
        session = SimulatedResource(state)
        assert session.query("SOURce1:FREQuency:MODE?") == "SWEep"
        assert session.query("SOURce1:FREQuency:STARt?") == "1000"
        assert session.query("SOURce1:FREQuency:STOP?") == "10000"
        assert session.query("SOURce1:SWEep:SPACing?") == "linear"
        assert session.query("SOURce1:SWEep:TIME?") == "1"
        assert session.query("SOURce1:SWEep:HTIMe?") == "2"
        assert session.query("SOURce1:SWEep:RTIMe?") == "3"
        assert session.query("TRIGger1:SOURce?") == "immediate"
        session.close()
        assert state.amplitude_vpp == 0.2
        assert state.offset_v == 0.0
        assert state.output_load == "50"
        if expected_function == "SQUARE":
            assert state.square_duty_cycle_percent == 25.0
        if expected_function == "RAMP":
            assert state.ramp_symmetry_percent == 40.0
    assert state.output_enabled is False
    assert result.output_state == "off"


def test_simulator_preserves_independent_pulse_edges_and_readback():
    state = Simulated33521BState(output_enabled=True)

    result = configure_pulse(
        SIMULATED_33521B_RESOURCE,
        1000,
        0.2,
        0.0001,
        leading_edge_s=10e-9,
        trailing_edge_s=20e-9,
        resource_manager_factory=_factory_for(state),
    )

    session = SimulatedResourceManager(state).open_resource(
        SIMULATED_33521B_RESOURCE
    )
    assert result.edge_time_s is None
    assert state.pulse_edge_time_s is None
    assert state.pulse_leading_edge_s == 10e-9
    assert state.pulse_trailing_edge_s == 20e-9
    assert session.query("SOURce1:FUNCtion:PULSe:TRANsition:LEADing?") == "1e-08"
    assert session.query("SOURce1:FUNCtion:PULSe:TRANsition:TRAiling?") == "2e-08"
    assert state.output_enabled is False
    session.close()


def test_simulator_state_persists_across_manager_and_session_lifecycles() -> None:
    state = Simulated33521BState()
    factory = _factory_for(state)
    assert state.phase_deg == 0.0

    session = SimulatedResource(state)
    session.write("UNIT:ANGLe DEGree")
    session.write("SOURce1:PHASe 90")
    assert state.phase_deg == 90.0
    assert session.query("SOURce1:PHASe?") == "90"
    session.write("SOURce1:FREQuency MINimum")
    assert state.frequency_hz == 0.000001
    session.write("SOURce1:FUNCtion TRIangle")
    session.write("SOURce1:FREQuency 2500")
    assert state.frequency_hz == 2500.0
    session.close()

    configure_sine_sweep(
        SIMULATED_33521B_RESOURCE,
        1000,
        10000,
        "linear",
        1,
        0.2,
        resource_manager_factory=factory,
    )
    assert state.frequency_mode == "SWEep"

    configure_sine(
        SIMULATED_33521B_RESOURCE,
        2500,
        0.4,
        resource_manager_factory=factory,
    )
    assert state.frequency_mode == "CW"
    assert state.output_enabled is False

    configure_triangle(
        SIMULATED_33521B_RESOURCE,
        2500,
        0.4,
        0.1,
        "high-z",
        resource_manager_factory=factory,
    )
    set_output(
        SIMULATED_33521B_RESOURCE,
        "on",
        resource_manager_factory=factory,
    )
    status = query_status(
        SIMULATED_33521B_RESOURCE,
        resource_manager_factory=factory,
    )

    assert status.function == "TRIANGLE"
    assert status.frequency_hz == 2500.0
    assert status.amplitude == 0.4
    assert status.offset_v == 0.1
    assert status.load == "high-z"
    assert status.output_state == "on"


def test_fresh_simulator_states_are_isolated() -> None:
    changed = Simulated33521BState()
    fresh = Simulated33521BState()

    set_output(
        SIMULATED_33521B_RESOURCE,
        "on",
        resource_manager_factory=_factory_for(changed),
    )

    assert changed.output_enabled is True
    assert fresh.output_enabled is False
    assert fresh.active_function == "SIN"
    assert fresh.frequency_hz == 1000.0


def test_simulator_fails_closed_for_unknown_operations_and_closed_sessions() -> None:
    manager = SimulatedResourceManager()
    with pytest.raises(ValueError, match="Unsupported simulated VISA resource"):
        manager.open_resource("USB0::SIM::OTHER::INSTR")

    session = manager.open_resource(SIMULATED_33521B_RESOURCE)
    with pytest.raises(ValueError, match="Unsupported simulated SCPI write"):
        session.write("*RST")
    with pytest.raises(ValueError, match="Unsupported simulated SCPI query"):
        session.query("*OPC?")
    session.close()
    with pytest.raises(RuntimeError, match="session is closed"):
        session.write("OUTPut1 OFF")
    with pytest.raises(RuntimeError, match="session is closed"):
        session.query("*IDN?")

    direct_session = SimulatedResource(Simulated33521BState())
    direct_session.close()
    with pytest.raises(RuntimeError, match="session is closed"):
        direct_session.query("*IDN?")


def test_two_channel_simulator_preserves_independent_channel_state() -> None:
    state = Simulated33521BState(model_id="keysight-33512b")
    factory = SimulatedResourceManagerFactory(state)

    configure_sine(
        factory.resource_name,
        1000,
        0.1,
        resource_manager_factory=factory,
        channel=1,
    )
    configure_sine(
        factory.resource_name,
        2000,
        0.2,
        resource_manager_factory=factory,
        channel=2,
    )
    set_output(
        factory.resource_name,
        "on",
        resource_manager_factory=factory,
        channel=2,
    )

    status_one = query_status(
        factory.resource_name,
        resource_manager_factory=factory,
        channel=1,
    )
    status_two = query_status(
        factory.resource_name,
        resource_manager_factory=factory,
        channel=2,
    )

    assert status_one.frequency_hz == 1000.0
    assert status_one.amplitude == 0.1
    assert status_one.output_state == "off"
    assert status_two.frequency_hz == 2000.0
    assert status_two.amplitude == 0.2
    assert status_two.output_state == "on"


def test_prbs_status_preserves_simulator_channel_isolation() -> None:
    state = Simulated33521BState(model_id="keysight-33512b")
    factory = SimulatedResourceManagerFactory(state)

    configure_prbs(
        factory.resource_name,
        2_000_000,
        0.2,
        resource_manager_factory=factory,
        channel=2,
    )

    status_one = query_status(
        factory.resource_name,
        resource_manager_factory=factory,
        channel=1,
    )
    status_two = query_status(
        factory.resource_name,
        resource_manager_factory=factory,
        channel=2,
    )

    assert status_one.function == "SIN"
    assert status_one.frequency_hz == 1000.0
    assert status_one.bit_rate_bps is None
    assert state.ch1.prbs_bit_rate_bps == 1_000_000.0
    assert status_two.function == "PRBS"
    assert status_two.frequency_hz is None
    assert status_two.bit_rate_bps == 2_000_000.0


def test_two_channel_simulator_sweep_isolation_and_cw_recovery() -> None:
    state = Simulated33521BState(model_id="keysight-33512b")
    factory = SimulatedResourceManagerFactory(state)

    sweep_result = configure_square_sweep(
        factory.resource_name,
        2000,
        20000,
        "linear",
        2,
        0.2,
        duty_cycle_percent=25,
        channel=2,
        resource_manager_factory=factory,
    )

    assert sweep_result.channel == 2
    assert state.ch1.active_function == "SIN"
    assert state.ch1.frequency_mode == "CW"
    assert state.ch1.sweep_start_frequency_hz == 1000.0
    assert state.ch1.sweep_stop_frequency_hz == 10000.0
    assert state.ch2.active_function == "SQUARE"
    assert state.ch2.frequency_mode == "SWEep"
    assert state.ch2.sweep_start_frequency_hz == 2000.0
    assert state.ch2.sweep_stop_frequency_hz == 20000.0

    configure_square(
        factory.resource_name,
        3000,
        0.3,
        channel=2,
        resource_manager_factory=factory,
    )

    assert state.ch1.active_function == "SIN"
    assert state.ch1.frequency_mode == "CW"
    assert state.ch1.frequency_hz == 1000.0
    assert state.ch2.active_function == "SQUARE"
    assert state.ch2.frequency_mode == "CW"
    assert state.ch2.frequency_hz == 3000.0


def test_simulator_error_queue_fifo() -> None:
    """SYSTem:ERRor? drains the shared state FIFO; status does not consume it."""
    state = Simulated33521BState()
    state.error_queue = ['-222,"Data out of range"', '-350,"Queue overflow"']

    manager_a = SimulatedResourceManager(state)
    session_a = manager_a.open_resource(SIMULATED_33521B_RESOURCE)

    # First session drains first entry
    assert session_a.query("SYSTem:ERRor?") == '-222,"Data out of range"'

    # Second session (same state) drains second entry
    manager_b = SimulatedResourceManager(state)
    session_b = manager_b.open_resource(SIMULATED_33521B_RESOURCE)
    assert session_b.query("SYSTem:ERRor?") == '-350,"Queue overflow"'

    # Third drain on empty queue returns sentinel
    assert session_b.query("SYSTem:ERRor?") == '+0,"No error"'

    # Status query does not consume the queue
    state.error_queue = ['-100,"Data out of range"']
    manager_c = SimulatedResourceManager(state)
    session_c = manager_c.open_resource(SIMULATED_33521B_RESOURCE)
    result = query_status(
        SIMULATED_33521B_RESOURCE,
        resource_manager_factory=_factory_for(state),
    )
    assert result.function == "SIN"
    # Queue still has the entry (not consumed by status)
    assert session_c.query("SYSTem:ERRor?") == '-100,"Data out of range"'

    # Close does not clear a pending queue entry
    state.error_queue = ['+123,"Test after close"']
    session_c.close()
    assert state.error_queue == ['+123,"Test after close"']

    # Cross-session: remaining sessions on the same state see the shared queue
    manager_d = SimulatedResourceManager(state)
    session_d = manager_d.open_resource(SIMULATED_33521B_RESOURCE)
    assert session_d.query("SYSTem:ERRor?") == '+123,"Test after close"'
