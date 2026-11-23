import json

import pytest

from wavegen_tool_core import (
    Simulated33521BState,
    SimulatedResource,
    SimulatedResourceManager,
    run_software_sequence,
    set_output,
)
from wavegen_tool_core import visa
from wavegen_tool_core.errors import WavegenError
from wavegen_tool_core.identity import SUPPORT_POLICY_MODE_VALIDATION
from wavegen_tool_core.simulator import SimulatedResourceManagerFactory
from wavegen_tool_core.software_sequence import SoftwareSequenceError

MODEL = "keysight-33512b"


def _step(action, **arguments):
    return {"action": action, "arguments": arguments}


def _document(*steps, loops=1):
    return {"version": 1, "loop_count": loops, "steps": list(steps)}


SINE = _step("configure-sine", frequency_hz=1000, amplitude_vpp=0.1)
ON = _step("output", enabled=True)
WAIT = _step("wait", seconds=0)


@pytest.fixture
def fake_instrument(monkeypatch):
    state = Simulated33521BState(model_id=MODEL)
    writes = []
    queries = []

    class Session(SimulatedResource):
        def write(self, command):
            writes.append(command)
            super().write(command)

        def query(self, command):
            queries.append(command)
            return super().query(command)

    class Manager(SimulatedResourceManager):
        def open_resource(self, name):
            return Session(state)

    monkeypatch.setattr(visa, "create_resource_manager", lambda backend: Manager(state))
    return state, writes, queries


def test_plan_validates_the_maximum_without_expanding_or_touching_visa(monkeypatch):
    def forbidden(backend):
        pytest.fail("Dry-run opened VISA")

    monkeypatch.setattr(visa, "create_resource_manager", forbidden)
    result = run_software_sequence(_document(*([WAIT] * 100), loops=10_000), model=MODEL)
    assert result["status"] == "planned"
    assert result["execution_units"] == 1_000_000
    assert len(result["steps"]) == 100
    assert result["cleanup"] is None

    result = run_software_sequence(_document(SINE, ON, _step("status")), model=MODEL)
    assert result["steps"][1]["enabled"] is True
    assert "output_state" not in result["steps"][2]


@pytest.mark.parametrize(
    "document",
    [
        {"version": True, "steps": [WAIT]},
        _document(WAIT, loops=1.0),
        _document(WAIT, loops=10_001),
        _document(*([WAIT] * 251)),
        _document(*([WAIT] * 101), loops=10_000),
        _document(_step("run-software-sequence")),
        _document(_step("configure-sync")),
        _document(_step("clear-arbitrary-memory", confirm_clear=True)),
        _document(_step("wait", seconds=True)),
        _document(_step("wait", seconds=float("inf"))),
        _document(_step("wait", seconds=10**20)),
        _document(_step("configure-sine", frequency_hz="1000", amplitude_vpp=0.1)),
        _document(_step("output", enabled="on")),
        _document(_step("output", enabled=True, confirm_output=True)),
        _document(_step("upload-arbitrary", channel=1, name="WAVE", path="wave.csv"), loops=2),
        _document(
            _step("upload-arbitrary", channel=1, name="WAVE", path="wave.csv"),
            _step("upload-arbitrary", channel=1, name="wave", path="wave.csv"),
        ),
    ],
)
def test_rejects_invalid_document(document):
    with pytest.raises(WavegenError):
        run_software_sequence(document, model=MODEL)


@pytest.mark.parametrize(
    ("entry", "action"),
    [
        ([], None),
        ({"action": "status", "unknown": True}, "status"),
        ({"action": "unknown-command"}, "unknown-command"),
        ({"action": "private/sequence.json"}, None),
        ({"action": "status", "arguments": []}, "status"),
    ],
)
def test_malformed_step_reports_location_without_execution(entry, action):
    with pytest.raises(SoftwareSequenceError) as caught:
        run_software_sequence(_document(ON, entry, loops=2), model=MODEL)
    details = caught.value.details
    assert details["completed_loops"] == details["completed_step_executions"] == 0
    assert details["cleanup"] is None
    failed = details["failed_step"]
    assert (failed["loop"], failed["step"], failed["action"]) == (1, 2, action)
    assert failed["error"]["code"] == "waveform_parameter_error"


@pytest.mark.parametrize(
    ("action", "arguments", "mode", "field", "expected"),
    [
        (
            "configure-sine",
            {"frequency_hz": 1000, "am": {"modulation_frequency_hz": 100, "depth_percent": 50}},
            "AM", "depth_percent", 50,
        ),
        (
            "configure-sine-sweep",
            {"start_frequency_hz": 2, "stop_frequency_hz": 8, "spacing": "linear", "sweep_time_s": 1},
            "SWEEP", "stop_frequency_hz", 8,
        ),
        (
            "configure-sine-list-sweep",
            {"frequencies_hz": [2, 4, 8], "dwell_s": 1},
            "LIST", "frequencies_hz", [2, 4, 8],
        ),
    ],
)
def test_channel_two_advanced_readback_is_planned_and_executed(
    action, arguments, mode, field, expected
):
    document = _document(
        _step(action, channel=2, amplitude_vpp=0.1, verify_readback=True, **arguments),
        _step("readback-advanced", channel=2),
    )
    plan = run_software_sequence(document, model=MODEL)
    assert plan["status"] == "planned"
    assert plan["channels"] == [2]

    factory = SimulatedResourceManagerFactory(Simulated33521BState(model_id=MODEL))
    result = run_software_sequence(
        document, resource=factory.resource_name, resource_manager_factory=factory
    )
    assert result["completed_step_executions"] == 2
    readback = result["results"][1]
    assert readback["channel"] == 2
    assert readback["advanced_mode"] == mode
    assert readback["settings"][field] == expected


def test_simulator_keeps_arbitrary_memory_and_channel_context(tmp_path):
    path = tmp_path / "wave.csv"
    path.write_text("0\n" * 8, encoding="utf-8")
    playback = {"name": "WAVE", "sample_rate_hz": 1e6, "amplitude_vpp": 0.1}
    native = {
        **playback,
        "version": 1,
        "name": "NATIVE",
        "trigger_source": "bus",
        "steps": [{"waveform": "WAVE", "play_control": "once"}],
    }
    document = _document(
        _step("upload-arbitrary", channel=2, name="WAVE", path=str(path)),
        _step("configure-arbitrary", channel=2, **playback),
        _step("configure-arbitrary-sequence", channel=2, document=native),
        _step("list-arbitrary-memory", channel=2),
        _step("list-arbitrary-memory", channel=1),
    )
    factory = SimulatedResourceManagerFactory(Simulated33521BState(model_id=MODEL))
    result = run_software_sequence(
        document, resource=factory.resource_name, resource_manager_factory=factory
    )
    assert result["completed_step_executions"] == 5
    assert {"WAVE", "NATIVE.seq"} <= set(result["results"][3]["names"])
    assert {"WAVE", "NATIVE.seq"}.isdisjoint(result["results"][4]["names"])
    assert str(path) not in json.dumps(result)
    with pytest.raises(SoftwareSequenceError, match="Arbitrary"):
        run_software_sequence(_document(_step("list-arbitrary-memory")), model="keysight-33510b")
    with pytest.raises(SoftwareSequenceError, match="Channel 2"):
        run_software_sequence(document, model="keysight-33521b")


def test_loop_order_and_bounded_results():
    factory = SimulatedResourceManagerFactory(Simulated33521BState(model_id=MODEL))
    result = run_software_sequence(
        _document(WAIT, _step("status"), loops=101),
        resource=factory.resource_name,
        resource_manager_factory=factory,
    )
    assert result["completed_loops"] == 101
    assert result["completed_step_executions"] == 202
    assert len(result["results"]) == 200
    assert [(row["loop"], row["step"], row["action"]) for row in result["results"][:4]] == [
        (1, 1, "wait"),
        (1, 2, "status"),
        (2, 1, "wait"),
        (2, 2, "status"),
    ]


@pytest.mark.parametrize(
    ("later", "message"),
    [
        (_step("configure-sine", frequency_hz=25e6, amplitude_vpp=0.1), "frequency"),
        (
            _step(
                "configure-sine",
                frequency_hz=1000,
                amplitude_vpp=0.1,
                am={"modulation_frequency_hz": 100, "depth_percent": 50, "shape": "square"},
            ),
            "Non-sine",
        ),
        (
            _step(
                "configure-sine",
                frequency_hz=1000,
                amplitude_vpp=0.1,
                burst={"mode": "counted", "count": 2, "period_s": 0.02, "trigger_delay_s": 0},
            ),
            "Trigger Delay",
        ),
        (_step("upload-arbitrary", name="WAVE", path="missing.csv"), "outside Product Live"),
    ],
)
def test_later_live_rejection_precedes_all_writes(fake_instrument, later, message):
    state, writes, _ = fake_instrument
    with pytest.raises(SoftwareSequenceError, match=message) as caught:
        run_software_sequence(_document(SINE, ON, later), resource=state.resource_name)
    assert caught.value.details["failed_step"]["step"] == 3
    assert writes == []


def test_global_trigger_checks_the_other_active_channel(fake_instrument):
    state, writes, _ = fake_instrument
    set_output(state.resource_name, "on", channel=2)
    writes.clear()
    with pytest.raises(WavegenError, match="Channel 2.*OFF"):
        run_software_sequence(_document(SINE, _step("trigger")), resource=state.resource_name)
    assert writes == []
    result = run_software_sequence(_document(SINE, ON), resource=state.resource_name)
    assert result["cleanup"]["confirmed_off_channels"] == [1]
    assert not any(command.startswith("OUTPut2 ") for command in writes)


def test_live_coupling_guard_precedes_writes(fake_instrument, monkeypatch):
    state, writes, _ = fake_instrument
    original = SimulatedResource.query
    monkeypatch.setattr(
        SimulatedResource,
        "query",
        lambda session, command: (
            "1" if command == "SOURce1:FREQuency:COUPle:STATe?" else original(session, command)
        ),
    )
    with pytest.raises(WavegenError, match="coupling"):
        run_software_sequence(_document(SINE, _step("status")), resource=state.resource_name)
    assert writes == []


def test_failed_on_is_cleaned_up_and_original_and_cleanup_errors_survive(
    fake_instrument, monkeypatch
):
    state, writes, _ = fake_instrument
    original = SimulatedResource.write

    def fail_after_on(session, command):
        if command == "OUTPut1 OFF":
            raise OSError("OFF failed")
        original(session, command)
        if command == "OUTPut1 ON":
            raise OSError("ON reply lost")

    monkeypatch.setattr(SimulatedResource, "write", fail_after_on)
    with pytest.raises(SoftwareSequenceError) as caught:
        run_software_sequence(_document(ON, SINE), resource=state.resource_name)
    details = caught.value.details
    assert writes == ["OUTPut1 ON", "OUTPut1 OFF"]
    assert details["completed_step_executions"] == 0
    assert details["failed_step"]["error"]["code"] == "visa_write_error"
    assert details["cleanup"]["confirmed_off_channels"] == []
    assert len(details["cleanup"]["errors"]) == 2


def test_cleanup_failure_alone_fails_the_run_and_checks_other_channel(fake_instrument, monkeypatch):
    state, writes, _ = fake_instrument
    original = SimulatedResource.write

    def lose_off(session, command):
        if command != "OUTPut1 OFF":
            original(session, command)

    monkeypatch.setattr(SimulatedResource, "write", lose_off)
    with pytest.raises(SoftwareSequenceError) as caught:
        run_software_sequence(
            _document(ON, _step("output", channel=2, enabled=True)), resource=state.resource_name
        )
    assert caught.value.code == "visa_cleanup_error"
    assert caught.value.details["cleanup"]["confirmed_off_channels"] == [2]
    assert writes == ["OUTPut1 ON", "OUTPut2 ON", "OUTPut1 OFF", "OUTPut2 OFF"]


def test_contributor_arbitrary_still_requires_exact_live_identity(fake_instrument):
    state, writes, _ = fake_instrument
    document = _document(_step("list-arbitrary-memory"))
    options = {
        "resource": state.resource_name,
        "support_policy_mode": SUPPORT_POLICY_MODE_VALIDATION,
    }
    with pytest.raises(WavegenError, match="exact expected model"):
        run_software_sequence(document, **options)
    with pytest.raises(WavegenError, match="expected"):
        run_software_sequence(document, expected_model_id="keysight-33521b", **options)
    result = run_software_sequence(document, expected_model_id=MODEL, **options)
    assert result["results"][0]["names"]
    assert writes == []
