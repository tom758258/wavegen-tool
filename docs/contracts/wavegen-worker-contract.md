# Wavegen Worker Contract

Schema version: `2`

Compatibility policy: `v2-only`

This contract defines Wavegen-specific command admission and command semantics.
It is used with the [Common Worker Protocol](common-worker-protocol.md), the
[Common CLI JSON / JSONL Contract](common-cli-jsonl-contract.md), and the
[Common Orchestrator Workflows](common-orchestrator-workflows.md). Common
envelope and lifecycle rules are not repeated here.

Part 1 implements request admission only. It does not implement the HTTP
control plane, Worker runtime, lifecycle clients, queues, or artifacts.

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
