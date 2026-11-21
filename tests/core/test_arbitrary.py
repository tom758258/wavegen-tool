import struct

import pytest

from wavegen_tool_core import Simulated33521BState, run_arbitrary, send_bus_trigger, set_output
from wavegen_tool_core import visa
from wavegen_tool_core.errors import (
    UnsupportedInstrumentError,
    VisaWriteError,
    WaveformParameterError,
    WaveformVerificationError,
)
from wavegen_tool_core.identity import SUPPORT_POLICY_MODE_VALIDATION
from wavegen_tool_core.simulator import SimulatedResource, SimulatedResourceManager


MODEL = "keysight-33512b"
DEFAULT_ARBITRARY = r"INT:\BUILTIN\EXP_RISE.ARB"
SAMPLES = (-1, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1)
PLAYBACK = {"name": "WAVE", "sample_rate_hz": 1e6, "amplitude_vpp": 1.0}
BUILTIN_PLAYBACK = {**PLAYBACK, "name": "SINC", "load_builtin": True}
SINC_PATH = r"INT:\BUILTIN\SINC.ARB"


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "waveform.csv"
    path.write_text("\n".join(map(str, SAMPLES)), encoding="utf-8")
    return path


def _sequence(**changes):
    return {
        **PLAYBACK,
        "version": 1,
        "name": "SEQ",
        "trigger_source": "bus",
        "steps": [
            {"waveform": "WAVE", "play_control": "once"},
            {"waveform": "WAVE", "play_control": "repeat", "repeat_count": 3},
            {"waveform": "WAVE", "play_control": "onceWaitTrig"},
            {"waveform": "WAVE", "play_control": "repeatTilTrig"},
        ],
        **changes,
    }


@pytest.fixture
def fake_visa(monkeypatch):
    state = Simulated33521BState(model_id=MODEL)
    commands = []
    sessions = []

    class FakeSession(SimulatedResource):
        def __init__(self):
            super().__init__(state)
            self.io_timeouts = []

        def write(self, command):
            commands.append(command)
            return super().write(command)

        def query(self, command):
            commands.append(command)
            self.io_timeouts.append((command, self.timeout))
            return super().query(command)

        def write_raw(self, message):
            commands.append(message)
            self.io_timeouts.append((message, self.timeout))
            return super().write_raw(message)

    class FakeManager(SimulatedResourceManager):
        def open_resource(self, _resource_name):
            session = FakeSession()
            session.write_termination = "\r\n"
            sessions.append(session)
            return session

    monkeypatch.setattr(visa, "create_resource_manager", lambda _backend: FakeManager(state))

    def run(command, *, resource=None, **arguments):
        commands.clear()
        return run_arbitrary(
            command, arguments, resource=resource or state.resource_name,
            support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
        )

    return state, commands, sessions, run


@pytest.mark.parametrize("name,stem", [
    ("Cardiac", "CARDIAC"), ("Exponential Rise", "EXP_RISE"),
    ("exponential fall", "EXP_FALL"), ("Gaussian", "GAUSSIAN"),
    ("Haversine", "HAVERSINE"), ("Lorentz", "LORENTZ"),
    ("D-Lorentz", "D_LORENTZ"), ("Negative Ramp", "NEG_RAMP"), ("Sinc", "SINC"),
])
def test_builtin_arbitrary_plan_uses_documented_files(name, stem):
    path = rf"INT:\BUILTIN\{stem}.ARB"
    plan = run_arbitrary(
        "configure-arbitrary", {**BUILTIN_PLAYBACK, "name": name, "channel": 2}, model=MODEL
    )
    assert plan["name"] == stem and plan["executed"] is False
    assert "builtin_loaded" not in plan
    assert f'MMEMory:LOAD:DATA2 "{path}"' in plan["commands"]
    assert f'SOURce2:FUNCtion:ARBitrary "{path}"' in plan["commands"]


@pytest.mark.parametrize("changes,model,message", [
    ({"name": r"INT:\BUILTIN\SINC.ARB"}, MODEL, "Unknown built-in"),
    ({"name": 'SINC";*RST'}, MODEL, "Unknown built-in"),
    ({"load_builtin": 1}, MODEL, "boolean"),
    ({}, "keysight-33510b", "standard Arbitrary"),
    ({"channel": 2}, "keysight-33521b", "Channel 2"),
])
def test_builtin_arbitrary_rejects_invalid_requests(changes, model, message):
    with pytest.raises(WaveformParameterError, match=message):
        run_arbitrary("configure-arbitrary", {**BUILTIN_PLAYBACK, **changes}, model=model)


def test_builtin_arbitrary_loads_and_reuses_without_touching_other_channel(fake_visa):
    state, commands, sessions, run = fake_visa
    state.ch1.output_enabled = state.ch2.output_enabled = True
    state.ch2.arbitrary_points["EXISTING"] = 8
    settings = {**BUILTIN_PLAYBACK, "channel": 2, "offset_v": 0.1, "load": "high-z"}
    result = run("configure-arbitrary", **settings)
    assert result["builtin_loaded"] is True and result["output_state"] == "off"
    load = f'MMEMory:LOAD:DATA2 "{SINC_PATH}"'
    assert commands.index("OUTPut2?") < commands.index(load)
    assert commands.count("SOURce2:DATA:VOLatile:CATalog?") == 2
    assert sessions[-1].writes[0] == "OUTPut2 OFF"
    assert state.ch2.selected_arbitrary == SINC_PATH
    assert state.ch2.active_function == "ARB"
    assert state.ch2.arbitrary_sample_rate_hz == 1e6
    assert state.ch2.amplitude_vpp == 1.0 and state.ch2.offset_v == 0.1
    assert state.ch2.output_load == "high-z" and not state.ch2.output_enabled
    assert state.ch1.output_enabled and state.ch1.active_function == "SIN"
    assert state.ch1.arbitrary_points == {DEFAULT_ARBITRARY: 40}
    assert set(state.ch2.arbitrary_points) == {DEFAULT_ARBITRARY, "EXISTING", SINC_PATH}

    state.ch2.arbitrary_points[SINC_PATH.lower()] = state.ch2.arbitrary_points.pop(SINC_PATH)
    reused = run("configure-arbitrary", **settings)
    assert reused["builtin_loaded"] is False and load not in commands
    assert state.ch2.selected_arbitrary == SINC_PATH.lower()
    writes = [write for session in sessions for write in session.writes]
    assert all("CLEar" not in write and write != "*RST" and not write.endswith(" ON") for write in writes)


@pytest.mark.parametrize("names", [
    ("sinc",), (r"USB:\Other\SINC.BARB",), ("sinc.seq",), (SINC_PATH, "sinc"),
])
def test_builtin_arbitrary_rejects_catalog_conflicts_before_writes(fake_visa, names):
    state, _, sessions, run = fake_visa
    state.ch2.arbitrary_points.update(dict.fromkeys(names, 8))
    expected = state.ch2.arbitrary_points.copy()
    with pytest.raises(WaveformVerificationError, match="conflicts|ambiguous"):
        run("configure-arbitrary", **BUILTIN_PLAYBACK, channel=2)
    assert sessions[-1].writes == []
    assert state.ch2.arbitrary_points == expected


@pytest.mark.parametrize("failure,message", [
    ("memory", "SCPI error -225"), ("completion", "did not confirm completion"),
    ("missing", "missing after loading"),
])
def test_builtin_arbitrary_load_failures_stop_before_playback(fake_visa, monkeypatch, failure, message):
    state, _, sessions, run = fake_visa
    if failure == "memory":
        state.ch1.arbitrary_points["FULL"] = state.ch1.arbitrary_free_points
    elif failure == "completion":
        query = SimulatedResource.query
        monkeypatch.setattr(SimulatedResource, "query", lambda self, command:
                            "0" if command == "*OPC?" else query(self, command))
    else:
        write = SimulatedResource._apply_write
        monkeypatch.setattr(SimulatedResource, "_apply_write", lambda self, command:
                            None if command.startswith("MMEMory:LOAD:") else write(self, command))
    with pytest.raises(VisaWriteError, match=message):
        run("configure-arbitrary", **BUILTIN_PLAYBACK)
    assert sessions[-1].writes == ["OUTPut1 OFF", f'MMEMory:LOAD:DATA1 "{SINC_PATH}"']
    assert state.ch1.selected_arbitrary == DEFAULT_ARBITRARY
    assert not state.ch1.output_enabled and sessions[-1].closed


def test_builtin_arbitrary_rejects_wrong_selection_readback(fake_visa, monkeypatch):
    state, _, _, run = fake_visa
    query = SimulatedResource.query
    monkeypatch.setattr(SimulatedResource, "query", lambda self, command:
                        f'"{DEFAULT_ARBITRARY}"' if command == "SOURce1:FUNCtion:ARBitrary?"
                        else query(self, command))
    with pytest.raises(WaveformVerificationError, match="waveform did not match"):
        run("configure-arbitrary", **BUILTIN_PLAYBACK)
    assert not state.ch1.output_enabled


@pytest.mark.parametrize("order,endian", [("NORM", ">"), ("SWAP", "<")])
def test_binary_upload_uses_instrument_byte_order_without_selecting(
    csv_path,
    fake_visa,
    monkeypatch,
    order,
    endian,
):
    state, commands, sessions, run = fake_visa
    original = SimulatedResource._query_response
    monkeypatch.setattr(
        SimulatedResource,
        "_query_response",
        lambda session, query: order if query == "FORMat:BORDer?" else original(session, query),
    )
    result = run("upload-arbitrary", name="WAVE", path=csv_path, channel=2)
    block = b"SOURce2:DATA:ARBitrary WAVE,#232" + struct.pack(f"{endian}8f", *SAMPLES) + b"\r\n"
    assert [message for message in commands if isinstance(message, bytes)] == [block]
    assert state.ch2.selected_arbitrary == DEFAULT_ARBITRARY
    assert state.ch2.active_function == "SIN"
    assert state.ch2.arbitrary_points == {DEFAULT_ARBITRARY: 40, "WAVE": 8}
    assert state.ch1.arbitrary_points == {DEFAULT_ARBITRARY: 40}
    assert not sessions[-1].writes[:-1]
    assert result["sample_count"] == 8 and "path" not in result and "samples" not in result

    with pytest.raises(WaveformParameterError, match="already exists"):
        run("upload-arbitrary", name="wave", path=csv_path, channel=2)
    assert not sessions[-1].writes


@pytest.mark.parametrize(
    "resource",
    [
        pytest.param(None, id="usb"),
        pytest.param("TCPIP0::192.0.2.10::inst0::INSTR", id="lan"),
    ],
)
def test_upload_timeout_scales_with_binary_size_on_usb(tmp_path, fake_visa, resource):
    _state, _commands, sessions, run = fake_visa
    transfer_timeouts = []
    path = tmp_path / "transfer.csv"
    for name, sample_count in (("SMALL", 4_096), ("NORMAL", 250_000)):
        path.write_bytes(b"0\n" * sample_count)
        run("upload-arbitrary", name=name, path=path, resource=resource)
        session = sessions[-1]
        write_timeout, completion_timeout = [
            timeout
            for message, timeout in session.io_timeouts
            if isinstance(message, bytes) or message == "*OPC?"
        ]
        assert write_timeout == completion_timeout
        transfer_timeouts.append(write_timeout)
        assert all(
            timeout == visa.DEFAULT_TIMEOUT_MS
            for command, timeout in session.io_timeouts
            if isinstance(command, str) and command != "*OPC?"
        )
        assert session.timeout == visa.DEFAULT_TIMEOUT_MS
    small, normal = transfer_timeouts
    if resource is None:
        assert visa.DEFAULT_TIMEOUT_MS < small < normal
        assert normal >= 15_000
    else:
        assert small == normal == visa.DEFAULT_TIMEOUT_MS


@pytest.mark.parametrize("failure", ["short write", "completion timeout", "SCPI error"])
def test_upload_failure_reports_uncertain_memory_without_clearing(
    csv_path,
    fake_visa,
    monkeypatch,
    failure,
):
    state, commands, sessions, run = fake_visa
    original = SimulatedResource.write_raw

    def failed_write(session, message):
        count = original(session, message)
        if failure == "SCPI error":
            state.error_queue.append('-222,"Value out of range"')
        return count - 1 if failure == "short write" else count

    if failure == "completion timeout":
        original_query = SimulatedResource.query

        def failed_completion(session, command):
            if command == "*OPC?":
                raise TimeoutError
            return original_query(session, command)

        monkeypatch.setattr(SimulatedResource, "query", failed_completion)

    monkeypatch.setattr(SimulatedResource, "write_raw", failed_write)
    with pytest.raises(VisaWriteError, match="memory state may be uncertain"):
        run("upload-arbitrary", name="WAVE", path=csv_path)
    assert state.ch1.arbitrary_points["WAVE"] == 8
    assert sum(isinstance(message, bytes) for message in commands) == 1
    assert all("CLEar" not in write for write in sessions[-1].writes)
    assert sessions[-1].timeout == visa.DEFAULT_TIMEOUT_MS
    assert sessions[-1].closed


@pytest.mark.parametrize("row", ["", "1,2", "header", "NaN", "Inf", "-1.01", "1_", "1" * 257])
def test_invalid_csv_rejected_before_visa(tmp_path, row):
    path = tmp_path / "invalid.csv"
    path.write_text("0\n" * 8 + row + "\n", encoding="utf-8")
    with pytest.raises(WaveformParameterError, match="CSV"):
        run_arbitrary("upload-arbitrary", {"name": "WAVE", "path": path}, model=MODEL)


def test_file_bounds_and_model_admission(tmp_path, csv_path, fake_visa, monkeypatch):
    state, _commands, sessions, run = fake_visa
    args = {"name": "WAVE", "path": tmp_path / "missing.csv"}
    with pytest.raises(UnsupportedInstrumentError, match="Product Live"):
        run_arbitrary("upload-arbitrary", args, resource=state.resource_name)
    with pytest.raises(WaveformParameterError, match="UTF-8"):
        run("upload-arbitrary", **args)
    with pytest.raises(WaveformParameterError, match="Arbitrary name"):
        run("upload-arbitrary", **dict(args, name="bad;*RST"))
    short = tmp_path / "short.csv"
    short.write_text("0\n" * 7, encoding="utf-8")
    with pytest.raises(WaveformParameterError, match="8 samples"):
        run("upload-arbitrary", **dict(args, path=short))
    assert not sessions
    with pytest.raises(WaveformParameterError, match="standard Arbitrary"):
        run_arbitrary("upload-arbitrary", args, model="keysight-33510b")
    with pytest.raises(WaveformParameterError, match="Channel 2"):
        run_arbitrary("list-arbitrary-memory", {"channel": 2}, model="keysight-33521b")

    def no_visa(*_args):
        pytest.fail("dry-run opened VISA")

    monkeypatch.setattr(visa, "create_resource_manager", no_visa)
    plan = run_arbitrary("upload-arbitrary", dict(args, path=csv_path), model=MODEL)
    assert plan["executed"] is False
    assert plan["sample_count"] == 8
    assert "<Float32 definite-length block>" in plan["commands"][0]


@pytest.mark.parametrize("guard", ["memory", "coupling", "output", "unknown tracking"])
def test_upload_respects_memory_allocation_and_channel_safety(csv_path, fake_visa, guard):
    state, _commands, sessions, run = fake_visa
    if guard == "memory":
        state.ch2.arbitrary_points["OLD"] = 999_808  # 64 free points, below one block.
    elif guard == "coupling":
        state.voltage_coupling = True
    elif guard == "output":
        state.ch2.output_enabled = True
    else:
        state.tracking = "UNKNOWN"

    with pytest.raises((WaveformParameterError, WaveformVerificationError)):
        run("upload-arbitrary", name="WAVE", path=csv_path, channel=2)
    assert sessions[-1].writes == []


def test_single_waveform_and_confirmed_clear_keep_channel_isolation(csv_path, fake_visa):
    state, commands, sessions, run = fake_visa
    run("upload-arbitrary", name="WAVE", path=csv_path, channel=2)
    state.ch1.output_enabled = state.ch2.output_enabled = True
    result = run("configure-arbitrary", **PLAYBACK, channel=2)
    assert result["output_state"] == "off"
    assert sessions[-1].writes[0] == "OUTPut2 OFF"
    assert state.ch1.output_enabled is True
    assert state.ch2.selected_arbitrary == "WAVE"
    assert state.ch2.arbitrary_sample_rate_hz == PLAYBACK["sample_rate_hz"]
    assert "*RST" not in commands
    status = visa.query_status(state.resource_name, channel=2)
    assert status.sample_rate_hz == PLAYBACK["sample_rate_hz"]
    assert status.frequency_hz is None

    result = run("list-arbitrary-memory", channel=2)
    assert result["names"] == (DEFAULT_ARBITRARY, "WAVE")
    assert result["free_points"] == 999_744
    assert sessions[-1].writes == []
    with pytest.raises(WaveformParameterError, match="explicit confirmation"):
        run("clear-arbitrary-memory", channel=2)
    assert sessions[-1].writes == []
    run("clear-arbitrary-memory", channel=2, confirm_clear=True)
    assert sessions[-1].writes == ["OUTPut2 OFF", "SOURce2:DATA:VOLatile:CLEar"]
    assert state.ch1.output_enabled is True
    assert state.ch2.arbitrary_points == {DEFAULT_ARBITRARY: 40}
    assert state.ch2.selected_arbitrary == DEFAULT_ARBITRARY


@pytest.mark.parametrize("command,arguments", [
    ("configure-arbitrary", PLAYBACK),
    ("configure-arbitrary", BUILTIN_PLAYBACK),
    ("configure-arbitrary-sequence", {"document": _sequence()}),
])
@pytest.mark.parametrize("response", ["1", "UNKNOWN", None], ids=["on", "unknown", "timeout"])
def test_playback_rejects_unconfirmed_sample_rate_isolation(
    csv_path, fake_visa, monkeypatch, command, arguments, response
):
    state, commands, sessions, run = fake_visa
    run("upload-arbitrary", name="WAVE", path=csv_path, channel=2)
    state.ch1.active_function = state.ch2.active_function = "ARB"
    state.ch1.output_enabled = state.ch2.output_enabled = True
    state.ch1.arbitrary_sample_rate_hz = 40_000

    original = SimulatedResource._query_response

    def query(session, scpi):
        if scpi == "RATE:COUPle:STATe?":
            if response is None:
                raise TimeoutError
            return response
        return original(session, scpi)

    monkeypatch.setattr(SimulatedResource, "_query_response", query)
    with pytest.raises(WaveformVerificationError, match="sample rate coupling"):
        run(command, **arguments, channel=2)

    assert sessions[-1].writes == []
    assert not any(isinstance(scpi, bytes) for scpi in commands)
    assert state.ch1.arbitrary_sample_rate_hz == 40_000
    assert state.ch1.output_enabled and state.ch2.output_enabled


def test_catalog_resolves_loaded_paths_in_the_selected_channel(csv_path, fake_visa, monkeypatch):
    state, commands, sessions, run = fake_visa
    original = SimulatedResource._query_response

    def query(session, command):
        response = original(session, command)
        if command.endswith(":DATA:VOLatile:CATalog?"):
            return response.replace(',"', ', "')
        return response

    monkeypatch.setattr(SimulatedResource, "_query_response", query)
    loaded = r"USB:\Wave Files\A.ARB"
    state.ch2.arbitrary_points[loaded] = 40
    playback = {**PLAYBACK, "name": "a", "channel": 2}
    assert loaded in run("list-arbitrary-memory", channel=2)["names"]
    run("configure-arbitrary", **playback)
    assert f'SOURce2:FUNCtion:ARBitrary "{loaded}"' in commands
    step = {"waveform": "A", "play_control": "once"}
    run("configure-arbitrary-sequence", document=_sequence(steps=[step]), channel=2)
    assert f'SOURce2:DATA:ATTRibute:POINts? "{loaded}"' in commands
    assert state.ch2.arbitrary_sequences["SEQ"][0][0] == loaded

    with pytest.raises(WaveformParameterError, match="already exists"):
        run("upload-arbitrary", name="A", path=csv_path, channel=2)
    with pytest.raises(WaveformParameterError, match="missing"):
        run("configure-arbitrary", **{**playback, "channel": 1})
    state.ch2.arbitrary_points[r"INT:\Another Folder\A.BARB"] = 40
    with pytest.raises(WaveformVerificationError, match="ambiguous"):
        run("configure-arbitrary", **playback)
    assert sessions[-1].writes == []

    state.ch2.arbitrary_points[r"USB:\X;\B.ARB"] = 40
    with pytest.raises(WaveformVerificationError, match="unsafe catalog"):
        run("configure-arbitrary", **{**playback, "name": "B"})
    assert sessions[-1].writes == []


@pytest.mark.parametrize("trigger_source", ["bus", "external"])
def test_native_sequence_descriptor_and_trigger_routing(csv_path, fake_visa, trigger_source):
    state, commands, sessions, run = fake_visa
    run("upload-arbitrary", name="WAVE", path=csv_path, channel=2)
    document = _sequence(trigger_source=trigger_source)
    if trigger_source == "external":
        document["trigger_slope"] = "negative"

    result = run("configure-arbitrary-sequence", document=document, channel=2)
    descriptor = (
        b'"SEQ","WAVE",0,once,lowAtStart,4,"WAVE",3,repeat,lowAtStart,4,'
        b'"WAVE",0,onceWaitTrig,lowAtStart,4,"WAVE",0,repeatTilTrig,lowAtStart,4'
    )
    size = str(len(descriptor)).encode()
    assert next(message for message in commands if isinstance(message, bytes)) == (
        b"SOURce2:DATA:SEQuence #" + str(len(size)).encode() + size + descriptor + b"\r\n"
    )
    writes = sessions[-1].writes
    assert writes[0] == "OUTPut2 OFF"
    assert writes.index('SOURce2:FUNCtion:ARBitrary "SEQ"') < writes.index("SOURce2:FUNCtion ARB")
    advance = "SOURce2:FUNCtion:ARBitrary:ADVance SRATe"
    assert writes.index(advance) < writes.index(
        f"TRIGger2:SOURce {'BUS' if trigger_source == 'bus' else 'EXTernal'}"
    )
    assert ("TRIGger2:SLOPe NEGative" in writes) == (trigger_source == "external")
    assert not any(
        "FILTer" in write or "SYNC" in write.upper() or " ON" in write for write in writes
    )
    assert result["step_count"] == 4
    assert state.ch2.selected_arbitrary == "SEQ"
    assert state.ch2.output_enabled is False
    assert state.ch2.arbitrary_sequences["SEQ"][-1] == ("WAVE", 0, "repeatTilTrig", "lowAtStart", 4)
    set_output(state.resource_name, "on", channel=2)
    send_bus_trigger(state.resource_name)
    assert state.ch2.arbitrary_bus_triggers == (1 if trigger_source == "bus" else 0)
    run("clear-arbitrary-memory", channel=2, confirm_clear=True)
    assert not state.ch2.arbitrary_sequences


def test_sequence_model_and_memory_errors_fail_before_writes(csv_path, fake_visa):
    state, _commands, sessions, run = fake_visa
    run("upload-arbitrary", name="WAVE", path=csv_path, channel=1)
    with pytest.raises(WaveformParameterError, match="missing"):
        run("configure-arbitrary-sequence", document=_sequence(), channel=2)
    assert not sessions[-1].writes
    with pytest.raises(WaveformParameterError, match="limit"):
        run(
            "configure-arbitrary-sequence",
            document=_sequence(sample_rate_hz=160_000_001),
            channel=1,
        )
    assert not sessions[-1].writes
    state.model_id = "keysight-33510b"
    with pytest.raises(WaveformParameterError, match="standard Arbitrary"):
        run("list-arbitrary-memory")
    assert not sessions[-1].writes


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"steps": []},
        {"steps": [{}] * 513},
        {"trigger_source": "immediate"},
        {"trigger_slope": "positive"},
        {"marker_mode": "maintain"},
        {"steps": [{"waveform": "WAVE", "play_control": "repeat", "repeat_count": 1_000_001}]},
        {"steps": [{"waveform": "WAVE", "play_control": "onceWaitTrig", "repeat_count": 1}]},
    ],
)
def test_sequence_document_bounds(changes):
    with pytest.raises(WaveformParameterError):
        run_arbitrary(
            "configure-arbitrary-sequence", {"document": _sequence(**changes)}, model=MODEL
        )


def test_dry_run_sequence_maximum_steps():
    fixed = {"waveform": "WAVE", "play_control": "repeat", "repeat_count": 1_000_000}
    document = _sequence(steps=[fixed] * 512)
    result = run_arbitrary("configure-arbitrary-sequence", {"document": document}, model=MODEL)
    assert result["step_count"] == 512
    assert result["executed"] is False
    assert "SOURce1:FUNCtion:ARBitrary:ADVance SRATe" in result["commands"]


def test_simulator_sequence_capacity_is_shared_per_channel_and_reset_by_clear(fake_visa):
    state, _commands, _sessions, run = fake_visa
    step = {"waveform": "EXP_RISE", "play_control": "once"}
    run("configure-arbitrary-sequence", document=_sequence(steps=[step] * 512), channel=2)
    extra = _sequence(name="EXTRA", steps=[step])
    with pytest.raises(VisaWriteError):
        run("configure-arbitrary-sequence", document=extra, channel=2)
    assert "EXTRA.seq" not in run("list-arbitrary-memory", channel=2)["names"]
    assert state.ch2.selected_arbitrary == "SEQ"

    run("configure-arbitrary-sequence", document=extra, channel=1)
    run("clear-arbitrary-memory", channel=2, confirm_clear=True)
    assert run("list-arbitrary-memory", channel=2)["names"] == (DEFAULT_ARBITRARY,)
    assert state.ch2.selected_arbitrary == DEFAULT_ARBITRARY
    run("configure-arbitrary-sequence", document=extra, channel=2)
    assert "EXTRA.seq" in run("list-arbitrary-memory", channel=1)["names"]
    assert "EXTRA.seq" in run("list-arbitrary-memory", channel=2)["names"]
