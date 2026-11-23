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
        _document(_step("run-sine-list-sweep-once", frequencies_hz=[1000], dwell_s=1, amplitude_vpp=0.1)),
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


@pytest.mark.parametrize("start,stop,expected", [
    (0.1, 0.25, [0.1, 0.2, 0.25]),
    (0.25, 0.1, [0.25, 0.15, 0.1]),
    (0.1, 0.1, [0.1]),
])
def test_amplitude_ramp_keeps_output_on_through_each_update(
    fake_instrument, monkeypatch, start, stop, expected,
):
    from wavegen_tool_core import run_basic_workflow

    state, writes, _ = fake_instrument
    state.ch1.autorange_enabled = False
    held = []

    def dwell(seconds):
        assert seconds == 2
        assert state.ch1.output_enabled
        held.append(state.ch1.amplitude_vpp)

    monkeypatch.setattr("wavegen_tool_core.software_sequence.time.sleep", dwell)
    result = run_basic_workflow(
        "run-amplitude-ramp",
        {"start_vpp": start, "stop_vpp": stop, "step_vpp": 0.1, "dwell_s": 2},
        resource=state.resource_name, support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
        expected_model_id=MODEL,
    )

    assert held == expected
    assert writes.count("OUTPut1 ON") == 1
    effects = writes[writes.index("OUTPut1 ON") + 1:writes.index("OUTPut1 OFF", 1)]
    assert effects == [f"SOURce1:VOLTage {point:g}" for point in expected[1:]]
    assert all("RANGe:AUTO" not in command for command in writes)
    assert state.ch1.autorange_enabled is False
    assert result["cleanup"]["confirmed_off_channels"] == [1]


def test_amplitude_ramp_bounds_and_product_policy(fake_instrument):
    from wavegen_tool_core import run_basic_workflow

    state, writes, queries = fake_instrument
    action = "run-amplitude-ramp"
    arguments = {"start_vpp": 0.01, "stop_vpp": 1.24, "step_vpp": 0.01, "dwell_s": 1}
    result = run_basic_workflow(action, arguments, model=MODEL)
    assert result["step_count"] == 250
    assert result["total_wait_seconds"] == 124
    assert result["steps"][-3]["amplitude_vpp"] == 1.24
    at_limit = arguments | {"stop_vpp": 0.02, "dwell_s": 300}
    assert run_basic_workflow(action, at_limit, model=MODEL)["total_wait_seconds"] == 600

    for overrides in (
        {"stop_vpp": 1.25}, {"dwell_s": 5}, {"step_vpp": 0},
        {"stop_vpp": 11, "step_vpp": 10},
    ):
        with pytest.raises(WavegenError):
            run_basic_workflow(action, arguments | overrides, model=MODEL)

    with pytest.raises(WavegenError, match="outside Product Live"):
        run_basic_workflow(action, arguments | {"stop_vpp": 0.01}, resource=state.resource_name)
    assert writes == queries == []


def test_amplitude_ramp_error_attempts_safe_off(fake_instrument, monkeypatch):
    from wavegen_tool_core import run_basic_workflow

    state, writes, _ = fake_instrument

    def fail_update(*args, **kwargs):
        assert state.ch1.output_enabled
        raise visa.VisaWriteError("Amplitude write failed.")

    monkeypatch.setattr(visa, "update_sine_amplitude", fail_update)
    monkeypatch.setattr("wavegen_tool_core.software_sequence.time.sleep", lambda _: None)
    with pytest.raises(SoftwareSequenceError) as failure:
        run_basic_workflow(
            "run-amplitude-ramp",
            {"start_vpp": 0.1, "stop_vpp": 0.2, "step_vpp": 0.1, "dwell_s": 1},
            resource=state.resource_name, support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
            expected_model_id=MODEL,
        )
    assert failure.value.details["cleanup"]["confirmed_off_channels"] == [1]
    assert writes[-1] == "OUTPut1 OFF"
    assert not state.ch1.output_enabled


def test_basic_workflows_schedule_existing_operations(fake_instrument, monkeypatch):
    from wavegen_tool_core import run_basic_workflow

    state, writes, _ = fake_instrument
    observed = []
    monkeypatch.setattr(
        "wavegen_tool_core.software_sequence.time.sleep",
        lambda seconds: observed.append((seconds, state.ch1.output_enabled, state.ch1.active_function)),
    )
    sine = {"action": "configure-sine", "arguments": {"frequency_hz": 1000, "amplitude_vpp": 0.1}}
    square = {"action": "configure-square", "arguments": sine["arguments"]}
    run_basic_workflow("run-timed-output", {"waveform": sine, "duration_s": 2}, resource=state.resource_name)
    run_basic_workflow("run-output-cycle", {"on_s": 3, "off_s": 4, "cycles": 2}, resource=state.resource_name)
    switch = run_basic_workflow("run-waveform-sequence", {"waveforms": [
        {"waveform": sine, "duration_s": 5}, {"waveform": square, "duration_s": 6},
    ]}, resource=state.resource_name)
    assert observed == [
        (2, True, "SIN"), (3, True, "SIN"), (4, False, "SIN"),
        (3, True, "SIN"), (4, False, "SIN"), (5, True, "SIN"), (6, True, "SQUARE"),
    ]
    assert [step["output_state"] for step in switch["results"] if step["action"] == "output"] == [
        "on", "off", "on", "off",
    ]
    assert switch["cleanup"]["confirmed_off_channels"] == [1]
    assert writes[-1] == "OUTPut1 OFF"


def test_frequency_step_uses_list_sweep_limits_and_dwell(fake_instrument, monkeypatch):
    from wavegen_tool_core import run_basic_workflow

    state, writes, _ = fake_instrument
    state.ch1.trigger_count = 19
    state.ch1.trigger_delay_s = 0.01
    traffic = []
    write, query = SimulatedResource.write, SimulatedResource.query
    completion = iter(("8", "0"))
    clock = 0.0

    def record_write(session, command):
        traffic.append(command)
        return write(session, command)

    def record_query(session, command):
        nonlocal clock
        traffic.append(command)
        if command == "STATus:OPERation:CONDition?":
            return next(completion)
        if command == "SYSTem:ERRor?" and state.ch1.output_enabled:
            clock += 0.25
        return query(session, command)

    monkeypatch.setattr(SimulatedResource, "write", record_write)
    monkeypatch.setattr(SimulatedResource, "query", record_query)
    monkeypatch.setattr(visa, "monotonic", lambda: clock)
    monkeypatch.setattr("wavegen_tool_core.software_sequence.time.sleep", traffic.append)
    arguments = {"start_frequency_hz": 800, "stop_frequency_hz": 250, "frequency_step_hz": 200, "dwell_s": 1.25}
    dry_run = run_basic_workflow("run-frequency-step", arguments, model=MODEL)
    assert traffic == []
    commands = dry_run["steps"][0]["commands"]
    assert dry_run["total_wait_seconds"] == 5
    setup_to_start = (
        "SOURce1:FREQuency 800", "SOURce1:LIST:FREQuency 800,600,400,250", "SOURce1:LIST:DWELl 1.25",
        "TRIGger1:SOURce IMMediate", "INITiate1:CONTinuous OFF", "TRIGger1:COUNt 4",
        "SOURce1:FREQuency?", "SOURce1:LIST:FREQuency?", "SOURce1:LIST:DWELl?",
        "TRIGger1:SOURce?", "OUTPut1 ON", "SOURce1:FREQuency:MODE LIST", "INITiate1:IMMediate",
    )
    assert [entry for entry in commands if entry in setup_to_start] == list(setup_to_start)
    start = commands.index("INITiate1:IMMediate")
    assert commands[start + 1 : start + 3] == ("SYSTem:ERRor?", "STATus:OPERation:CONDition?")
    off = commands.index("OUTPut1 OFF", start)
    assert commands[off + 1 : off + 4] == ("OUTPut1?", "SYSTem:ERRor?", "SOURce1:FREQuency:MODE CW")
    result = run_basic_workflow("run-frequency-step", arguments, resource=state.resource_name)
    assert result["results"][0]["frequencies_hz"] == (800, 600, 400, 250)
    assert result["results"][0]["dwell_s"] == 1.25
    assert [entry for entry in traffic if entry in setup_to_start] == list(setup_to_start)
    start = traffic.index("INITiate1:IMMediate")
    assert traffic[start + 1 : start + 7] == [
        "SYSTem:ERRor?", 4.75, "STATus:OPERation:CONDition?", 0.02, "STATus:OPERation:CONDition?", "OUTPut1 OFF",
    ]
    assert writes.count("OUTPut1 ON") == state.ch1.list_runs == result["completed_loops"] == 1
    assert state.ch1.frequency_mode == "CW" and not state.ch1.output_enabled
    assert state.ch1.continuous_trigger and state.ch1.trigger_count == 19 and state.ch1.trigger_delay_s == 0.01
    assert result["cleanup"]["confirmed_off_channels"] == [1]
    for stop, valid in [(128, True), (129, False)]:
        request = arguments | {"start_frequency_hz": 1, "stop_frequency_hz": stop, "frequency_step_hz": 1}
        if valid:
            preview = run_basic_workflow("run-frequency-step", request, model=MODEL)
            assert len(preview["steps"][0]["frequencies_hz"]) == stop
        else:
            with pytest.raises(WavegenError):
                run_basic_workflow("run-frequency-step", request, model=MODEL)


@pytest.mark.parametrize("failure", [
    "start_readback", "initiation", "startup_queue", "startup_queue_and_cleanup",
    "two_startup_errors", "two_startup_errors_and_restore", "two_startup_errors_and_queue_read",
])
def test_frequency_step_failure_keeps_output_safe_and_retains_cleanup_error(fake_instrument, monkeypatch, failure):
    from wavegen_tool_core import run_basic_workflow

    state, writes, _ = fake_instrument
    state.ch1.trigger_count = 19
    write, query = SimulatedResource.write, SimulatedResource.query
    two_errors = failure.startswith("two_startup_errors")
    traffic = []
    malformed_reply_sent = False

    def fail_write(session, command):
        traffic.append(command)
        if command == "SOURce1:FREQuency:MODE LIST" and two_errors:
            state.error_queue.append('-221,"Settings conflict"')
            return
        if command == "INITiate1:IMMediate":
            if failure == "initiation":
                raise OSError("LIST initiation failed")
            assert state.ch1.output_enabled
            entry = '-211,"Trigger ignored"' if two_errors else '-221,"Settings conflict"'
            state.error_queue.append(entry)
            return
        if failure == "startup_queue_and_cleanup" and command == "OUTPut1 OFF" and state.ch1.output_enabled:
            raise OSError("Output OFF failed")
        if failure == "two_startup_errors_and_restore" and command == "TRIGger1:COUNt 19":
            state.error_queue.append('-222,"Data out of range"')
            return
        return write(session, command)

    def fail_query(session, command):
        nonlocal malformed_reply_sent
        traffic.append(command)
        if failure == "start_readback" and command == "SOURce1:FREQuency?":
            return "3000"
        if (
            failure == "two_startup_errors_and_queue_read"
            and command == "SYSTem:ERRor?"
            and "INITiate1:IMMediate" in traffic
            and not state.ch1.output_enabled
            and not malformed_reply_sent
        ):
            malformed_reply_sent = True
            return "malformed response"
        return query(session, command)

    monkeypatch.setattr(SimulatedResource, "write", fail_write)
    monkeypatch.setattr(SimulatedResource, "query", fail_query)
    waits = []
    monkeypatch.setattr("wavegen_tool_core.software_sequence.time.sleep", waits.append)
    arguments = {"start_frequency_hz": 1000, "stop_frequency_hz": 3000, "frequency_step_hz": 1000, "dwell_s": 1000}
    with pytest.raises(SoftwareSequenceError) as error:
        run_basic_workflow("run-frequency-step", arguments, resource=state.resource_name)
    assert waits == []
    assert writes[-1] == "OUTPut1 OFF"
    cleanup = error.value.details["cleanup"]
    if failure == "start_readback":
        assert "FREQuency?" in str(error.value)
        assert "OUTPut1 ON" not in writes
    else:
        assert "OUTPut1 ON" in writes
        assert error.value.code == "visa_write_error"
    if failure.startswith("startup_queue"):
        assert "-221" in str(error.value)
        assert "Settings conflict" in str(error.value)
        assert not state.error_queue
    if two_errors:
        start = traffic.index("INITiate1:IMMediate")
        assert traffic[start + 1 : start + 4] == ["SYSTem:ERRor?", "OUTPut1 OFF", "OUTPut1?"]
        report = error.value.details["failed_step"]["error"]
        assert "-221" in report["message"]
        if failure == "two_startup_errors_and_queue_read":
            assert state.ch1.trigger_count == 19
            assert not state.error_queue
            read_failure, pending = report["cleanup_errors"]
            assert "error queue" in read_failure
            assert "-211" in pending and "source unconfirmed" in pending
        else:
            assert "-211" in report["message"] and "Trigger ignored" in report["message"]
            assert not state.error_queue
            if failure == "two_startup_errors_and_restore":
                assert "-222" not in report["message"]
                diagnostic, = report["cleanup_errors"]
                assert "-222" in diagnostic and "-211" not in diagnostic
            else:
                assert not error.value.cleanup_errors
                assert state.ch1.trigger_count == 19
    if failure == "startup_queue_and_cleanup":
        assert cleanup["errors"] and not cleanup["confirmed_off_channels"]
        assert error.value.cleanup_errors
    else:
        assert cleanup["confirmed_off_channels"] == [1] and not state.ch1.output_enabled


@pytest.mark.parametrize("preset,function", [("sine", "SIN"), ("square", "SQUARE"), ("triangle", "TRIANGLE")])
def test_quick_output_presets_apply_fixed_settings_with_output_off(fake_instrument, preset, function):
    from wavegen_tool_core import run_basic_workflow

    state, writes, _ = fake_instrument
    result = run_basic_workflow("apply-quick-output-preset", {"preset": preset}, resource=state.resource_name)
    assert state.ch1.active_function == function
    assert state.ch1.frequency_hz == 1000
    assert state.ch1.amplitude_vpp == 0.1
    assert state.ch1.offset_v == 0
    assert result["results"][0]["load"] == "high-z"
    if preset == "square":
        assert result["results"][0]["duty_cycle_percent"] == 50
    assert "OUTPut1 ON" not in writes
    assert result["cleanup"]["confirmed_off_channels"] == [1]


def test_software_sequence_cancellation_between_steps_cleans_up(fake_instrument, monkeypatch):
    from threading import Event

    state, _, _ = fake_instrument
    event = Event()
    set_output = visa.set_output

    def cancel_after_on(resource, output_state, *args, **kwargs):
        result = set_output(resource, output_state, *args, **kwargs)
        if output_state == "on":
            event.set()
        return result

    monkeypatch.setattr(visa, "set_output", cancel_after_on)
    with pytest.raises(SoftwareSequenceError) as failure:
        run_software_sequence(
            _document(ON, _step("status")), resource=state.resource_name, cancel_event=event,
        )
    details = failure.value.details
    assert details["failed_step"]["action"] == "status"
    assert details["completed_step_executions"] == 1
    assert details["cleanup"]["confirmed_off_channels"] == [1]
    assert not state.ch1.output_enabled
