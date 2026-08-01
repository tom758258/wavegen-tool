# Wavegen Worker Contract

Schema version: `2`

Compatibility policy: `v2-only`

This contract defines Wavegen-specific command admission and command semantics.
It is used with the [Common Worker Protocol](common-worker-protocol.md), the
[Common CLI JSON / JSONL Contract](common-cli-jsonl-contract.md), and the
[Common Orchestrator Workflows](common-orchestrator-workflows.md). Common
envelope and lifecycle rules are not repeated here.

The v1 Worker includes a local HTTP control plane and one background command
runner. Lifecycle clients and per-job artifacts are not part of v1.

## Command Envelope

`POST /command` requests are JSON objects with exactly these allowed top-level
fields:

- `schema_version`: required exact integer `2`.
- `command`: required non-empty string.
- `arguments`: optional object; omitted means `{}`.
- `job_id`: optional string.
- `context`: required object.

Unknown top-level fields are rejected. Context is supplied per request.

## Startup and Request Modes

Worker startup mode is either `live` or `simulate`.

| Worker startup mode | Accepted request modes |
| --- | --- |
| `live` | `live`, `dry_run` |
| `simulate` | `simulate`, `dry_run` |

Other combinations are rejected.

The only supported model ID is `keysight-33521b`.

| Request context mode | Required fields | Forbidden fields |
| --- | --- | --- |
| `live` | `mode`; optional `expected_model_id` | `planning_model_id` |
| `simulate` | `mode`, `planning_model_id` | `expected_model_id` |
| `dry_run` | `mode`, `planning_model_id` | `expected_model_id` |

For `live`, `expected_model_id`, when present, must be
`keysight-33521b`. For `simulate` and `dry_run`, `planning_model_id` must be
`keysight-33521b`. Unknown context fields are rejected.

## Supported Commands

Supported commands are:

- `identify`
- `status`
- `read-errors`
- `configure-sine`
- `configure-square`
- `configure-ramp`
- `configure-pulse`
- `configure-dc`
- `configure-noise`
- `configure-prbs`
- `output`

`list-resources`, arbitrary SCPI, and waveform, sweep, burst, or sequence
commands not listed above are unsupported.

In `dry_run` context, only the seven `configure-*` commands are supported.
`identify`, `status`, `read-errors`, and `output` do not receive invented
dry-run behavior in v1.

## Command Arguments

Arguments use `snake_case`. Unknown argument fields and missing required fields
are rejected.

`identify` and `status` do not allow argument fields.

`read-errors` accepts optional `max_reads`, default `20`. It must be a
non-boolean integer from `1` through `100`. Execution drains the instrument
error queue.

Waveform arguments and defaults are:

| Command | Required arguments | Optional defaults |
| --- | --- | --- |
| `configure-sine` | `frequency_hz`, `amplitude_vpp` | `offset_v=0`, `load="50"` |
| `configure-square` | `frequency_hz`, `amplitude_vpp` | `offset_v=0`, `duty_cycle_percent=50`, `load="50"` |
| `configure-ramp` | `frequency_hz`, `amplitude_vpp` | `offset_v=0`, `symmetry_percent=100`, `load="50"` |
| `configure-pulse` | `frequency_hz`, `amplitude_vpp`, `pulse_width_s` | `offset_v=0`, `edge_time_s=1e-8`, `load="50"` |
| `configure-dc` | `voltage_v` | `load="50"` |
| `configure-noise` | `amplitude_vpp`, `bandwidth_hz` | `offset_v=0`, `load="50"` |
| `configure-prbs` | `bit_rate_bps`, `amplitude_vpp` | `pattern="PN7"`, `offset_v=0`, `edge_time_s=8.4e-9`, `load="50"` |

Waveform numeric ranges, load values, patterns, and SCPI planning rules are
owned by the existing Core `dry_run_*` functions. The Worker does not duplicate
those rules.

`output` requires `enabled`, which must be a boolean. Optional
`confirm_output`, when present, must also be a boolean. `confirm_output` is an
admission safety field and is not passed to Core.

## Live Write Safety

The following safety gate applies only when `context.mode` is `live`:

- `identify`, `status`, and `read-errors` do not require
  `allow_output_writes`.
- Every `configure-*` command requires `allow_output_writes=True`.
- `output` with `enabled=false` is always allowed.
- `output` with `enabled=true` requires both `allow_output_writes=True` and
  `confirm_output=true`.

Simulation and dry-run admission does not require live write authorization.
Output off is an explicit safe-domain command. `POST /stop` is a lifecycle
stop; it does not reconnect or silently execute output off. A normal live
shutdown workflow should explicitly execute output off before stopping the
Worker.

## Lifecycle Boundaries

`GET /status` is lifecycle status only. It does not execute a Wavegen Core
command or perform VISA I/O. Instrument status must be requested with
`command="status"`.

`POST /stop` follows the Common lifecycle contract and does not change the
command admission rules above. v1 does not create per-job artifacts.

## Worker Startup

The standalone entry point is `wavegen-tool worker`. Its options are:

- `--mode live|simulate` (required).
- `--resource`, required for `live` and forbidden for `simulate`.
- `--backend`, defaulting to `system`; live values use Core backend,
  transport, and connection-scope validation, while simulation permits only
  `system`.
- `--control-port`, an integer from `0` through `65535`, defaulting to `0`.
- `--allow-output-writes`, disabled by default.

The control plane binds only to `127.0.0.1`. Startup validation does not create
a ResourceManager, open a session, or probe a live instrument. A `ready` event
means that the HTTP server is serving lifecycle requests; it does not mean
that a physical instrument has been connected or identified. Startup usage
errors exit with code `2`; an unrecoverable bind or runtime failure exits with
code `3`.

## Runtime and Execution

The Worker has one active job slot. An accepted job is `queued` or `running`;
while either state is active, another command is rejected with HTTP `409` and
`reason="busy"`. New commands are rejected with `reason="stopping"` after a
stop request.

Simulation creates one `Simulated33521BState` for the Worker lifetime. Each
simulated command creates a new `SimulatedResourceManager` over that shared
state and executes through the existing Core command functions. State is not
reset when a manager or session closes. A live Worker retains only its
resource and backend; each command uses the existing Core per-command VISA
open, identification, execution, verification, and cleanup lifecycle.

All admitted dry-run commands use the existing Core `dry_run_*` functions and
do not create a live or simulated session. Results are serialized as stable
JSON objects and are retained in the terminal job event and `last_job`.

## HTTP Control Plane

`GET /status` is memory-only lifecycle status. It does not execute a Core
command, perform VISA I/O, or access simulator state. Instrument status must
be requested with `POST /command` and `command="status"`.

`POST /command` performs P1 admission and returns HTTP `202` only after the
single active slot has been reserved. The background runner performs the
domain command. Admission errors return HTTP `400`; busy and stopping
requests return HTTP `409`.

`POST /stop` accepts an empty body or `{}` and is idempotent while the server
is reachable. It requests cooperative shutdown, allows an accepted job to
finish, then stops the runner and HTTP server. It does not reconnect, query
the instrument, execute `set_output`, or modify simulator output state.
Output off remains an explicit command and should be submitted before a
normal live shutdown.

## JSONL Events

Worker stdout contains only compact JSONL event objects. Events are
`ready`, `job_accepted`, `job_started`, `job_finished`, `job_failed`,
`stop_requested`, `error`, and `summary`. Every event uses schema version `2`,
service `wavegen-tool`, the Worker `run_id`, and an UTC timestamp. The `ready`
event includes the actual control port and loopback URLs. `summary` includes
the exit code and accepted, succeeded, and failed counters. Human diagnostics
are written to stderr. Lifecycle client implementation remains deferred to
Part 3.
