# Testing Guidelines

Wavegen Tool tests protect public APIs, instrument-safety boundaries, support
policy, CLI behavior, validation artifacts, and documented integration
contracts. Default tests and validation paths are hardware-free.

## What To Test

Test stable behavior and boundaries, including:

- public Core imports, request validation, identity resolution, capability
  lookup, selected-channel behavior, and fail-closed support decisions;
- CLI parsing, exit behavior, human output where contract-relevant, and
  machine-readable JSON or JSONL output;
- dry-run planning, including the selected model/channel, ordered commands,
  output state, invalid option groups, and safety decisions;
- deterministic simulator workflows and independent channel state;
- transport/backend admission and the absence of fallback;
- fake-VISA live paths for identity, writes, queries, verification, cleanup,
  and error handling;
- validation runner admission, planned/exercised case status, confirmation,
  artifacts, sanitization, and redaction;
- output-off defaults, explicit output-on, coupling/tracking guards, and the
  absence of implicit reset, preset, or recall;
- documentation structure that affects users, such as required files, links,
  support status, command names, and privacy boundaries.

Real hardware is appropriate only for explicitly approved validation of live
behavior. Documentation-only and hardware-independent changes normally require
only focused hardware-free checks.

## What Not To Freeze

Do not write tests that depend on:

- exact README prose, sentence wording, paragraph order, or Markdown layout;
- complete documentation snapshots, line counts, or incidental example order;
- internal helper or local-variable names that are not a public boundary;
- decorative terminal spacing or wording that is not an operator contract;
- incidental dictionary ordering outside a documented schema;
- simulator output as proof of physical-instrument behavior;
- model, connection, backend, channel, or feature scope that Core does not
  explicitly register and admit.

Prefer structural assertions: a link target exists, a public export remains
available, a schema field has the documented meaning, a command plan is
correct, a safety invariant holds, or an unsupported combination fails closed.

## Documentation Tests

Documentation checks should verify stable, user-relevant facts:

- required documentation files exist and important relative links resolve;
- the root README points to Core, CLI, supported-model, contributor, and
  testing entry points;
- model, feature, Worker, connection, and backend claims match current Core,
  CLI, contracts, and validation scripts;
- warnings distinguish dry-run, simulator, PlanOnly, and real hardware;
- live examples require an explicit resource and do not imply scanning or
  auto-selection;
- tracked text contains no real resource, serial, raw IDN, private address,
  private hostname, or personal filesystem path;
- evidence wording distinguishes planned cases from cases actually exercised.

Do not require exact natural-language paragraphs unless the exact text is
itself a legal, safety, privacy, or public contract requirement.

## Instrument Safety Tests

Tests that cover instrument-facing behavior should confirm that:

- identification and read-only diagnostics do not change waveform state or
  enable output;
- waveform and output configuration turn the selected output off and leave it
  off;
- output-on occurs only through an explicit operation;
- Channel 2 is rejected for one-channel profiles;
- two-channel state-changing operations fail closed when coupling or tracking
  is enabled, indeterminate, or cannot be queried;
- unsupported identity, transport, backend, parameter, and expected-model
  combinations fail before unsafe execution;
- the implementation does not use `*RST`, preset, or setup recall;
- cleanup and error-queue failures remain visible rather than being converted
  into passing results.

Use fake resources to verify exact write/query ordering and failure paths.
Do not use private lab equipment for default test execution.

## Hardware-Free and Live Boundaries

Dry-run validates a registered planning model and produces a command plan
without creating a VISA resource manager, opening a resource, sending SCPI, or
performing hardware I/O.

The simulator provides deterministic in-memory behavior. It does not prove
physical timing, analog performance, firmware behavior, transport behavior, or
real-instrument support.

PlanOnly is also hardware-free. It requires an explicit resource to describe
the intended operator scope, but it must not open VISA, perform hardware I/O,
or send live SCPI.

Real validation must be explicit, opt-in, bounded, interactive where the runner
requires confirmation, and use an operator-supplied resource. Normal control
and validation must never infer, guess, scan, or auto-select a resource.
Resource enumeration is allowed only when a user explicitly invokes the
dedicated discovery diagnostic; it is not unattended live admission.

Keep output off unless a separately approved test explicitly requires it. Do
not use `*RST`. Do not make tests depend on a private lab resource, and never
commit the real resource, serial, raw IDN, private address, hostname, or local
path.

## Test Output Locations

Run pytest from the repository root. Put intentional test and validation
artifacts under `.tmp_tests/`. Do not write generated evidence into tracked
documentation or source directories.

Validation artifacts follow the existing `private/` and `shareable/` policy in
[Contributing](CONTRIBUTING.md). Raw private evidence remains local.

## Review Standard

Start with the narrowest relevant checks, then run broader hardware-free tests
when the risk and change justify them. Documentation-only work does not require
a full suite without a specific reason.

Report every failed, skipped, blocked, and unexecuted check. Describe
unsupported or untested scope honestly. A failed hardware run remains useful
diagnostic evidence, but it is not passing Product Live evidence. A simulator,
dry-run, PlanOnly run, or successful representative live case must not be used
to claim validation of feature families that were not exercised.

