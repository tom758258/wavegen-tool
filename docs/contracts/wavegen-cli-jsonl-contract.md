# Wavegen CLI JSON / JSONL Contract

Schema version: `2`

Compatibility policy: `v2-only`

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

Worker stdout is JSONL. Every non-empty stdout line is one schema-version-2
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
- `identify` returns backend, transport, reported identity fields,
  `canonical_model_id`, and `model_supported`.
- `status` returns selected `channel`, identity context, output state,
  function, frequency or bit rate, amplitude, bandwidth, offset, and load.
- `read-errors` returns bounded error entries, read counts, empty/limit flags,
  and identity/backend context.
- waveform, sweep, List Sweep, output-configuration, and output-control
  results identify `action`, selected `channel`, normalized request values,
  output state, and relevant waveform-specific fields.
- dry-run plans contain `mode: "dry-run"`, the planning model and
  `canonical_model_id`, ordered `commands`, `executed: false`, selected
  `channel`, and the normalized request fields.
- simulator execution uses the normal executed result shape and reports the
  observed simulated identity. Simulator identity is not physical evidence.

Waveform-specific additions include modulation, Sum, burst, duty-cycle,
symmetry, pulse-edge, PRBS, sweep timing/spacing/trigger, and List Sweep fields
only when that Direct CLI command exposes them. This contract does not freeze
every internal result dataclass field.

## Direct CLI Commands

The public Direct CLI machine surface includes:

- `manifest`, `capabilities`, and `list-resources`;
- `identify`, `status`, and `read-errors`;
- `configure-sine`, `configure-square`, `configure-ramp`,
  `configure-triangle`, `configure-pulse`, `configure-dc`,
  `configure-noise`, and `configure-prbs`;
- sine, square, ramp, and triangle ordinary sweep and List Sweep commands;
- `configure-output`, `output`, and `trigger`;
- `worker`, `send-command`, `worker-status`, `wait-ready`, and `worker-stop`.

Direct command availability does not imply that the Worker accepts the same
command. Worker commands are defined only by the
[Wavegen Worker Contract](wavegen-worker-contract.md).

## Worker JSONL Events

Worker events are `ready`, `job_accepted`, `job_started`, `job_finished`,
`job_failed`, `stop_requested`, `error`, and `summary`. Each event contains
exact integer `schema_version: 2`, `service: "wavegen-tool"`, `run_id`, and a
UTC timestamp.

Terminal successful job results contain the Core result serialized as JSON.
Selected-channel results retain Core's canonical integer `channel`. Failed
jobs contain a structured error with a stable `code` and `message`, plus
available backend, transport, identity, output-state, or cleanup context.

The Worker is memory-first: accepted responses and events do not promise a
per-job artifact path. Observe completion through terminal JSONL events or
`GET /status` `last_job`.

## Lifecycle Client JSON

`send-command` submits one schema-2 Worker request and reports HTTP admission.
`ok: true` means the Worker accepted the request; it does not mean background
execution succeeded. `worker-status` reads memory-only lifecycle state.
`wait-ready` polls until the Worker becomes ready or fails its bounded wait.
`worker-stop` requests cooperative stop without executing a domain command.

Lifecycle-client JSON merges the validated Worker response with diagnostics
including `client_command`, `method`, `url`, `endpoint`, `timeout_ms`,
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
