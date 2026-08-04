"""Local Wavegen Worker runtime and loopback control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
import time
import traceback
import uuid

from wavegen_tool_core import (
    SIMULATED_33521B_RESOURCE,
    SYSTEM_BACKEND,
    Simulated33521BState,
    SimulatedResourceManager,
    WavegenError,
    classify_transport,
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
    dry_run_dc,
    dry_run_noise,
    dry_run_prbs,
    dry_run_pulse,
    dry_run_ramp,
    dry_run_ramp_sweep,
    dry_run_sine,
    dry_run_sine_sweep,
    dry_run_square,
    dry_run_square_sweep,
    dry_run_triangle,
    dry_run_triangle_sweep,
    identify_instrument,
    normalize_backend,
    query_status,
    read_error_queue,
    set_output,
    validate_backend_transport,
)
from wavegen_tool_core.transport import normalize_resource
from wavegen_tool_cli.worker_commands import (
    ValidatedWorkerCommand,
    WorkerRequestValidationError,
    validate_worker_command_request,
)

__all__ = [
    "JobRecord",
    "WorkerConfig",
    "WorkerRuntime",
    "run_worker",
    "validate_worker_startup",
]


@dataclass(frozen=True)
class WorkerConfig:
    """Purely validated startup configuration."""

    mode: str
    resource: str | None
    backend: str
    control_port: int
    allow_output_writes: bool


@dataclass
class JobRecord:
    """The small in-memory record retained for one accepted job."""

    worker_job_id: str
    job_id: str | None
    command: str
    arguments: dict[str, object]
    context: dict[str, object]
    state: str
    accepted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: object = None
    error: dict[str, object] | None = None


def validate_worker_startup(
    *,
    mode: str,
    resource: str | None,
    backend: str,
    control_port: int,
    allow_output_writes: bool,
) -> WorkerConfig:
    """Validate startup options without creating a VISA or simulator session."""

    if type(control_port) is not int or not 0 <= control_port <= 65535:
        raise ValueError("control port must be an integer between 0 and 65535.")
    if type(allow_output_writes) is not bool:
        raise ValueError("allow_output_writes must be a boolean.")
    if mode not in {"live", "simulate"}:
        raise ValueError("worker mode must be live or simulate.")

    backend_selection = normalize_backend(backend)
    if mode == "simulate":
        if resource is not None:
            raise ValueError("--resource cannot be used with --mode simulate.")
        if backend_selection.name != SYSTEM_BACKEND:
            raise ValueError("--mode simulate requires the system backend.")
        return WorkerConfig(
            mode=mode,
            resource=None,
            backend=backend_selection.name,
            control_port=control_port,
            allow_output_writes=allow_output_writes,
        )

    if resource is None or not isinstance(resource, str) or not resource.strip():
        raise ValueError("the following arguments are required: --resource")
    normalized_resource = normalize_resource(resource)
    transport = classify_transport(normalized_resource)
    validate_backend_transport(backend_selection, transport)
    return WorkerConfig(
        mode=mode,
        resource=normalized_resource,
        backend=backend_selection.name,
        control_port=control_port,
        allow_output_writes=allow_output_writes,
    )


class WorkerRuntime:
    """One local Worker service with a single cooperative command runner."""

    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self.run_id = str(uuid.uuid4())
        self._condition = threading.Condition(threading.RLock())
        self._event_lock = threading.Lock()
        self._server: _WorkerHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._runner_thread: threading.Thread | None = None
        self._server_started = threading.Event()
        self._runner_done = threading.Event()
        self._service_status = "ready"
        self._active_job: JobRecord | None = None
        self._last_job: JobRecord | None = None
        self._fatal_error: dict[str, object] | None = None
        self._stop_requested = False
        self._accepted_jobs = 0
        self._succeeded_jobs = 0
        self._failed_jobs = 0
        self._summary_emitted = False
        self._exit_code = 0
        self._simulator_state = (
            Simulated33521BState() if config.mode == "simulate" else None
        )

    @property
    def control_port(self) -> int:
        server = self._server
        if server is None:
            return self.config.control_port
        return int(server.server_address[1])

    @property
    def simulator_state(self) -> Simulated33521BState | None:
        return self._simulator_state

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def start(self) -> None:
        """Bind the loopback server and emit ready after serving starts."""
        try:
            self._server = _WorkerHTTPServer(
                ("127.0.0.1", self.config.control_port), self
            )
            self._server_thread = threading.Thread(
                target=self._serve,
                name="wavegen-worker-http",
                daemon=True,
            )
            self._runner_thread = threading.Thread(
                target=self._run_commands,
                name="wavegen-worker-runner",
                daemon=True,
            )
            self._server_thread.start()
            if not self._server_started.wait(timeout=2.0):
                raise RuntimeError("Worker HTTP server did not start.")
            self._wait_until_serving()
            self._emit_event(
                "ready",
                control_port=self.control_port,
                command_url=self.command_url,
                status_url=self.status_url,
                stop_url=self.stop_url,
            )
            self._runner_thread.start()
        except Exception:
            server = self._server
            if server is not None:
                if self._server_started.is_set():
                    server.shutdown()
                if self._server_thread is not None:
                    self._server_thread.join(timeout=1.0)
                server.server_close()
            raise

    @property
    def command_url(self) -> str:
        return f"http://127.0.0.1:{self.control_port}/command"

    @property
    def status_url(self) -> str:
        return f"http://127.0.0.1:{self.control_port}/status"

    @property
    def stop_url(self) -> str:
        return f"http://127.0.0.1:{self.control_port}/stop"

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for cooperative shutdown and close the listening socket."""

        deadline = None
        if timeout is not None:
            deadline = time.monotonic() + timeout
        for thread in (self._runner_thread, self._server_thread):
            if thread is None:
                continue
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            thread.join(remaining)
        done = self._runner_done.is_set() and (
            self._server_thread is None or not self._server_thread.is_alive()
        )
        if done and self._server is not None:
            self._server.server_close()
        if done:
            self._emit_summary()
        return done

    def request_stop(self) -> None:
        """Request a cooperative stop without running a domain command."""

        emit = False
        with self._condition:
            if not self._stop_requested:
                self._stop_requested = True
                self._service_status = "stopping"
                emit = True
                self._condition.notify_all()
        if emit:
            self._emit_event("stop_requested")

    def admit(
        self, command: ValidatedWorkerCommand
    ) -> tuple[str, JobRecord | None]:
        """Reserve the one active slot after admission validation."""

        with self._condition:
            if self._stop_requested or self._service_status in {"stopping", "error"}:
                return "stopping", None
            if self._active_job is not None:
                return "busy", None
            job = JobRecord(
                worker_job_id=str(uuid.uuid4()),
                job_id=command.job_id,
                command=command.command,
                arguments=dict(command.arguments),
                context=dict(command.context),
                state="queued",
                accepted_at=_timestamp(),
            )
            self._active_job = job
            self._service_status = "busy"
            self._accepted_jobs += 1
            self._emit_event(
                "job_accepted",
                worker_job_id=job.worker_job_id,
                job_id=job.job_id,
                command=job.command,
            )
            self._condition.notify_all()
        return "accepted", job

    def status_payload(self) -> dict[str, object]:
        with self._condition:
            return {
                "schema_version": 2,
                "service": "wavegen-tool",
                "run_id": self.run_id,
                "status": self._service_status,
                "active_job": _job_payload(self._active_job),
                "last_job": _job_payload(self._last_job),
                "fatal_error": self._fatal_error,
                "command_url": self.command_url,
                "status_url": self.status_url,
                "stop_url": self.stop_url,
                "timestamp_utc": _timestamp(),
            }

    def fail_startup(self, exc: BaseException) -> None:
        """Record a bind/startup failure before the HTTP server is available."""

        self._set_fatal(exc, emit_summary=False)
        self._emit_summary()

    def _serve(self) -> None:
        server = self._server
        if server is None:
            return
        self._server_started.set()
        try:
            server.serve_forever(poll_interval=0.05)
        except Exception as exc:  # pragma: no cover - server lifecycle failure
            self._set_fatal(exc)

    def _wait_until_serving(self) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            connection = HTTPConnection(
                "127.0.0.1", self.control_port, timeout=0.2
            )
            try:
                connection.request("GET", "/status")
                response = connection.getresponse()
                response.read()
                if response.status == 200:
                    return
            except OSError:
                pass
            finally:
                connection.close()
            time.sleep(0.01)
        raise RuntimeError("Worker HTTP server did not accept status requests.")

    def _run_commands(self) -> None:
        try:
            while True:
                with self._condition:
                    while self._active_job is None and not self._stop_requested:
                        self._condition.wait()
                    if self._active_job is None:
                        break
                    job = self._active_job
                    job.state = "running"
                    job.started_at = _timestamp()
                self._emit_event(
                    "job_started",
                    worker_job_id=job.worker_job_id,
                    job_id=job.job_id,
                    command=job.command,
                )
                try:
                    result = self._execute_command(job)
                except WavegenError as exc:
                    error = _wavegen_error_payload(exc)
                    self._finish_job(job, result=None, error=error)
                    self._emit_event(
                        "job_failed",
                        worker_job_id=job.worker_job_id,
                        job_id=job.job_id,
                        command=job.command,
                        error=error,
                    )
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                    error = {
                        "code": "internal_error",
                        "message": "unexpected internal failure.",
                    }
                    self._finish_job(job, result=None, error=error)
                    self._emit_event(
                        "job_failed",
                        worker_job_id=job.worker_job_id,
                        job_id=job.job_id,
                        command=job.command,
                        error=error,
                    )
                else:
                    serialized = _json_safe(result)
                    self._finish_job(job, result=serialized, error=None)
                    self._emit_event(
                        "job_finished",
                        worker_job_id=job.worker_job_id,
                        job_id=job.job_id,
                        command=job.command,
                        result=serialized,
                    )
        except Exception as exc:  # pragma: no cover - runner lifecycle failure
            self._set_fatal(exc)
        finally:
            self._runner_done.set()
            self._shutdown_server()

    def _finish_job(
        self,
        job: JobRecord,
        *,
        result: object,
        error: dict[str, object] | None,
    ) -> None:
        with self._condition:
            job.state = "failed" if error is not None else "succeeded"
            job.finished_at = _timestamp()
            job.result = result
            job.error = error
            self._last_job = job
            self._active_job = None
            if error is None:
                self._succeeded_jobs += 1
            else:
                self._failed_jobs += 1
            self._service_status = "stopping" if self._stop_requested else "ready"
            self._condition.notify_all()

    def _execute_command(self, job: JobRecord) -> object:
        arguments = job.arguments
        context_mode = str(job.context["mode"])
        if context_mode == "dry_run":
            return self._execute_dry_run(job.command, arguments, job.context)

        resource = (
            SIMULATED_33521B_RESOURCE
            if self.config.mode == "simulate"
            else self.config.resource
        )
        factory_kwargs = self._factory_kwargs()
        backend = self.config.backend
        if job.command == "identify":
            return identify_instrument(resource, backend, **factory_kwargs)
        if job.command == "status":
            return query_status(resource, backend, **factory_kwargs)
        if job.command == "read-errors":
            return read_error_queue(
                resource,
                backend,
                max_reads=arguments["max_reads"],
                **factory_kwargs,
            )
        if job.command == "configure-sine":
            return configure_sine(
                resource,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                **factory_kwargs,
            )
        if job.command == "configure_sine_sweep":
            return configure_sine_sweep(
                resource,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                **factory_kwargs,
            )
        if job.command == "configure_square_sweep":
            return configure_square_sweep(
                resource,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                duty_cycle_percent=arguments["duty_cycle_percent"],
                **factory_kwargs,
            )
        if job.command == "configure_ramp_sweep":
            return configure_ramp_sweep(
                resource,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                symmetry_percent=arguments["symmetry_percent"],
                **factory_kwargs,
            )
        if job.command == "configure_triangle_sweep":
            return configure_triangle_sweep(
                resource,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                **factory_kwargs,
            )
        if job.command == "configure-square":
            return configure_square(
                resource,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["duty_cycle_percent"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                **factory_kwargs,
            )
        if job.command == "configure-ramp":
            return configure_ramp(
                resource,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["symmetry_percent"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                **factory_kwargs,
            )
        if job.command == "configure-triangle":
            return configure_triangle(
                resource,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                backend,
                arguments["phase_deg"],
                **factory_kwargs,
            )
        if job.command == "configure-pulse":
            return configure_pulse(
                resource,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["pulse_width_s"],
                arguments["offset_v"],
                arguments.get("edge_time_s"),
                arguments["load"],
                backend,
                arguments["phase_deg"],
                arguments.get("leading_edge_s"),
                arguments.get("trailing_edge_s"),
                **factory_kwargs,
            )
        if job.command == "configure-dc":
            return configure_dc(
                resource,
                arguments["voltage_v"],
                arguments["load"],
                backend,
                **factory_kwargs,
            )
        if job.command == "configure-noise":
            return configure_noise(
                resource,
                arguments["amplitude_vpp"],
                arguments["bandwidth_hz"],
                arguments["offset_v"],
                arguments["load"],
                backend,
                **factory_kwargs,
            )
        if job.command == "configure-prbs":
            return configure_prbs(
                resource,
                arguments["bit_rate_bps"],
                arguments["amplitude_vpp"],
                arguments["pattern"],
                arguments["offset_v"],
                arguments["edge_time_s"],
                arguments["load"],
                backend,
                **factory_kwargs,
            )
        if job.command == "output":
            return set_output(
                resource,
                "on" if arguments["enabled"] else "off",
                backend,
                **factory_kwargs,
            )
        raise RuntimeError(f"Unsupported admitted command: {job.command}.")

    def _execute_dry_run(
        self,
        command: str,
        arguments: dict[str, object],
        context: dict[str, object],
    ) -> object:
        model = context["planning_model_id"]
        if command == "configure-sine":
            return dry_run_sine(
                model,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                arguments["phase_deg"],
            )
        if command == "configure_sine_sweep":
            return dry_run_sine_sweep(
                model,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
            )
        if command == "configure_square_sweep":
            return dry_run_square_sweep(
                model,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
                duty_cycle_percent=arguments["duty_cycle_percent"],
            )
        if command == "configure_ramp_sweep":
            return dry_run_ramp_sweep(
                model,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
                symmetry_percent=arguments["symmetry_percent"],
            )
        if command == "configure_triangle_sweep":
            return dry_run_triangle_sweep(
                model,
                arguments["start_frequency_hz"],
                arguments["stop_frequency_hz"],
                arguments["spacing"],
                arguments["sweep_time_s"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["hold_time_s"],
                arguments["return_time_s"],
                arguments["load"],
                arguments["phase_deg"],
            )
        if command == "configure-square":
            return dry_run_square(
                model,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["duty_cycle_percent"],
                arguments["load"],
                arguments["phase_deg"],
            )
        if command == "configure-ramp":
            return dry_run_ramp(
                model,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["symmetry_percent"],
                arguments["load"],
                arguments["phase_deg"],
            )
        if command == "configure-triangle":
            return dry_run_triangle(
                model,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                arguments["phase_deg"],
            )
        if command == "configure-pulse":
            return dry_run_pulse(
                model,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["pulse_width_s"],
                arguments["offset_v"],
                arguments.get("edge_time_s"),
                arguments["load"],
                arguments["phase_deg"],
                arguments.get("leading_edge_s"),
                arguments.get("trailing_edge_s"),
            )
        if command == "configure-dc":
            return dry_run_dc(model, arguments["voltage_v"], arguments["load"])
        if command == "configure-noise":
            return dry_run_noise(
                model,
                arguments["amplitude_vpp"],
                arguments["bandwidth_hz"],
                arguments["offset_v"],
                arguments["load"],
            )
        if command == "configure-prbs":
            return dry_run_prbs(
                model,
                arguments["bit_rate_bps"],
                arguments["amplitude_vpp"],
                arguments["pattern"],
                arguments["offset_v"],
                arguments["edge_time_s"],
                arguments["load"],
            )
        raise RuntimeError(f"Unsupported admitted dry-run command: {command}.")

    def _factory_kwargs(self) -> dict[str, object]:
        if self.config.mode != "simulate":
            return {}
        state = self._simulator_state

        def factory(_pyvisa_library: str) -> SimulatedResourceManager:
            if state is None:  # pragma: no cover - configuration invariant
                raise RuntimeError("Simulator state is not available.")
            return SimulatedResourceManager(state)

        return {"resource_manager_factory": factory}

    def _shutdown_server(self) -> None:
        server = self._server
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:  # pragma: no cover - server lifecycle failure
                self._set_fatal(exc)

    def _set_fatal(self, exc: BaseException, *, emit_summary: bool = False) -> None:
        traceback.print_exception(exc, file=sys.stderr)
        with self._condition:
            self._fatal_error = {
                "code": "internal_error",
                "message": "unexpected internal failure.",
            }
            self._service_status = "error"
            self._stop_requested = True
            self._exit_code = 3
            self._condition.notify_all()
        self._emit_event("error", error=self._fatal_error)
        if emit_summary:
            self._emit_summary()

    def _emit_summary(self) -> None:
        with self._condition:
            if self._summary_emitted:
                return
            self._summary_emitted = True
            exit_code = 3 if self._fatal_error is not None else self._exit_code
            self._exit_code = exit_code
            payload = {
                "ok": self._fatal_error is None,
                "exit_code": exit_code,
                "accepted_jobs": self._accepted_jobs,
                "succeeded_jobs": self._succeeded_jobs,
                "failed_jobs": self._failed_jobs,
            }
        self._emit_event("summary", **payload)

    def _emit_event(self, event: str, **fields: object) -> None:
        payload: dict[str, object] = {
            "schema_version": 2,
            "event": event,
            "service": "wavegen-tool",
            "run_id": self.run_id,
            "timestamp_utc": _timestamp(),
        }
        payload.update(fields)
        with self._event_lock:
            print(
                json.dumps(_json_safe(payload), separators=(",", ":")),
                flush=True,
            )


class _WorkerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], runtime: WorkerRuntime) -> None:
        self.runtime = runtime
        super().__init__(server_address, _WorkerRequestHandler)


class _WorkerRequestHandler(BaseHTTPRequestHandler):
    server: _WorkerHTTPServer

    def do_GET(self) -> None:
        if self.path != "/status":
            self._send_json(404, {"schema_version": 2, "status": "error", "error": "not_found"})
            return
        self._send_json(200, self.server.runtime.status_payload())

    def do_POST(self) -> None:
        if self.path == "/command":
            self._handle_command()
        elif self.path == "/stop":
            self._handle_stop()
        else:
            self._send_json(404, {"schema_version": 2, "status": "error", "error": "not_found"})

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        del explain
        self._send_json(
            code,
            {
                "schema_version": 2,
                "status": "error",
                "error": "http_error",
                "message": message or "HTTP request failed.",
            },
        )

    def _handle_command(self) -> None:
        payload, parse_error = self._read_json()
        if parse_error is not None:
            self._send_json(
                400,
                _request_error(
                    None,
                    None,
                    "invalid_request",
                    parse_error,
                    run_id=self.server.runtime.run_id,
                ),
            )
            return
        command = payload.get("command") if isinstance(payload, Mapping) else None
        job_id = payload.get("job_id") if isinstance(payload, Mapping) else None
        if not isinstance(command, str):
            command = None
        if not isinstance(job_id, str):
            job_id = None
        try:
            validated = validate_worker_command_request(
                payload,
                worker_mode=self.server.runtime.config.mode,
                allow_output_writes=self.server.runtime.config.allow_output_writes,
            )
        except WorkerRequestValidationError as exc:
            self._send_json(
                400,
                _request_error(
                    command,
                    job_id,
                    exc.code,
                    str(exc),
                    run_id=self.server.runtime.run_id,
                ),
            )
            return

        reason, job = self.server.runtime.admit(validated)
        if reason != "accepted" or job is None:
            self._send_json(
                409,
                {
                    "schema_version": 2,
                    "status": "rejected",
                    "command": validated.command,
                    "job_id": validated.job_id,
                    "reason": reason,
                    "run_id": self.server.runtime.run_id,
                },
            )
            return
        self._send_json(
            202,
            {
                "schema_version": 2,
                "status": "accepted",
                "command": job.command,
                "job_id": job.job_id,
                "worker_job_id": job.worker_job_id,
                "run_id": self.server.runtime.run_id,
            },
        )

    def _handle_stop(self) -> None:
        body, parse_error = self._read_json(allow_empty=True)
        if parse_error is not None:
            self._send_json(400, _request_error(None, None, "invalid_request", parse_error))
            return
        if body not in (None, {}):
            self._send_json(
                400,
                _request_error(
                    None,
                    None,
                    "invalid_request",
                    "POST /stop accepts an empty body or an empty object.",
                ),
            )
            return
        self.server.runtime.request_stop()
        self._send_json(
            202,
            {
                "schema_version": 2,
                "status": "accepted",
                "run_id": self.server.runtime.run_id,
            },
        )

    def _read_json(self, *, allow_empty: bool = False) -> tuple[object, str | None]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, "Content-Length must be an integer."
        raw = self.rfile.read(length)
        if not raw.strip() and allow_empty:
            return None, None
        if not raw.strip():
            return None, "Request body must be a JSON value."
        try:
            return json.loads(raw.decode("utf-8")), None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "Request body must contain valid UTF-8 JSON."

    def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_worker(config: WorkerConfig) -> int:
    """Run one Worker until a cooperative stop or fatal runtime failure."""

    runtime = WorkerRuntime(config)
    try:
        runtime.start()
    except Exception as exc:
        print(f"Worker startup failed: {exc}", file=sys.stderr)
        runtime.fail_startup(exc)
        return 3
    try:
        runtime.wait()
    except KeyboardInterrupt:
        runtime.request_stop()
        runtime.wait()
    return runtime.exit_code


def _request_error(
    command: str | None,
    job_id: str | None,
    code: str,
    message: str,
    *,
    run_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 2,
        "status": "error",
        "command": command,
        "job_id": job_id,
        "error": code,
        "message": message,
    }
    if run_id is not None:
        payload["run_id"] = run_id
    return payload


def _job_payload(job: JobRecord | None) -> dict[str, object] | None:
    if job is None:
        return None
    return {
        "worker_job_id": job.worker_job_id,
        "job_id": job.job_id,
        "command": job.command,
        "context": dict(job.context),
        "state": job.state,
        "accepted_at": job.accepted_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "result": job.result,
        "error": job.error,
    }


def _wavegen_error_payload(error: WavegenError) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": error.code,
        "message": str(error),
    }
    for name in ("backend", "transport", "output_state"):
        value = getattr(error, name, None)
        if value is not None:
            payload[name] = value
    identity = getattr(error, "identity", None)
    if identity is not None:
        payload["identity"] = _json_safe(identity)
    cleanup_errors = getattr(error, "cleanup_errors", ())
    if cleanup_errors:
        payload["cleanup_errors"] = list(cleanup_errors)
    return payload


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
