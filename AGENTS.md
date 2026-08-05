# Agent Instructions

These instructions define long-term, repository-specific boundaries for agents
working on Wavegen Tool. Global agent rules already cover communication,
planning, simple and surgical changes, and text-file hygiene.

## 1. Project Context

- Read the affected code and the relevant documentation before changing
  behavior. Use the root `README.md` and `pyproject.toml` when the task concerns
  installation, packaging, entry points, dependencies, or repository layout.
- Read the relevant files in `docs/contracts/` before changing CLI/WebUI
  adapter behavior, worker or subprocess orchestration, JSON/JSONL schemas, or
  HTTP control/status contracts.
- Preserve machine-mode stdout as structured JSON or JSONL only. Human-readable
  diagnostics belong in text mode or stderr; do not emit plain-text lifecycle
  output on machine-mode stdout.
- Get user confirmation before changing contract-defined queue admission or
  rejection semantics, worker lifecycle or HTTP control behavior, process exit
  meanings, run correlation, or artifact path, privacy, redaction, publication,
  or ownership rules.
- Read `docs/webui/web-ui-change-rules.md` before changing WebUI static files or
  in-app UI behavior.

## 2. Distribution And Import Boundaries

- The root `pyproject.toml` is the single distribution metadata boundary for
  `wavegen-tool`. Do not recreate component-local distributions or introduce a
  `wavegen_tool.*` namespace without explicit user approval.
- Get user confirmation before changing public packaging boundaries: package
  name or version, dependencies or optional dependency groups, console scripts
  or entry points, build system, or Core/CLI/WebUI component ownership. Tool
  configuration such as pytest, ruff, or mypy may be changed when the requested
  task clearly includes it.
- Preserve the import packages `wavegen_tool_core`, `wavegen_tool_cli`, and
  `wavegen_tool_webui`.
- Core must not import CLI or WebUI. CLI and WebUI may depend on Core, but must
  not depend on each other.

## 3. Multi-Vendor Extension Boundary

- Keep the product identity and shared architecture vendor-neutral.
  Implementation details and user documentation may remain vendor- or
  model-specific where accurate. Validation evidence may also remain vendor-
  or model-specific, but belongs in private or separately shared review
  artifacts, not tracked public documentation.
- Keep model identification, capabilities, drivers, validation, and
  instrument-command differences in Core. Live identity comes from detected
  `*IDN?`; no model override is allowed. Unknown, unregistered, unsupported, or
  mismatched vendors, models, profiles, drivers, or live scopes fail closed.
  Dry-run and simulator paths may use an explicitly selected registered
  profile.
- CLI, Worker, and WebUI must use Core identity, capability, driver, safety, and
  instrument-command behavior. They must not copy model-specific branches or
  create parallel SCPI, parsing, hardware, or safety implementations.
- These rules constrain future changes. Do not pre-build abstractions for an
  unsupported second vendor or refactor reasonable current model-specific code
  without a concrete requirement.

## 4. Instrument Safety

- Treat changes that can affect live waveform generation as high risk. Get user
  confirmation before changing SCPI behavior, output behavior, VISA timeout
  defaults, trigger/wait strategy, binary transfer, model safety limits, or
  cleanup behavior.
- Keep real output off by default. Connection, identification, diagnostics,
  queries, and waveform configuration must not enable output implicitly;
  output-on must remain explicit. Do not use `*RST`, preset, setup recall, or
  other broad state-changing operations by default.
- Real hardware access is explicit and opt-in. One-shot live access requires a
  user-supplied `--resource`; an environment variable may be used only as a
  shell convenience. Do not infer, guess, or scan for resources; only explicit
  discovery commands may open enumerated resources. Never commit real
  resources, serial numbers, private IDN data, or private lab addresses.

## 5. Testing And Validation

- Follow [Testing Guidelines](docs/testing-guidelines.md). Default tests must
  run without hardware; use simulators or fake instruments for command,
  validation, trigger-routing, and error-path coverage.
- Run pytest from the repository root. Run the narrowest relevant checks first,
  then broader no-hardware tests when practical.
- Use `.tmp_tests/` for intentional test and validation artifacts.
- Real-instrument validation must be explicit, opt-in, bounded, and use a VISA
  resource supplied by the user. Never infer, scan for, or guess a resource for
  unattended live validation. Do not describe dry-run, simulator, mocked, or
  plan-only results as real-instrument validation.
- If the full test suite is blocked by environment permissions, report the
  limitation and the focused checks that ran. Live validation is not a
  substitute and should run only when live behavior is in scope and approved.
- Report every failed, skipped, blocked, or unexecuted verification step.

## 6. Documentation Boundary

- Keep tracked documentation durable, public, and free of temporary planning,
  transient validation, review, or promotion status, private operator context,
  and run-specific validation results, records, evidence, or artifacts.
- Keep `USER_GUIDE.md` files operator-facing. Keep setup, build, maintainer,
  validation workflow and requirements, and detailed engineering material in
  `README.md` or focused contributor documentation. Include in `USER_GUIDE.md`
  only the minimum information required for normal user operation.
- English documentation is the default. Modify localized documentation only
  when the task explicitly includes it. If a modified localized Markdown file
  already has a corresponding HTML mirror, update that mirror in the same
  change.
- Do not place personal filesystem paths, real VISA resources, instrument
  serial numbers, private lab addresses, or link-local/private network
  addresses in tracked public documentation.
- Operator-facing and product-support documentation must not include internal
  phase names, candidate evidence, unperformed validation, review or promotion
  status or plans, or temporary laboratory-specific context.
