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
                    "schema_version": 2,
                    "status": "accepted",
                    "command": "status",
                    "job_id": "job-1",
                    "worker_job_id": "worker-1",
                    "run_id": "run-1",
                },
            ),
            0,
            None,
        ),
        (
            "status",
            _outcome(
                status=200,
                payload={
                    "schema_version": 2,
                    "service": "wavegen-tool",
                    "run_id": "run-1",
                    "status": "ready",
                },
            ),
            0,
            None,
        ),
        (
            "stop",
            _outcome(
                status=202,
                payload={
                    "schema_version": 2,
                    "status": "accepted",
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
                    "schema_version": 2,
                    "status": "error",
                    "error": "invalid_request",
                    "message": "bad request",
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
                    "schema_version": 2,
                    "status": "rejected",
                    "reason": "busy",
                },
            ),
            3,
            "busy",
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
                payload={
                    "schema_version": 2,
                    "service": "wavegen-tool",
                    "run_id": "run-1",
                    "status": "error",
                    "fatal_error": {"code": "internal_error"},
                },
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


def test_wait_ready_retries_connection_and_busy_then_times_out(
    monkeypatch, capsys
):
    clock = [0.0]
    monkeypatch.setattr(client.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    ready_payload = {
        "schema_version": 2,
        "service": "wavegen-tool",
        "run_id": "run-1",
        "status": "ready",
    }
    busy_payload = {
        "schema_version": 2,
        "service": "wavegen-tool",
        "run_id": "run-1",
        "status": "busy",
    }
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
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

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
    assert summary["exit_code"] == 0
