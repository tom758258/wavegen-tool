# Wavegen Orchestrator Workflows

Schema version: `2`

Compatibility policy: `v2-only`

This document extends the
[Common Orchestrator Workflows](common-orchestrator-workflows.md) with
Wavegen-specific model context, command admission, and memory-first result
handling.

## Lifecycle

1. Start `wavegen-tool worker` in `live` or `simulate` mode.
2. Read JSONL until `ready`, then retain its `run_id`, `control_port`, and
   loopback URLs.
3. Use `wait-ready` or `worker-status` when readiness must be confirmed through
   HTTP.
4. Submit one command with `send-command` or `POST /command`.
5. Treat HTTP 202 as queue admission only. Match `worker_job_id` in a terminal
   event or `GET /status` `last_job` before using the result.
6. Submit explicit output-off when the workflow requires it, then request
   cooperative `worker-stop`.
7. Read the final `summary` and require a normal process exit.

The Worker has one active job slot. Orchestrators must handle `busy` and
`stopping` rejections without assuming a command ran.

## Request Context

Live requests use:

```json
{
  "mode": "live",
  "expected_model_id": "keysight-33512b"
}
```

`expected_model_id` is optional and is only a mismatch guard. The detected
`*IDN?` identity and Core Product policy are authoritative. Omitting the guard
does not default to 33521B. Model-dependent live validation occurs during the
background Core operation after identity resolution.

Simulator and dry-run requests use an exact registered planning model:

```json
{
  "mode": "simulate",
  "planning_model_id": "keysight-33512b"
}
```

```json
{
  "mode": "dry_run",
  "planning_model_id": "keysight-33510b"
}
```

Dry-run performs no VISA I/O and does not bind simulator state. A simulate
Worker binds to the first admitted simulate job's planning model and keeps that
model for its lifetime. A later simulate request for another model is rejected;
start a new Worker to simulate a different instrument.

## Command Submission

This representative request configures Channel 2 on a simulated two-channel
model while leaving output off:

```json
{
  "schema_version": 2,
  "command": "configure-sine",
  "arguments": {
    "channel": 2,
    "frequency_hz": 1000,
    "amplitude_vpp": 0.1
  },
  "job_id": "wavegen-job-1",
  "context": {
    "mode": "simulate",
    "planning_model_id": "keysight-33512b"
  }
}
```

Omitted `channel` defaults to 1 for channel-scoped Worker commands. Core owns
model-specific channel validation. `identify` and `read-errors` do not accept a
channel argument.

The Worker command surface is intentionally narrower than the Direct CLI.
List Sweep, modulation, burst, `configure-output`, `trigger`, resource listing,
and arbitrary SCPI are not Worker commands.

## Subprocess Example

```powershell
$worker = Start-Process `
  -FilePath ".\.venv\Scripts\wavegen-tool.exe" `
  -ArgumentList "worker", "--mode", "simulate", "--control-port", "8765" `
  -WindowStyle Hidden `
  -PassThru

uv run wavegen-tool wait-ready --port 8765 --json

uv run wavegen-tool send-command `
  --port 8765 `
  --command status `
  --arguments-json '{"channel":2}' `
  --context-json '{"mode":"simulate","planning_model_id":"keysight-33512b"}' `
  --job-id wavegen-status-1 `
  --json

uv run wavegen-tool worker-status --port 8765 --json
uv run wavegen-tool worker-stop --port 8765 --json
$worker.WaitForExit()
```

Production orchestrators must continuously drain Worker stdout, parse each
line as JSON, correlate `run_id` and `worker_job_id`, and bound all waits. The
short example omits that stream-processing code.

## Results and Shutdown

Wavegen Worker results are retained in memory and emitted in terminal events;
there is no per-job artifact contract. A successful accepted response is not a
successful job. Require `job_finished`, or a matching succeeded `last_job`,
before consuming result fields.

`GET /status` is lifecycle-only and performs no instrument I/O. To read
instrument state, submit the Worker `status` command. `POST /stop` does not
reconnect to hardware or turn output off. Safe shutdown workflows explicitly
submit output-off before stopping the Worker.
