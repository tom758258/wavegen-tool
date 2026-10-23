"""Loopback lifecycle clients for the local Wavegen Worker."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
import json
import sys
import time
from typing import Any
from wavegen_tool_cli.worker_protocol import (
    WORKER_SCHEMA_VERSION,
    WORKER_SERVICE_NAME,
    worker_features,
)


_SUCCESS = 0
_CLI_USAGE = 2
_RUNTIME_ERROR = 3
_HOST = "127.0.0.1"
_STATUSES = frozenset({"ready", "busy", "stopping", "error"})
LIFECYCLE_CLIENT_SCHEMA_VERSION = 2
_CLIENT_FIELDS = frozenset({
    "schema_version", "ok", "error", "message", "exit_code", "worker_response",
    "client_command", "method", "url", "endpoint", "timeout_ms", "elapsed_ms",
    "request_sent", "reachable", "http_status", "error_phase",
})


@dataclass(frozen=True)
class _HTTPOutcome:
    status: int | None
    payload: dict[str, object] | None
    error: str | None
    message: str | None
    request_sent: bool
    reachable: bool | None
    elapsed_ms: int


def run_send_command(args: Any) -> int:
    """Submit one command request without waiting for its job."""

    endpoint = "/command"
    url = _url(args.port, endpoint)
    arguments, error = _parse_object(args.arguments_json, "arguments")
    if error:
        return _emit(args, _local_error(args, endpoint, url, error), "")
    context, error = _parse_object(args.context_json, "context")
    if error:
        return _emit(args, _local_error(args, endpoint, url, error), "")

    request: dict[str, object] = {
        "schema_version": WORKER_SCHEMA_VERSION,
        "command": args.worker_command,
        "arguments": arguments,
        "context": context,
    }
    if args.job_id is not None:
        request["job_id"] = args.job_id
    outcome = _request_json("POST", args.port, endpoint, request, args.timeout_ms)

    if outcome.error:
        result = _error_result(
            "send-command",
            "POST",
            endpoint,
            url,
            args.timeout_ms,
            outcome,
            error=outcome.error,
            message=outcome.message or "Worker response was invalid.",
            command=args.worker_command,
            job_id=args.job_id,
        )
    elif outcome.status == 202:
        validation_error = _validate_send(outcome.payload, args.worker_command, args.job_id)
        if validation_error:
            result = _error_result(
                "send-command",
                "POST",
                endpoint,
                url,
                args.timeout_ms,
                outcome,
                error="invalid_response",
                message=validation_error,
                phase="invalid_response",
                command=args.worker_command,
                job_id=args.job_id,
            )
        else:
            result = _success_result(
                "send-command", "POST", endpoint, url, args.timeout_ms, outcome
            )
    else:
        result = _http_error(
            "send-command",
            "POST",
            endpoint,
            url,
            args.timeout_ms,
            outcome,
            command=args.worker_command,
            job_id=args.job_id,
        )
    worker_job_id = _string(result.get("worker_job_id")) or ""
    return _emit(
        args,
        result,
        f"Worker command accepted: {args.worker_command} ({worker_job_id})",
    )


def run_worker_status(args: Any) -> int:
    """Read the Worker's memory-only lifecycle status."""

    endpoint = "/status"
    url = _url(args.port, endpoint)
    outcome = _request_json("GET", args.port, endpoint, None, args.timeout_ms)
    if outcome.error:
        result = _error_result(
            "worker-status",
            "GET",
            endpoint,
            url,
            args.timeout_ms,
            outcome,
            error=outcome.error,
            message=outcome.message or "Worker response was invalid.",
        )
    elif outcome.status != 200:
        result = _http_error(
            "worker-status", "GET", endpoint, url, args.timeout_ms, outcome
        )
    else:
        validation_error = _validate_status(outcome.payload)
        if validation_error:
            result = _error_result(
                "worker-status",
                "GET",
                endpoint,
                url,
                args.timeout_ms,
                outcome,
                error="invalid_response",
                message=validation_error,
                phase="invalid_response",
            )
        elif outcome.payload["status"] == "error":
            result = _error_result(
                "worker-status",
                "GET",
                endpoint,
                url,
                args.timeout_ms,
                outcome,
                payload=outcome.payload,
                error="worker_error",
                message="Worker reported an error.",
                phase="worker_status",
            )
        else:
            result = _success_result(
                "worker-status", "GET", endpoint, url, args.timeout_ms, outcome
            )
    return _emit(args, result, f"Worker status: {result.get('status', '')}")


def run_wait_ready(args: Any) -> int:
    """Poll lifecycle status until the local Worker is ready or terminal."""

    endpoint = "/status"
    url = _url(args.port, endpoint)
    started = time.monotonic()
    deadline = started + args.wait_timeout_ms / 1000.0
    last: _HTTPOutcome | None = None

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _emit(args, _wait_timeout(args, endpoint, url, last, started), "")
        timeout_ms = min(args.timeout_ms, max(1, int(remaining * 1000)))
        outcome = _request_json("GET", args.port, endpoint, None, timeout_ms)
        last = outcome

        if outcome.error:
            if outcome.error != "connection":
                return _emit(
                    args,
                    _error_result(
                        "wait-ready",
                        "GET",
                        endpoint,
                        url,
                        args.timeout_ms,
                        outcome,
                        error=outcome.error,
                        message=outcome.message or "Worker response was invalid.",
                    ),
                    "",
                )
        elif outcome.status != 200:
            return _emit(
                args,
                _http_error(
                    "wait-ready", "GET", endpoint, url, args.timeout_ms, outcome
                ),
                "",
            )
        else:
            validation_error = _validate_status(outcome.payload)
            if validation_error:
                return _emit(
                    args,
                    _error_result(
                        "wait-ready",
                        "GET",
                        endpoint,
                        url,
                        args.timeout_ms,
                        outcome,
                        error="invalid_response",
                        message=validation_error,
                        phase="invalid_response",
                    ),
                    "",
                )
            status = outcome.payload["status"]
            if status == "ready":
                return _emit(
                    args,
                    _success_result(
                        "wait-ready",
                        "GET",
                        endpoint,
                        url,
                        args.timeout_ms,
                        outcome,
                        elapsed_ms=_elapsed_ms(started),
                    ),
                    "Worker status: ready",
                )
            if status in {"stopping", "error"}:
                return _emit(
                    args,
                    _error_result(
                        "wait-ready",
                        "GET",
                        endpoint,
                        url,
                        args.timeout_ms,
                        outcome,
                        payload=outcome.payload,
                        error="worker_error" if status == "error" else "stopping",
                        message=(
                            "Worker reported an error."
                            if status == "error"
                            else "Worker is stopping."
                        ),
                        phase="worker_status",
                    ),
                    "",
                )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            continue
        time.sleep(min(args.poll_ms / 1000.0, remaining))


def run_worker_stop(args: Any) -> int:
    """Submit one cooperative Worker stop request."""

    endpoint = "/stop"
    url = _url(args.port, endpoint)
    outcome = _request_json("POST", args.port, endpoint, {}, args.timeout_ms)
    if outcome.error:
        result = _error_result(
            "worker-stop",
            "POST",
            endpoint,
            url,
            args.timeout_ms,
            outcome,
            error=outcome.error,
            message=outcome.message or "Worker response was invalid.",
        )
    elif outcome.status not in {200, 202}:
        result = _http_error(
            "worker-stop", "POST", endpoint, url, args.timeout_ms, outcome
        )
    else:
        validation_error = _validate_stop(outcome.payload)
        result = (
            _error_result(
                "worker-stop",
                "POST",
                endpoint,
                url,
                args.timeout_ms,
                outcome,
                error="invalid_response",
                message=validation_error,
                phase="invalid_response",
            )
            if validation_error
            else _success_result(
                "worker-stop", "POST", endpoint, url, args.timeout_ms, outcome
            )
        )
    return _emit(args, result, "Worker stop accepted.")


def _request_json(
    method: str,
    port: int,
    endpoint: str,
    request: dict[str, object] | None,
    timeout_ms: int,
) -> _HTTPOutcome:
    started = time.monotonic()
    connection: HTTPConnection | None = None
    request_sent = False
    try:
        connection = HTTPConnection(_HOST, port, timeout=timeout_ms / 1000.0)
        headers = {"Accept": "application/json"}
        body = None
        if request is not None:
            body = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json; charset=utf-8"
        request_sent = True
        connection.request(method, endpoint, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
    except (HTTPException, OSError, TimeoutError) as exc:
        return _HTTPOutcome(
            None,
            None,
            "connection",
            _safe_message(exc, "Worker connection failed."),
            request_sent,
            False,
            _elapsed_ms(started),
        )
    finally:
        if connection is not None:
            connection.close()

    if not raw.strip():
        return _invalid_response(status, request_sent, "Worker response body must not be empty.", started)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _invalid_response(status, request_sent, "Worker response must be valid UTF-8 JSON.", started)
    if not isinstance(payload, dict):
        return _invalid_response(status, request_sent, "Worker response must be a JSON object.", started)
    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != WORKER_SCHEMA_VERSION:
        return _invalid_response(
            status,
            request_sent,
            f"Worker response schema_version must be exact integer {WORKER_SCHEMA_VERSION}.",
            started,
        )
    if not _nonempty(payload.get("run_id")):
        return _invalid_response(status, request_sent, "Worker response must contain a run_id.", started)
    if status >= 400:
        expected_status = "rejected" if status in {409, 429} else "error"
        if payload.get("status") != expected_status or not _valid_error(payload.get("error")):
            return _invalid_response(status, request_sent, "Worker error response is invalid.", started)
        if endpoint == "/command" and not _valid_command_fields(payload, accepted=False):
            return _invalid_response(status, request_sent, "Worker command response is invalid.", started)
    return _HTTPOutcome(
        status,
        payload,
        None,
        None,
        request_sent,
        True,
        _elapsed_ms(started),
    )


def _invalid_response(
    status: int,
    request_sent: bool,
    message: str,
    started: float,
) -> _HTTPOutcome:
    return _HTTPOutcome(
        status,
        None,
        "invalid_response",
        message,
        request_sent,
        True,
        _elapsed_ms(started),
    )


def _parse_object(value: str, name: str) -> tuple[dict[str, object] | None, str | None]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None, f"--{name}-json must contain valid JSON."
    if not isinstance(parsed, dict):
        return None, f"--{name}-json must contain a JSON object."
    return parsed, None


def _validate_send(
    payload: dict[str, object] | None,
    command: str,
    job_id: str | None,
) -> str | None:
    if payload is None:
        return "Worker response is missing."
    if payload.get("status") != "accepted":
        return "Worker accepted response must have status=accepted."
    if payload.get("command") != command:
        return "Worker accepted response command does not match the request."
    if "job_id" not in payload:
        return "Worker accepted response must contain job_id."
    if payload["job_id"] != job_id:
        return "Worker accepted response job_id does not match the request."
    if not _nonempty(payload.get("worker_job_id")):
        return "Worker accepted response must contain a worker_job_id."
    if not _nonempty(payload.get("run_id")):
        return "Worker accepted response must contain a run_id."
    if not _valid_command_fields(payload, accepted=True):
        return "Worker accepted response must identify an async job with error=null."
    return None


def _validate_status(payload: dict[str, object] | None) -> str | None:
    if payload is None:
        return "Worker response is missing."
    if payload.get("service") != WORKER_SERVICE_NAME:
        return f"Worker status response must identify service={WORKER_SERVICE_NAME}."
    if not _nonempty(payload.get("run_id")):
        return "Worker status response must contain a run_id."
    if not isinstance(payload.get("status"), str) or payload["status"] not in _STATUSES:
        return "Worker status response contains an invalid status."
    state = payload.get("worker_state")
    if state not in ("starting", "running", "stopping", "failed"):
        return "Worker status response contains an invalid worker_state."
    if "fatal_error" not in payload or (
        payload["fatal_error"] is not None and not _valid_error(payload["fatal_error"])
    ):
        return "Worker status response contains an invalid fatal_error."
    if (state == "failed") != (payload["fatal_error"] is not None):
        return "Worker state and fatal_error disagree."
    expected_state = {"ready": "running", "busy": "running", "stopping": "stopping", "error": "failed"}
    if state != expected_state[payload["status"]]:
        return "Worker state and Wavegen status disagree."
    features = payload.get("features")
    if (
        features != worker_features()
        or type(features["common.async_jobs"]["contract_version"]) is not int
    ):
        return "Worker status response contains invalid runtime features."
    if not all(_nonempty(payload.get(key)) for key in (
        "timestamp_utc", "status_url", "command_url", "stop_url"
    )):
        return "Worker status response is missing its timestamp or control URLs."
    for key, terminal in (("active_job", False), ("last_job", True)):
        if key not in payload or not _valid_job_snapshot(payload[key], terminal=terminal):
            return f"Worker status response contains an invalid {key}."
    return None


def _validate_stop(payload: dict[str, object] | None) -> str | None:
    if payload is None:
        return "Worker response is missing."
    if payload.get("status") != "accepted":
        return "Worker stop response must have status=accepted."
    if not _nonempty(payload.get("run_id")):
        return "Worker stop response must contain a run_id."
    if "error" not in payload or payload["error"] is not None:
        return "Worker stop response must contain error=null."
    return None


def _valid_error(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("category") in ("validation", "admission", "execution", "worker")
        and _nonempty(value.get("code"))
        and _nonempty(value.get("message"))
        and isinstance(value.get("details"), dict)
    )


def _valid_command_fields(payload: dict[str, object], *, accepted: bool) -> bool:
    if not (
        "command" in payload and isinstance(payload["command"], (str, type(None)))
        and "job_id" in payload and isinstance(payload["job_id"], (str, type(None)))
        and "kind" in payload and payload["kind"] in ("async_job", None)
        and "worker_job_id" in payload
    ):
        return False
    if accepted:
        return (
            payload["kind"] == "async_job" and _nonempty(payload["worker_job_id"])
            and "error" in payload and payload["error"] is None
        )
    return payload["worker_job_id"] is None


def _valid_job_snapshot(value: object, *, terminal: bool) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    if not (
        _nonempty(value.get("worker_job_id")) and _nonempty(value.get("command"))
        and "job_id" in value and isinstance(value["job_id"], (str, type(None)))
        and _nonempty(value.get("timestamp_utc"))
    ):
        return False
    if not terminal:
        return value.get("state") in ("queued", "running")
    if "result" not in value or "error" not in value or type(value.get("ok")) is not bool:
        return False
    return (
        value.get("state") == "succeeded" and value["ok"] and value["error"] is None
        or value.get("state") == "failed" and not value["ok"] and _valid_error(value["error"])
    )


def _success_result(
    client_command: str,
    method: str,
    endpoint: str,
    url: str,
    timeout_ms: int,
    outcome: _HTTPOutcome,
    *,
    elapsed_ms: int | None = None,
) -> dict[str, object]:
    return _result(
        _worker_fields(outcome.payload),
        client_command,
        method,
        endpoint,
        url,
        timeout_ms,
        outcome,
        ok=True,
        elapsed_ms=elapsed_ms,
    )


def _error_result(
    client_command: str,
    method: str,
    endpoint: str,
    url: str,
    timeout_ms: int,
    outcome: _HTTPOutcome,
    *,
    error: object,
    message: object,
    phase: str | None = None,
    payload: dict[str, object] | None = None,
    command: str | None = None,
    job_id: str | None = None,
    exit_code: int = _RUNTIME_ERROR,
    elapsed_ms: int | None = None,
) -> dict[str, object]:
    result = _worker_fields(payload)
    if command is not None:
        result.setdefault("command", command)
    if job_id is not None:
        result.setdefault("job_id", job_id)
    result["error"] = error
    result["message"] = message
    return _result(
        result,
        client_command,
        method,
        endpoint,
        url,
        timeout_ms,
        outcome,
        ok=False,
        exit_code=exit_code,
        error_phase=phase or str(error),
        elapsed_ms=elapsed_ms,
    )


def _http_error(
    client_command: str,
    method: str,
    endpoint: str,
    url: str,
    timeout_ms: int,
    outcome: _HTTPOutcome,
    *,
    command: str | None = None,
    job_id: str | None = None,
) -> dict[str, object]:
    payload = dict(outcome.payload or {})
    worker_error = payload.get("error")
    error = worker_error.get("code") if isinstance(worker_error, dict) else "http_status"
    message = worker_error.get("message") if isinstance(worker_error, dict) else (
        f"Worker returned HTTP status {outcome.status}."
        if outcome.status is not None
        else "Worker request failed."
    )
    return _error_result(
        client_command,
        method,
        endpoint,
        url,
        timeout_ms,
        outcome,
        payload=payload,
        error=error,
        message=message,
        phase="http_status",
        command=command,
        job_id=job_id,
        exit_code=_CLI_USAGE if outcome.status == 400 else _RUNTIME_ERROR,
    )


def _local_error(args: Any, endpoint: str, url: str, message: str) -> dict[str, object]:
    outcome = _HTTPOutcome(None, None, "invalid_request", message, False, None, 0)
    return _error_result(
        "send-command",
        "POST",
        endpoint,
        url,
        args.timeout_ms,
        outcome,
        error="invalid_request",
        message=message,
        phase="validation",
        command=args.worker_command,
        job_id=args.job_id,
        exit_code=_CLI_USAGE,
    )


def _wait_timeout(
    args: Any,
    endpoint: str,
    url: str,
    outcome: _HTTPOutcome | None,
    started: float,
) -> dict[str, object]:
    if outcome is None:
        outcome = _HTTPOutcome(None, None, "wait_timeout", None, False, None, 0)
    return _error_result(
        "wait-ready",
        "GET",
        endpoint,
        url,
        args.timeout_ms,
        outcome,
        error="wait_timeout",
        message="Worker did not become ready before the deadline.",
        phase="wait_timeout",
        elapsed_ms=_elapsed_ms(started),
    )


def _result(
    payload: dict[str, object] | None,
    client_command: str,
    method: str,
    endpoint: str,
    url: str,
    timeout_ms: int,
    outcome: _HTTPOutcome,
    *,
    ok: bool,
    exit_code: int | None = None,
    error_phase: str | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, object]:
    result = dict(payload or {})
    result["schema_version"] = LIFECYCLE_CLIENT_SCHEMA_VERSION
    if outcome.payload is not None:
        result["worker_response"] = outcome.payload
    result["ok"] = ok
    if exit_code is not None:
        result["exit_code"] = exit_code
    result.update(
        {
            "client_command": client_command,
            "method": method,
            "url": url,
            "endpoint": endpoint,
            "timeout_ms": timeout_ms,
            "elapsed_ms": outcome.elapsed_ms if elapsed_ms is None else elapsed_ms,
            "request_sent": outcome.request_sent,
            "reachable": outcome.reachable,
            "http_status": outcome.status,
            "error_phase": error_phase,
        }
    )
    return result


def _worker_fields(payload: dict[str, object] | None) -> dict[str, object]:
    """Keep existing convenience fields without importing Worker-owned diagnostics."""

    return {key: value for key, value in (payload or {}).items() if key not in _CLIENT_FIELDS}


def _emit(args: Any, payload: dict[str, object], success_text: str) -> int:
    if args.json_output:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    elif payload.get("ok"):
        print(success_text)
    else:
        error = _string(payload.get("error")) or _string(payload.get("reason"))
        message = _string(payload.get("message")) or "Worker request failed."
        print(f"Error [{error or 'request_failed'}]: {message}", file=sys.stderr)
    return int(payload.get("exit_code", _SUCCESS))


def _url(port: int, endpoint: str) -> str:
    return f"http://{_HOST}:{port}{endpoint}"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _safe_message(exc: BaseException, fallback: str) -> str:
    return str(exc).strip() or fallback
