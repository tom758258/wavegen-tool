"""Exercise Worker CANCEL and STOP through loopback with a simulator only."""

import json
import sys
import time
from http.client import HTTPConnection

from wavegen_tool_cli.worker import WorkerRuntime, validate_worker_startup


def request(runtime, method, path, payload=None, *, status=200):
    connection = HTTPConnection("127.0.0.1", runtime.control_port, timeout=2)
    try:
        body = None if payload is None else json.dumps(payload)
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        if response.status != status:
            raise RuntimeError(f"{method} {path} returned {response.status}, expected {status}.")
        return result
    finally:
        connection.close()


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise RuntimeError("Simulator Worker state did not arrive before the deadline.")


def check(model):
    runtime = WorkerRuntime(validate_worker_startup(
        mode="simulate", resource=None, backend="system", control_port=0,
        allow_output_writes=False,
    ))
    runtime.start()

    def submit(command, arguments):
        return request(runtime, "POST", "/command", {
            "schema_version": 3, "command": command, "arguments": arguments,
            "context": {"mode": "simulate", "planning_model_id": model},
        }, status=202)["worker_job_id"]

    def terminal(job_id):
        snapshot = request(runtime, "GET", "/status")["last_job"]
        return snapshot if snapshot and snapshot["worker_job_id"] == job_id else None

    def output_is_on():
        state = runtime.simulator_state
        return state is not None and state.ch1.output_enabled

    try:
        ramp = submit("run-amplitude-ramp", {
            "start_vpp": 0.1, "stop_vpp": 0.2, "step_vpp": 0.1, "dwell_s": 5,
        })
        wait_for(output_is_on)
        request(runtime, "POST", "/cancel", {"schema_version": 3, "worker_job_id": ramp}, status=202)
        cancelled = wait_for(lambda: terminal(ramp))
        cleanup = cancelled["error"]["details"]["domain_error"]["sequence"]["cleanup"]
        if cancelled["state"] != "cancelled" or cleanup["confirmed_off_channels"] != [1] or output_is_on():
            raise RuntimeError("CANCEL did not finish with confirmed output OFF.")

        sequence = submit("run-software-sequence", {"document": {"version": 1, "steps": [
            {"action": "output", "arguments": {"enabled": True}},
            {"action": "wait", "arguments": {"seconds": 1}},
        ]}})
        wait_for(output_is_on)
        request(runtime, "POST", "/stop", {}, status=202)
        if not runtime.wait(4):
            raise RuntimeError("STOP did not drain the current job.")
        last = runtime.status_payload()["last_job"]
        if last["worker_job_id"] != sequence or last["state"] != "succeeded" or output_is_on():
            raise RuntimeError("STOP interrupted a job or normal job cleanup did not complete.")
    finally:
        runtime.request_stop()
        runtime.wait(4)


if __name__ == "__main__":
    check(sys.argv[1])
