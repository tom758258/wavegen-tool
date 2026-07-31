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
    configure_sine,
    configure_square,
    identify_instrument,
    list_resources,
    query_status,
    set_output,
)


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
    assert state.output_enabled is False
    assert result.output_state == "off"


def test_simulator_state_persists_across_manager_and_session_lifecycles() -> None:
    state = Simulated33521BState()
    factory = _factory_for(state)

    configure_square(
        SIMULATED_33521B_RESOURCE,
        2500,
        0.4,
        0.1,
        30,
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

    assert status.function == "SQUARE"
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
