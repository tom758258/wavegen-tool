import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from argparse import Namespace

import pytest

import wavegen_tool_cli.cli as cli_module
import wavegen_tool_cli.lifecycle_client as client


MODEL_ID = "keysight-33521b"


def _status_payload(status="ready"):
    return {
        "schema_version": 3, "service": "wavegen-tool", "run_id": "run-1",
        "status": status,
        "worker_state": {"ready": "running", "busy": "running", "stopping": "stopping", "error": "failed"}[status],
        "fatal_error": (
            {"category": "worker", "code": "internal_error", "message": "fixture", "details": {}}
            if status == "error" else None
        ),
        "timestamp_utc": "2026-10-23T00:00:00Z",
        "command_url": "http://127.0.0.1:8765/command",
        "status_url": "http://127.0.0.1:8765/status",
        "stop_url": "http://127.0.0.1:8765/stop",
        "features": {"common.async_jobs": {"contract_version": 1}},
        "active_job": None, "last_job": None,
    }


def _send_args(
    *,
    arguments_json: str = "{}",
    context_json: str = json.dumps(
        {"mode": "simulate", "planning_model_id": MODEL_ID}
    ),
) -> Namespace:
    return Namespace(
        port=8765,
        timeout_ms=1000,
        json_output=True,
        worker_command="status",
        arguments_json=arguments_json,
        context_json=context_json,
        job_id="job-1",
    )


def _status_args(**overrides: object) -> Namespace:
    values = {
        "port": 8765,
        "timeout_ms": 1000,
        "json_output": True,
    }
    values.update(overrides)
    return Namespace(**values)


def _outcome(
    *,
    status: int | None,
    payload: dict[str, object] | None,
    error: str | None = None,
    message: str | None = None,
    request_sent: bool = True,
    reachable: bool | None = True,
) -> client._HTTPOutcome:
    return client._HTTPOutcome(
        status=status,
        payload=payload,
        error=error,
        message=message,
        request_sent=request_sent,
        reachable=reachable,
        elapsed_ms=1,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("arguments_json", "{"),
        ("arguments_json", "[]"),
        ("context_json", "{"),
        ("context_json", "null"),
    ],
)
def test_lifecycle_local_json_validation_does_not_send_request(
    monkeypatch, capsys, field, value
):
    calls = []
    monkeypatch.setattr(client, "_request_json", lambda *args, **kwargs: calls.append(args))
    args = _send_args()
    setattr(args, field, value)

    assert client.run_send_command(args) == 2

    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == 2
    assert type(payload["schema_version"]) is int
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert payload["request_sent"] is False
    assert payload["reachable"] is None
    assert payload["http_status"] is None
    assert payload["error_phase"] == "validation"
    assert calls == []


@pytest.mark.parametrize(
    ("operation", "outcome", "expected_code", "expected_error"),
    [
        (
            "send",
            _outcome(
                status=202,
                payload={
                    "schema_version": 3,
                    "status": "accepted",
                    "command": "status",
                    "job_id": "job-1",
                    "worker_job_id": "worker-1",
                    "kind": "async_job", "error": None,
                    "run_id": "run-1",
                    "ok": False, "message": "worker diagnostic", "exit_code": 99,
                    "timeout_ms": -1, "request_sent": False,
                },
            ),
            0,
            None,
        ),
        (
            "send_missing_job_id",
            _outcome(
                status=202,
                payload={
                    "schema_version": 3,
                    "status": "accepted",
                    "command": "status",
                    "worker_job_id": "worker-1",
                    "kind": "async_job", "error": None,
                    "run_id": "run-1",
                },
            ),
            3,
            "invalid_response",
        ),
        (
            "status",
            _outcome(
                status=200,
                payload=_status_payload(),
            ),
            0,
            None,
        ),
        (
            "stop",
            _outcome(
                status=202,
                payload={
                    "schema_version": 3,
                    "status": "accepted", "error": None,
                    "run_id": "run-1",
                },
            ),
            0,
            None,
        ),
        (
            "send_http_400",
            _outcome(
                status=400,
                payload={
                    "schema_version": 3,
                    "status": "error",
                    "run_id": "run-1", "command": "status", "job_id": "job-1",
                    "kind": "async_job", "worker_job_id": None,
                    "error": {"category": "validation", "code": "invalid_request", "message": "bad request", "details": {}},
                },
            ),
            2,
            "invalid_request",
        ),
        (
            "send_http_409",
            _outcome(
                status=409,
                payload={
                    "schema_version": 3,
                    "status": "rejected",
                    "reason": "busy", "run_id": "run-1", "command": "status", "job_id": "job-1",
                    "kind": "async_job", "worker_job_id": None,
                    "error": {"category": "admission", "code": "busy", "message": "Worker is busy.", "details": {}},
                },
            ),
            3,
            "busy",
        ),
        (
            "stop_200",
            _outcome(status=200, payload={"schema_version": 3, "run_id": "run-1", "status": "accepted", "error": None}),
            0, None,
        ),
        (
            "status_stopping", _outcome(status=200, payload=_status_payload("stopping")), 0, None,
        ),
        (
            "status_http_500",
            _outcome(status=500, payload={
                "schema_version": 3, "run_id": "run-1", "status": "error",
                "error": {"category": "worker", "code": "status_failed", "message": "fixture", "details": {}},
            }),
            3, "status_failed",
        ),
        (
            "status_connection",
            _outcome(
                status=None,
                payload=None,
                error="connection",
                message="connection refused",
                reachable=False,
            ),
            3,
            "connection",
        ),
        (
            "status_invalid_response",
            _outcome(
                status=200,
                payload=None,
                error="invalid_response",
                message="Worker response must be a JSON object.",
            ),
            3,
            "invalid_response",
        ),
        (
            "status_error",
            _outcome(
                status=200,
                payload=_status_payload("error"),
            ),
            3,
            "worker_error",
        ),
    ],
)
def test_lifecycle_http_outcome_mapping(
    monkeypatch, capsys, operation, outcome, expected_code, expected_error
):
    monkeypatch.setattr(client, "_request_json", lambda *args, **kwargs: outcome)
    if operation.startswith("send"):
        args = _send_args()
        if operation == "send_missing_job_id":
            args.job_id = None
        code = client.run_send_command(args)
    elif operation == "status" or operation.startswith("status_"):
        args = _status_args()
        code = client.run_worker_status(args)
    else:
        args = _status_args()
        code = client.run_worker_stop(args)

    assert code == expected_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert type(payload["schema_version"]) is int
    assert payload["ok"] is (expected_code == 0)
    if outcome.payload is not None:
        assert payload["worker_response"] == outcome.payload
        assert payload["worker_response"]["schema_version"] == 3
    if expected_code == 0:
        assert payload["error_phase"] is None
    else:
        assert payload["error"] == expected_error
        assert payload["exit_code"] == expected_code
        assert payload["request_sent"] is True
    if operation == "send":
        assert payload["command"] == "status"
        assert payload["job_id"] == "job-1"
        assert payload["worker_job_id"] == "worker-1"
        assert "exit_code" not in payload and "message" not in payload
        assert payload["timeout_ms"] == args.timeout_ms and payload["request_sent"] is True
        assert payload["worker_response"]["ok"] is False
    if operation == "send_missing_job_id":
        assert payload["message"] == "Worker accepted response must contain job_id."
        assert payload["error_phase"] == "invalid_response"


def test_wait_ready_retries_connection_and_busy_then_times_out(
    monkeypatch, capsys
):
    clock = [0.0]
    monkeypatch.setattr(client.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    ready_payload = _status_payload()
    busy_payload = _status_payload("busy")
    outcomes = iter(
        [
            _outcome(
                status=None,
                payload=None,
                error="connection",
                message="connection refused",
                reachable=False,
            ),
            _outcome(status=200, payload=busy_payload),
            _outcome(status=200, payload=ready_payload),
        ]
    )
    monkeypatch.setattr(client, "_request_json", lambda *args, **kwargs: next(outcomes))
    args = _status_args(wait_timeout_ms=100, poll_ms=10)

    assert client.run_wait_ready(args) == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out)["status"] == "ready"

    clock[0] = 0.0
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda *args, **kwargs: _outcome(
            status=None,
            payload=None,
            error="connection",
            message="connection refused",
            reachable=False,
        ),
    )
    assert client.run_wait_ready(_status_args(wait_timeout_ms=25, poll_ms=10)) == 3
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    timeout_payload = json.loads(captured.out)
    assert timeout_payload["schema_version"] == 2
    assert timeout_payload["error"] == "wait_timeout"
    assert timeout_payload["error_phase"] == "wait_timeout"
    assert timeout_payload["exit_code"] == 3

    for status, expected_error in (("stopping", "stopping"), ("error", "worker_error")):
        clock[0] = 0.0
        monkeypatch.setattr(client, "_request_json", lambda *a, **kw: _outcome(
            status=200, payload=_status_payload(status)
        ))
        assert client.run_wait_ready(args) == 3
        failure = json.loads(capsys.readouterr().out)
        assert failure["error"] == expected_error and failure["error_phase"] == "worker_status"

    clock[0] = 0.0
    monkeypatch.setattr(client, "_request_json", lambda *a, **kw: _outcome(
        status=200, payload=busy_payload
    ))
    assert client.run_wait_ready(_status_args(wait_timeout_ms=25, poll_ms=10)) == 3
    timeout_payload = json.loads(capsys.readouterr().out)
    assert timeout_payload["error"] == "wait_timeout"
    assert timeout_payload["worker_response"]["worker_state"] == "running"


def test_lifecycle_rejects_invalid_raw_worker_responses(monkeypatch, capsys):
    raw = [b""]

    class Response:
        status = 200

        def read(self):
            return raw[0]

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(client, "HTTPConnection", Connection)
    valid = json.dumps(_status_payload())
    raw[0] = valid.encode()
    assert client.run_worker_status(_status_args()) == 0
    assert json.loads(capsys.readouterr().out)["worker_response"]["schema_version"] == 3
    invalid = [
        valid.replace('"schema_version": 3', '"schema_version": ' + token)
        for token in ('"3"', 'true', '3.0', '3e0', '2')
    ] + [json.dumps(_status_payload() | changes) for changes in (
        {"worker_state": "running", "fatal_error": _status_payload("error")["fatal_error"]},
        {"features": {"common.streaming": {"contract_version": 1}}},
        {"features": {"common.async_jobs": {"contract_version": True}}},
        {"last_job": {"state": "succeeded"}},
    )]
    for value in invalid:
        raw[0] = value.encode()
        assert client.run_worker_status(_status_args()) == 3
        failure = json.loads(capsys.readouterr().out)
        assert failure["schema_version"] == 2 and failure["ok"] is False
        assert failure["error"] == "invalid_response"
        assert failure["request_sent"] is True and failure["reachable"] is True
        assert failure["http_status"] == 200


def test_lifecycle_clients_complete_simulate_worker_subprocess(capsys):
    repository_root = Path(__file__).resolve().parents[2]
    source_root = repository_root / "src"
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(source_root)
        if not existing_pythonpath
        else str(source_root) + os.pathsep + existing_pythonpath
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from wavegen_tool_cli.cli import main; raise SystemExit(main())",
            "worker",
            "--mode",
            "simulate",
            "--control-port",
            "0",
        ],
        cwd=repository_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout_queue: queue.Queue[str] = queue.Queue()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            stdout_queue.put(line)

    def read_stderr() -> None:
        assert process.stderr is not None
        stderr_lines.extend(process.stderr)

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    try:
        ready = None
        deadline = time.monotonic() + 5.0
        while ready is None and time.monotonic() < deadline:
            try:
                line = stdout_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if line.strip():
                ready = json.loads(line)
        assert ready is not None
        assert ready["event"] == "ready"
        assert ready["schema_version"] == 3
        assert ready["features"] == {"common.async_jobs": {"contract_version": 1}}
        port = ready["control_port"]
        assert isinstance(port, int) and port > 0

        wait_code = cli_module.main(
            [
                "wait-ready",
                "--port",
                str(port),
                "--timeout-ms",
                "500",
                "--wait-timeout-ms",
                "3000",
                "--poll-ms",
                "25",
                "--json",
            ]
        )
        wait_payload = json.loads(capsys.readouterr().out)
        assert wait_code == 0
        assert wait_payload["schema_version"] == 2
        worker_status = wait_payload["worker_response"]
        assert worker_status["schema_version"] == 3
        assert worker_status["run_id"] == ready["run_id"]
        assert worker_status["worker_state"] == "running" and worker_status["fatal_error"] is None
        for key in ("features", "command_url", "status_url", "stop_url"):
            assert worker_status[key] == ready[key]
        assert wait_payload["status"] == "ready"

        send_code = cli_module.main(
            [
                "send-command",
                "--port",
                str(port),
                "--command",
                "status",
                "--context-json",
                json.dumps({"mode": "simulate", "planning_model_id": MODEL_ID}),
                "--job-id",
                "p3-status",
                "--json",
            ]
        )
        send_payload = json.loads(capsys.readouterr().out)
        assert send_code == 0
        assert send_payload["schema_version"] == 2
        assert send_payload["worker_response"]["kind"] == "async_job"
        assert send_payload["worker_response"]["error"] is None
        worker_job_id = send_payload["worker_job_id"]

        last_job = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status_code = cli_module.main(
                ["worker-status", "--port", str(port), "--json"]
            )
            status_payload = json.loads(capsys.readouterr().out)
            assert status_code == 0
            candidate = status_payload.get("last_job")
            if (
                isinstance(candidate, dict)
                and candidate.get("worker_job_id") == worker_job_id
                and candidate.get("state") == "succeeded"
            ):
                last_job = candidate
                break
            time.sleep(0.02)
        assert last_job is not None
        assert last_job["worker_job_id"] == worker_job_id

        stop_code = cli_module.main(["worker-stop", "--port", str(port), "--json"])
        stop_payload = json.loads(capsys.readouterr().out)
        assert stop_code == 0
        assert stop_payload["status"] == "accepted"
        assert process.wait(timeout=5.0) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    assert process.poll() is not None
    assert stderr_lines == []
    events = [json.loads(line) for line in stdout_lines if line.strip()]
    assert all(isinstance(event, dict) for event in events)
    names = [event["event"] for event in events]
    for required in (
        "ready",
        "job_accepted",
        "job_started",
        "job_finished",
        "stop_requested",
        "summary",
    ):
        assert required in names
    assert {event["run_id"] for event in events} == {ready["run_id"]}
    summary = next(event for event in events if event["event"] == "summary")
    assert summary["exit_code"] == 0 and summary["ok"] is True
    assert summary["fatal_error"] is None
    assert (summary["accepted_jobs"], summary["succeeded_jobs"], summary["failed_jobs"]) == (1, 1, 0)
    assert all(event["schema_version"] == 3 for event in events)
    terminals = [event for event in events if event["event"] == "job_finished"]
    assert len(terminals) == 1
    for key in ("worker_job_id", "job_id", "command", "timestamp_utc", "state", "ok", "result", "error"):
        assert terminals[0][key] == last_job[key]
    assert not stdout_thread.is_alive() and not stderr_thread.is_alive()  # stdout/stderr EOF
    assert process.returncode == 0
