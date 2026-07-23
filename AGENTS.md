# Agent Instructions

These are durable repository rules for coding agents working on Wavegen Tool.
Do not use this file for project plans, task prompts, progress logs, or handoff notes.

## 1. Architecture

- Keep one Python distribution: `wavegen-tool`.
- Preserve the import packages:
  - `wavegen_tool_core`
  - `wavegen_tool_cli`
  - `wavegen_tool_webui`
- Core must not import CLI or WebUI.
- CLI and WebUI may depend on Core but must not depend on each other.
- Core owns instrument identity, model capabilities, installed options,
  validation, support policy, SCPI, transport behavior, output safety,
  simulator behavior, and dry-run behavior.
- CLI, WebUI, and any future Electron frontend must not duplicate SCPI,
  model limits, identity rules, option rules, or live-support decisions.
- Keep shared architecture vendor-neutral and vendor/model behavior explicit.
- Do not add a second instrument-control implementation for Electron or WebUI.

## 2. Supported Scope

- Initial physical model support is Keysight 33521B.
- Do not infer support for another model from family similarity.
- Initial live transports are USB and LAN.
- GPIB remains unsupported until explicitly implemented, tested, and approved.
- Treat system VISA and `@py` (`pyvisa-py`) as separate backend scopes.
- Resolve live identity from both manufacturer and model.
- Unknown, mismatched, unsupported, or unvalidated live scopes must fail closed.
- Do not use a generic SCPI fallback for model-aware live control merely because
  another model appears command-compatible.

## 3. Instrument Safety

- Treat changes affecting live hardware as high risk.
- Default output state is off.
- Connection, identification, diagnostics, queries, and waveform configuration
  must not implicitly enable output.
- Output-on must always be explicit.
- Do not send `*RST` automatically during normal connection, diagnostics,
  configuration, or tests.
- Validate waveform, frequency, amplitude, offset, termination, pulse,
  arbitrary-memory, sample-rate, and option-dependent limits in Core before
  live writes.
- Get user confirmation before changing SCPI behavior, VISA timeouts,
  trigger/wait behavior, binary transfer, output behavior, stop/cleanup
  behavior, or model safety limits.
- Keep VISA resources configurable. Never commit real resource strings,
  serial numbers, private IDN data, lab addresses, private IP addresses, or
  personal filesystem paths.

## 4. Runtime and Testing

- Real, Dry-run, and Simulator modes must remain isolated.
- Dry-run and Simulator modes must not open real VISA resources.
- Default tests must run without hardware.
- Prefer fake-session, simulator, and dry-run tests before live validation.
- Real-instrument validation must be explicit, opt-in, bounded, and use a
  resource supplied by the user.
- Never infer, scan for, or guess a live VISA resource.
- Hardware validation must use conservative settings, verify output off before
  and after state-changing tests, inspect the final error queue, and report
  cleanup results.
- Report failed, skipped, blocked, partial, and unexecuted verification steps.

## 5. Packaging and Dependencies

- The root `pyproject.toml` is the single packaging and dependency boundary.
- Use a committed `uv.lock` for reproducible environments.
- Do not edit `uv.lock` manually.
- Use `uv sync --all-extras --locked --link-mode=copy` for normal reproducible
  setup once the project metadata exists.
- Update `pyproject.toml` and `uv.lock` together when dependencies change.
- Get user confirmation before changing the distribution name, supported Python
  versions, dependency groups, entry points, build system, or package ownership.

## 6. Documentation and Text Hygiene

- Keep tracked documentation durable, public, and free of temporary planning,
  transient progress, private hardware context, and raw validation records.
- Keep user guides operator-facing and engineering details in focused
  maintainer or contract documentation.
- English Markdown is the default source documentation. Update localized
  documentation only when explicitly requested.
- Use UTF-8 without BOM.
- Inspect diffs for accidental encoding, line-ending, or generated-file churn.
- Update tests and documentation when a public contract or safety boundary
  changes.
