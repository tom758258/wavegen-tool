import http.client
import json
import threading
import time

import pytest

import wavegen_tool_cli.cli as cli_module
import wavegen_tool_cli.worker as worker_module
from wavegen_tool_cli.worker import (
    JobRecord,
    WorkerConfig,
    WorkerRuntime,
    validate_worker_startup,
)
from wavegen_tool_cli.worker_commands import validate_worker_command_request
from wavegen_tool_core import WavegenError


MODEL_ID = "keysight-33521b"


def _worker_config(mode: str = "simulate") -> WorkerConfig:
    return WorkerConfig(
        mode=mode,
        resource=None if mode == "simulate" else "TCPIP0::192.0.2.10::inst0::INSTR",
        backend="system",
        control_port=0,
        allow_output_writes=True,
    )


def _request(
    runtime: WorkerRuntime,
    method: str,
    path: str,
    payload: object | None = None,
) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", runtime.control_port, timeout=1.0
    )
    body = None if payload is None else json.dumps(payload)
    try:
        connection.request(
            method,
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _payload(
    command: str,
    arguments: dict[str, object] | None = None,
    *,
    mode: str = "simulate",
    job_id: str = "job-1",
) -> dict[str, object]:
    context: dict[str, object] = {"mode": mode}
    if mode in {"simulate", "dry_run"}:
        context["planning_model_id"] = MODEL_ID
    return {
        "schema_version": 2,
        "command": command,
        "arguments": {} if arguments is None else arguments,
        "job_id": job_id,
        "context": context,
    }


def _sine_arguments() -> dict[str, object]:
    return {"frequency_hz": 1000, "amplitude_vpp": 0.1}


def _admitted_job(
    command: str,
    arguments: dict[str, object],
    *,
    mode: str = "simulate",
) -> JobRecord:
    validated = validate_worker_command_request(
        _payload(command, arguments, mode=mode),
        worker_mode="live" if mode == "live" else "simulate",
        allow_output_writes=True,
    )
    return JobRecord(
        worker_job_id="worker-job-1",
        job_id=validated.job_id,
        command=validated.command,
        arguments=validated.arguments,
        context=validated.context,
        state="running",
        accepted_at="2026-01-01T00:00:00Z",
    )


def _wait_for_job(runtime: WorkerRuntime, worker_job_id: str, state: str) -> dict:
    latest: dict = {}

    def completed() -> bool:
        nonlocal latest
        _, status = _request(runtime, "GET", "/status")
        last_job = status.get("last_job")
        if isinstance(last_job, dict):
            latest = last_job
        return (
            isinstance(last_job, dict)
            and last_job.get("worker_job_id") == worker_job_id
            and last_job.get("state") == state
        )

    assert _wait_until(completed)
    return latest


@pytest.mark.parametrize(
    ("argv", "error_text"),
    [
        (["worker", "--mode", "live"], "resource"),
        (["worker", "--mode", "simulate", "--resource", "USB0::SIM"], "resource"),
        (["worker", "--mode", "simulate", "--backend", "@py"], "system backend"),
        (["worker", "--mode", "simulate", "--control-port", "65536"], "control port"),
        (["worker", "--mode", "simulate"], None),
    ],
)
def test_worker_cli_startup_validation(argv, error_text, monkeypatch, capsys):
    captured: dict[str, WorkerConfig] = {}

    def fake_run_worker(config):
        captured["config"] = config
        return 0

    monkeypatch.setattr(
        cli_module,
        "run_worker",
        fake_run_worker,
    )

    if error_text is not None:
        with pytest.raises(SystemExit) as error:
            cli_module.main(argv)
        assert error.value.code == 2
        assert error_text in capsys.readouterr().err
        assert "config" not in captured
    else:
        assert cli_module.main(argv) == 0
        assert captured["config"] == validate_worker_startup(
            mode="simulate",
            resource=None,
            backend="system",
            control_port=0,
            allow_output_writes=False,
        )


@pytest.mark.parametrize(
    ("command", "arguments", "request_mode"),
    [
        ("identify", {}, "simulate"),
        ("status", {}, "simulate"),
        ("read-errors", {}, "simulate"),
        (
            "configure-sine",
            {"frequency_hz": 1000, "high_level_v": 3.3, "low_level_v": 0.0},
            "simulate",
        ),
        (
            "configure_sine_sweep",
            {
                "start_frequency_hz": 1000,
                "stop_frequency_hz": 10000,
                "spacing": "linear",
                "sweep_time_s": 1,
                "amplitude_vpp": 0.1,
            },
            "simulate",
        ),
        (
            "configure_square_sweep",
            {
                "start_frequency_hz": 1000,
                "stop_frequency_hz": 30000,
                "spacing": "linear",
                "sweep_time_s": 1,
                "amplitude_vpp": 0.1,
                "duty_cycle_percent": 25,
            },
            "simulate",
        ),
        (
            "configure_ramp_sweep",
            {
                "start_frequency_hz": 10000,
                "stop_frequency_hz": 1000,
                "spacing": "logarithmic",
                "sweep_time_s": 2,
                "amplitude_vpp": 0.1,
                "symmetry_percent": 40,
            },
            "simulate",
        ),
        (
            "configure_triangle_sweep",
            {
                "start_frequency_hz": 200000,
                "stop_frequency_hz": 1000,
                "spacing": "logarithmic",
                "sweep_time_s": 2,
                "amplitude_vpp": 0.1,
            },
            "simulate",
        ),
        (
            "configure-square",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1},
            "simulate",
        ),
        (
            "configure-ramp",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1},
            "simulate",
        ),
        (
            "configure-triangle",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1, "phase_deg": 90.0},
            "simulate",
        ),
        (
            "configure-pulse",
            {
                "frequency_hz": 1000,
                "amplitude_vpp": 0.1,
                "pulse_width_s": 0.0001,
                "leading_edge_s": 1e-8,
                "trailing_edge_s": 2e-8,
            },
            "simulate",
        ),
        ("configure-dc", {"voltage_v": 1.0}, "simulate"),
        (
            "configure-noise",
            {"amplitude_vpp": 0.1, "bandwidth_hz": 1_000_000},
            "simulate",
        ),
        (
            "configure-prbs",
            {"bit_rate_bps": 1_000_000, "amplitude_vpp": 0.1},
            "simulate",
        ),
        ("output", {"enabled": True, "confirm_output": True}, "simulate"),
        ("configure-sine", _sine_arguments(), "dry_run"),
        (
            "configure_square_sweep",
            {
                "start_frequency_hz": 1000,
                "stop_frequency_hz": 30000,
                "spacing": "linear",
                "sweep_time_s": 1,
                "amplitude_vpp": 0.1,
                "duty_cycle_percent": 25,
            },
            "dry_run",
        ),
        (
            "configure_ramp_sweep",
            {
                "start_frequency_hz": 10000,
                "stop_frequency_hz": 1000,
                "spacing": "logarithmic",
                "sweep_time_s": 2,
                "amplitude_vpp": 0.1,
                "symmetry_percent": 40,
            },
            "dry_run",
        ),
        (
            "configure_triangle_sweep",
            {
                "start_frequency_hz": 200000,
                "stop_frequency_hz": 1000,
                "spacing": "logarithmic",
                "sweep_time_s": 2,
                "amplitude_vpp": 0.1,
            },
            "dry_run",
        ),
        (
            "configure-triangle",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1},
            "dry_run",
        ),
        ("identify", {}, "live"),
    ],
)
def test_worker_command_execution_mapping(
    command, arguments, request_mode, monkeypatch
):
    if request_mode == "live":
        calls: list[tuple[object, object, dict[str, object]]] = []

        def fake_identify(resource, backend, **kwargs):
            calls.append((resource, backend, kwargs))
            return {"resource": resource, "backend": backend}

        monkeypatch.setattr(worker_module, "identify_instrument", fake_identify)
        runtime = WorkerRuntime(_worker_config("live"))
        result = runtime._execute_command(
            _admitted_job(command, arguments, mode="live")
        )
        assert result["resource"] == runtime.config.resource
        assert result["backend"] == "system"
        assert calls == [(runtime.config.resource, "system", {})]
        assert runtime.simulator_state is None
        return

    adapter_calls = []
    if command in {
        "configure_square_sweep",
        "configure_ramp_sweep",
        "configure_triangle_sweep",
    }:
        adapter_function_name = (
            command
            if request_mode == "simulate"
            else command.replace("configure_", "dry_run_", 1)
        )
        real_adapter = getattr(worker_module, adapter_function_name)

        def spy_adapter(*args, **kwargs):
            adapter_calls.append((args, kwargs))
            return real_adapter(*args, **kwargs)

        monkeypatch.setattr(worker_module, adapter_function_name, spy_adapter)

    runtime = WorkerRuntime(_worker_config())
    result = runtime._execute_command(
        _admitted_job(command, arguments, mode=request_mode)
    )
    assert isinstance(result, dict) or hasattr(result, "__dataclass_fields__")
    if command == "output":
        assert runtime.simulator_state is not None
        assert runtime.simulator_state.output_enabled is True
    if command == "configure-triangle" and arguments.get("phase_deg") == 90.0:
        assert result.phase_deg == 90.0
    if command == "configure-sine" and "high_level_v" in arguments:
        assert result.amplitude_vpp == 3.3
        assert result.offset_v == 1.65
    if command in {
        "configure_square_sweep",
        "configure_ramp_sweep",
        "configure_triangle_sweep",
    }:
        assert adapter_calls
        assert result.spacing == arguments["spacing"]
        assert result.trigger_source == "immediate"
        if command == "configure_square_sweep":
            assert result.duty_cycle_percent == 25.0
            assert adapter_calls[0][1]["duty_cycle_percent"] == 25
        elif command == "configure_ramp_sweep":
            assert result.symmetry_percent == 40.0
            assert adapter_calls[0][1]["symmetry_percent"] == 40
        if request_mode == "simulate":
            assert runtime.simulator_state is not None
            assert runtime.simulator_state.frequency_mode == "SWEep"
            assert runtime.simulator_state.output_enabled is False
    if command == "configure_sine_sweep":
        assert result.spacing == "linear"
        assert result.trigger_source == "immediate"
        assert runtime.simulator_state is not None
        assert runtime.simulator_state.frequency_mode == "SWEep"
    if request_mode == "dry_run":
        assert result.commands


def test_worker_status_and_invalid_request_are_memory_only(monkeypatch):
    runtime = WorkerRuntime(_worker_config())
    runtime.start()
    try:
        runtime.simulator_state.error_queue.append('-100,"fixture"')
        status_code, status = _request(runtime, "GET", "/status")
        assert status_code == 200
        assert status["status"] == "ready"
        assert status["active_job"] is None

        executed: list[str] = []
        monkeypatch.setattr(
            runtime,
            "_execute_command",
            lambda job: executed.append(job.command),
        )
        invalid_code, invalid = _request(
            runtime,
            "POST",
            "/command",
            {"schema_version": 1, "command": "status", "context": {"mode": "simulate"}},
        )
        assert invalid_code == 400
        assert invalid["error"] == "invalid_request"
        assert invalid["run_id"] == runtime.run_id
        assert executed == []
        assert runtime.simulator_state.error_queue == ['-100,"fixture"']
    finally:
        runtime.request_stop()
        assert runtime.wait(timeout=2.0)


def test_worker_background_runner_rejects_busy_jobs(monkeypatch):
    runtime = WorkerRuntime(_worker_config())
    started = threading.Event()
    release = threading.Event()

    def blocking_execute(job):
        started.set()
        assert release.wait(timeout=2.0)
        return {"command": job.command}

    monkeypatch.setattr(runtime, "_execute_command", blocking_execute)
    runtime.start()
    try:
        first_code, first = _request(
            runtime, "POST", "/command", _payload("status", job_id="first")
        )
        assert first_code == 202
        assert started.wait(timeout=1.0)
        second_code, second = _request(
            runtime, "POST", "/command", _payload("status", job_id="second")
        )
        assert second_code == 409
        assert second["command"] == "status"
        assert second["job_id"] == "second"
        assert second["run_id"] == runtime.run_id
        assert second["reason"] == "busy"
        release.set()
        last_job = _wait_for_job(runtime, first["worker_job_id"], "succeeded")
        assert last_job["job_id"] == "first"
        assert _request(runtime, "GET", "/status")[1]["status"] == "ready"
    finally:
        release.set()
        runtime.request_stop()
        assert runtime.wait(timeout=2.0)


def test_worker_shared_simulator_state_persists_across_commands():
    runtime = WorkerRuntime(_worker_config())
    runtime.start()
    try:
        for index, (command, arguments) in enumerate(
            [
                ("configure-square", {"frequency_hz": 1000, "amplitude_vpp": 0.1}),
                ("output", {"enabled": True, "confirm_output": True}),
                ("status", {}),
            ]
        ):
            code, accepted = _request(
                runtime,
                "POST",
                "/command",
                _payload(command, arguments, job_id=f"state-{index}"),
            )
            assert code == 202
            last_job = _wait_for_job(runtime, accepted["worker_job_id"], "succeeded")
            if command == "status":
                assert last_job["result"]["function"] == "SQUARE"
                assert last_job["result"]["output_state"] == "on"

        assert runtime.simulator_state.active_function == "SQUARE"
        assert runtime.simulator_state.output_enabled is True
        runtime.simulator_state.error_queue.append('-200,"fixture"')
        status_code, status_accepted = _request(
            runtime,
            "POST",
            "/command",
            _payload("status", job_id="state-status"),
        )
        assert status_code == 202
        _wait_for_job(runtime, status_accepted["worker_job_id"], "succeeded")
        assert runtime.simulator_state.error_queue == ['-200,"fixture"']
        read_code, read_accepted = _request(
            runtime,
            "POST",
            "/command",
            _payload("read-errors", job_id="state-errors"),
        )
        assert read_code == 202
        read_job = _wait_for_job(runtime, read_accepted["worker_job_id"], "succeeded")
        assert read_job["result"]["errors"][0]["message"] == "fixture"
        assert runtime.simulator_state.error_queue == []
    finally:
        runtime.request_stop()
        assert runtime.wait(timeout=2.0)


def test_worker_job_failure_returns_ready_and_recovers(monkeypatch):
    runtime = WorkerRuntime(_worker_config())
    calls = 0

    def fail_once(job):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WavegenError("representative failure")
        return {"ok": True}

    monkeypatch.setattr(runtime, "_execute_command", fail_once)
    runtime.start()
    try:
        failed_code, failed = _request(
            runtime, "POST", "/command", _payload("status", job_id="failed")
        )
        assert failed_code == 202
        failed_job = _wait_for_job(runtime, failed["worker_job_id"], "failed")
        assert failed_job["error"] == {
            "code": "wavegen_error",
            "message": "representative failure",
        }
        status = _request(runtime, "GET", "/status")[1]
        assert status["status"] == "ready"
        assert status["fatal_error"] is None

        succeeded_code, succeeded = _request(
            runtime, "POST", "/command", _payload("status", job_id="succeeded")
        )
        assert succeeded_code == 202
        succeeded_job = _wait_for_job(runtime, succeeded["worker_job_id"], "succeeded")
        assert succeeded_job["result"] == {"ok": True}
    finally:
        runtime.request_stop()
        assert runtime.wait(timeout=2.0)


def test_worker_cooperative_stop_emits_lifecycle_events(capsys, monkeypatch):
    runtime = WorkerRuntime(_worker_config())
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def blocking_execute(job):
        calls.append(job.command)
        started.set()
        assert release.wait(timeout=2.0)
        return {"ok": True}

    monkeypatch.setattr(runtime, "_execute_command", blocking_execute)
    runtime.start()
    try:
        command_code, accepted = _request(
            runtime, "POST", "/command", _payload("status", job_id="stop-job")
        )
        assert command_code == 202
        assert started.wait(timeout=1.0)
        stop_code, stop = _request(runtime, "POST", "/stop")
        assert stop_code == 202
        assert stop["run_id"] == runtime.run_id
        assert _request(runtime, "GET", "/status")[1]["status"] == "stopping"
        rejected_code, rejected = _request(
            runtime, "POST", "/command", _payload("status", job_id="late")
        )
        assert rejected_code == 409
        assert rejected["command"] == "status"
        assert rejected["job_id"] == "late"
        assert rejected["run_id"] == runtime.run_id
        assert rejected["reason"] == "stopping"
        assert calls == ["status"]
        release.set()
        assert runtime.wait(timeout=2.0)
    finally:
        release.set()
        runtime.request_stop()
        assert runtime.wait(timeout=2.0)

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    event_names = [event["event"] for event in events]
    for required in (
        "ready",
        "job_accepted",
        "job_started",
        "job_finished",
        "stop_requested",
        "summary",
    ):
        assert required in event_names
    assert event_names.index("ready") < event_names.index("job_accepted")
    assert event_names.index("job_started") < event_names.index("job_finished")
    assert event_names[-1] == "summary"
    assert {event["run_id"] for event in events} == {runtime.run_id}
    assert events[-1]["exit_code"] == 0


def test_worker_serialization_keeps_core_channel_field_out_of_worker_results():
    result = worker_module._json_safe(
        cli_module.dry_run_sine(
            MODEL_ID,
            1000,
            0.1,
            channel=1,
        )
    )

    assert isinstance(result, dict)
    assert "channel" not in result
