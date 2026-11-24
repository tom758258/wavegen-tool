# Wavegen CLI JSON / JSONL Contract

Worker schema version: `3`

Manifest, capabilities, and lifecycle-client schema version: `2`

Compatibility policy: `v3-only`

This document defines Wavegen-specific machine output. It extends the
[Common CLI JSON / JSONL Contract](common-cli-jsonl-contract.md) and separates
Direct CLI single-response JSON from Worker lifecycle JSONL.

## Machine-output Boundaries

Direct CLI commands with `--json` emit exactly one JSON object and no
human-readable stdout. Instrument-operation objects use the existing
command-specific `success` and `error` fields and do not add a top-level
`schema_version`. The offline `manifest` and `capabilities` commands instead
use their existing schema-version-2 event objects. These are current Direct
CLI contracts, not aliases for Worker events.

Worker stdout is JSONL. Every non-empty stdout line is one schema-version-3
event object. The lifecycle clients `send-command`, `worker-status`,
`wait-ready`, and `worker-stop` emit one schema-version-2 JSON object with
`--json`.

Consumers must not parse text-mode prose as a machine interface. Direct JSON,
Worker JSONL, HTTP responses, and lifecycle-client JSON have separate
envelopes and must not be interchanged.

## Direct CLI Results

Successful instrument-operation objects contain `success: true` and
`error: null`. Failures contain `success: false` and a stable error string.
Result families add fields appropriate to the command:

- `manifest` emits `event: "tool_manifest"`, exact integer
  `schema_version: 2`, tool identity, and Worker protocol compatibility.
- `capabilities` emits `event: "capabilities"`, exact integer
  `schema_version: 2`, the requested/registered model identity, and Core
  capabilities. Its structured invalid-model response uses `event: "error"`,
  `ok: false`, and exit code 2.
- `list-resources` returns backend and resource entries. Every entry retains
  `resource`, `manufacturer`, and `model`, with additive `model_id` and
  `model_support_status`. Raw listings set the new fields to `null` because
  they do not query IDN. Live listings use `product_live_model`,
  `registered_only`, `unsupported`, or `unidentified`; a
  `product_live_model` label does not admit the backend, transport, or
  command for live control. The list excludes serial, firmware, and raw IDN.
- `identify` returns backend, transport, reported identity fields,
  `canonical_model_id`, and `model_supported`.
- `readback-advanced` returns `advanced_mode`, a `settings` object with only active parameters, selected `channel`, and identity context. `CW` uses an empty settings object; standalone queries never claim `readback_verified`. The field `advanced_mode` is separate from `mode="simulate"` execution metadata.
- `status` returns selected `channel`, identity context, output state,
  function, frequency or bit rate, amplitude, bandwidth, offset, and load.
  For Arbitrary playback, it reports `sample_rate_hz` with `frequency_hz: null`.
- `read-errors` returns bounded error entries, read counts, empty/limit flags,
  and identity/backend context.
- waveform, sweep, List Sweep, output-configuration, and output-control
  results identify `action`, selected `channel`, normalized request values,
  output state, and relevant waveform-specific fields.
- `configure-sync` and `sync-output` report the connector's `sync_state`
  and ordered `commands`. Configuration also reports `source_channel`,
  `sync_mode`, and `polarity`. Successful execution includes
  `readback_verified: true`. A Sync configuration dry-run includes
  `executed: false`, the planning model, and `canonical_model_id`.
- Arbitrary results identify `action`, selected `channel`, and
  `status` (`"planned"` or `"completed"`). Depending on the operation,
  they report waveform name and sample count, playback and step metadata,
  or a bounded catalog and free points. With `configure-arbitrary --load-builtin`,
  an executed result includes `builtin_loaded`, which is `true` if a built-in
  waveform was loaded and `false` if it was reused. A dry-run shows the
  commands for loading an absent built-in.
- dry-run plans contain `mode: "dry-run"`, the planning model and
  `canonical_model_id`, ordered `commands`, `executed: false`, selected
  `channel`, and the normalized request fields.
- simulator execution uses the normal executed result shape and reports the
  observed simulated identity. Simulator identity is not physical evidence.

Waveform-specific additions include modulation, Sum, burst, duty-cycle,
symmetry, pulse-edge, PRBS, sweep timing/spacing/trigger, and List Sweep fields
only when that Direct CLI command exposes them. Successful ordinary Sweep
results and dry-run plans report `trigger_slope` as `"positive"` or
`"negative"` for External and `null` for other sources.
Counted Burst results and dry-run plans include `burst_trigger_delay_s`.
Ordinary Sweep results and dry-run plans include `trigger_delay_s`. Both
report the normalized requested delay in seconds, or `null` if omitted.
An explicit zero is a write and has the same access restrictions as any
other delay. Independent `readback-advanced` reports the observed
`settings.trigger_delay_s` for active Counted Burst or ordinary Sweep.
Internal result dataclasses may contain additional fields.

Successful Direct CLI configurations and dry-run plans with AM, FM, PM, PWM,
or Sum include the active mode's shape in `am_shape`, `fm_shape`, `pm_shape`,
`pwm_shape`, or `sum_shape`, respectively. Each value is one of `"sine"`,
`"square"`, `"up-ramp"`, `"triangle"`, or `"down-ramp"`. A standalone
`readback-advanced` result includes `settings.shape` when an internal
modulation function corresponds to one of those values and also returns the
existing `settings.internal_function`.

Successful Direct CLI Burst configurations and dry-run plans include
`burst_start_phase_deg` for both Counted and Gated Burst. It is the normalized
requested start phase in degrees and defaults to 0.0. It is independent of the
ordinary waveform `phase_deg`. An independent `readback-advanced` result with
`advanced_mode="BURST"` reports the observed value in `settings.phase_deg`
for both Burst modes.

For the existing Advanced Mode configuration commands, a successful executed
operation with `--verify-readback` adds `readback_verified: true` to Direct CLI
JSON. A failed comparison retains `success: false` and the existing waveform
verification error category (exit 29). Default executed results and hardware-free
dry-run plans never include that field. Normalized configuration results remain
requested values and must not be mistaken for independent observations.
Configuration verification queries Trigger Delay only when explicitly supplied.

Successful Direct CLI execution with `--verify-waveform-readback` adds
`waveform_readback_verified: true`. This verifies phase for Square, Ramp, and
Triangle configuration, pattern and edge time for PRBS, duty cycle for Square
Sweep, or symmetry for Ramp Sweep. A mismatch or failed query uses the existing
waveform verification error category (exit 29). Dry-run and default execution
omit this field. Sweep commands can opt into both types of verification.

### Software Sequence Results

`run-software-sequence --file <sequence.json>` returns one JSON object with the
usual Direct CLI `success`, `action`, and `error` fields. Results also report
`model`, `canonical_model_id`, `channels`, `step_count`, `loop_count`,
`execution_units`, `completed_loops`, `completed_step_executions`, and
`total_wait_seconds`.

A dry-run has `status: "planned"`, `executed: false`, and `cleanup: null`.
Its `steps` array contains one ordered plan per input step. Simulation and
live successes have `status: "completed"`, `executed: true`, and `results`
with details for the first 200 completed executions, identified by one-based
`loop` and `step`. Completed counts cover every execution. Error-queue steps
report numeric error codes and bounded-read status. Sequence results omit
raw instrument identity, local CSV paths, and waveform samples.

Executed runs report `cleanup.attempted_channels`, `confirmed_off_channels`,
and `errors`. Cleanup errors fail the command. Failure can include a
`sequence` object with completed counts, cleanup details, and a `failed_step`
with one-based `loop` and `step`, the `action`, and the Core `error`. For a
step rejected before execution, `loop` is 1 and the completed counts are zero.
`action` is null when the step has no well-formed command identifier.
Document-level validation errors have no step location. A controllable
interruption exits with code 130 after attempting output cleanup. Step failures
use the underlying Core error's normal exit-code mapping.

The six [Basic Workflow commands](wavegen-worker-contract.md#basic-workflows)
use the same result structure with their own `action` and a top-level
`channel`. Successful executions include the selected channel's safe-off
confirmation. A dry-run describes the expanded workflow and its waits.
Frequency Step reports a single `run-sine-list-sweep-once` step whose `seconds`
and the workflow's `total_wait_seconds` report the nominal list dwell in total.
The dry-run `commands` show Output ON before LIST and Immediate initiation.

## Direct CLI Commands

The public Direct CLI machine surface includes:

- `manifest`, `capabilities`, and `list-resources`;
- `identify`, `status`, `readback-advanced`, and `read-errors`;
- `configure-sine`, `configure-square`, `configure-ramp`,
  `configure-triangle`, `configure-pulse`, `configure-dc`,
  `configure-noise`, and `configure-prbs`;
- sine, square, ramp, and triangle ordinary sweep and List Sweep commands;
- `configure-output`, `output`, and `trigger`;
- `configure-sync` and `sync-output`;
- `upload-arbitrary`, `configure-arbitrary`, `configure-arbitrary-sequence`,
  `list-arbitrary-memory`, and `clear-arbitrary-memory`;
- `run-software-sequence`, `run-timed-output`, `run-amplitude-ramp`,
  `run-output-cycle`, `run-waveform-sequence`, `run-frequency-step`, and
  `apply-quick-output-preset`;
- `worker`, `send-command`, `worker-status`, `wait-ready`, and `worker-stop`.

Direct command availability does not imply that the Worker accepts the same
command. Worker commands are defined only by the
[Wavegen Worker Contract](wavegen-worker-contract.md).

## Worker JSONL Events

Worker events are `ready`, `job_accepted`, `job_started`, `job_finished`,
`stop_requested`, `error`, and `summary`. Each event contains
exact integer-token `schema_version: 3`, `service: "wavegen-tool"`, `run_id`,
and a UTC timestamp.

All Worker domain commands are async jobs. Every normal, controllable
termination emits `job_finished` with `state`, boolean `ok`, and required
`result` and `error`. The terminal timestamp is fixed and shared with the
HTTP `last_job` snapshot. Success uses `state="succeeded"`, `ok=true`, and
`error=null`; failure uses `state="failed"`, `ok=false`, and a Common Error.
Cancellation uses `state="cancelled"` and `ok=false`. The Common Error
preserves the cancellation details and sequence cleanup. A cleanup failure
produces a failed terminal. Manifest and ready declare `common.job_cancel`
contract version 1 with the supported commands, and ready supplies `cancel_url`.

Terminal successful job results contain the Core result serialized as JSON.
AM, FM, PM, PWM, and Sum configuration results and plans report the selected
`shape` in their respective nested modulation object. Standalone Advanced
Mode readback returns `settings.shape` under the same conditions as Direct CLI
readback.

Selected-channel results retain Core's canonical integer `channel`. Failed
jobs contain a Common Error; original backend, transport, identity,
output-state, or cleanup diagnostics remain in `error.details.domain_error`.
Fatal Worker errors use `scope="worker"`, `fatal=true`, and category `worker`.
Summary includes `ok`, `fatal_error`, intended `exit_code`, and job counters.
A job failure does not itself fail the multi-job Worker summary. Consumers
continue through EOF and actual process exit, retaining any later cleanup
failure evidence even after a successful summary.

The Worker is memory-first: accepted responses and events do not promise a
per-job artifact path. Observe completion through terminal JSONL events or
`GET /status` `last_job`.

## Lifecycle Client JSON

`send-command` submits one schema-3 Worker request and reports HTTP admission.
`ok: true` means the Worker accepted the request; it does not mean background
execution succeeded. `worker-status` reads memory-only lifecycle state.
`wait-ready` polls until the Wavegen command slot is idle or fails its
bounded wait.
A busy Worker can remain Common control-plane ready; this CLI keeps waiting.
`worker-stop` requests cooperative stop without executing a domain command.

Lifecycle-client JSON retains the raw Worker v3 object under
`worker_response`, with existing convenience fields at the outer schema-2
level. Client `ok`, string `error`, `message`, `exit_code`, and diagnostics
retain their independent meanings. Worker fields cannot overwrite them.
The diagnostics include
`client_command`, `method`, `url`, `endpoint`, `timeout_ms`,
`elapsed_ms`, `request_sent`, `reachable`, `http_status`, and `error_phase`.
Failures also include `ok: false` and `exit_code`.

## Exit Codes

- `0`: success, accepted request, or successful hardware-free plan.
- `2`: CLI usage, request validation, or Worker HTTP 400 failure.
- `3`: lifecycle connection/timeout/invalid-response failure or Worker runtime
  failure.
- Direct instrument commands additionally use their documented domain exit
  codes; see the [CLI guide](../cli/README.md#json-and-errors).

Argument-parser failures may write usage to stderr before structured output is
available. Machine callers must treat any non-zero process exit as failure even
when a JSON object was emitted.
