# Contributing to Wavegen Tool

Contributions are welcome, including focused bug fixes, documentation,
hardware-free tests, CLI/Core improvements, and carefully reviewed instrument
support work. Keep each change narrow and read the documentation for the
component and contracts it affects.

## Development Setup

Work from the repository root with Python 3.10 or newer. The documented
environment uses `uv`:

```powershell
uv sync --all-extras --locked --link-mode=copy
```

Run the narrowest relevant hardware-free checks first. Follow the
[Testing Guidelines](testing-guidelines.md), report every check that was not
run, and do not substitute a simulator or dry-run result for hardware evidence.

The canonical broader development checks are:

```powershell
uv run python -m ruff check src tests
uv run python -m pytest tests -q -p no:cacheprovider
uv run python -m build
```

Run only the checks relevant to a focused change before expanding to these
broader commands. A small documentation or isolated regression change does not
automatically require the full suite and build.

## Architecture and Ownership

Wavegen Tool is one distribution with three import packages:

- `wavegen_tool_core` owns identity, model capabilities, support policy,
  request validation, SCPI/VISA behavior, simulation, and instrument safety.
- `wavegen_tool_cli` owns argument parsing, CLI rendering, exit behavior, and
  the Direct CLI and Worker adapters.
- `wavegen_tool_webui` is reserved for a future adapter.

Core must not import CLI or WebUI. Adapters must use Core rather than copying a
model registry, capability decision, SCPI path, VISA session, or safety rule.
Read the relevant files in [`docs/contracts/`](contracts/) before changing
Worker, subprocess orchestration, JSON/JSONL, or HTTP control/status behavior.

## Testing Expectations

Every pull request needs relevant hardware-free validation. Documentation-only
changes and hardware-independent refactors normally need targeted static or
unit checks, not a physical instrument.

Real-instrument evidence is normally required when a change affects live model
support, instrument commands, waveform behavior, backend or transport scope,
identity checks, selected-channel behavior, output safety, VISA session or
cleanup behavior, or the live validation workflow. Discuss unusually broad or
risky validation scope with a maintainer before using hardware.

## General Contribution Workflow

For changes that can be fully verified without hardware:

1. Make the focused change.
2. Run the relevant targeted hardware-free tests or static checks.
3. Check formatting with `git diff --check`.
4. Check affected documentation links and public examples.
5. Open a focused pull request that states exactly what ran and what did not.

## Real-Instrument Validation Workflow

When hardware evidence is required, use this workflow:

```text
development
-> normal hardware-free tests
-> preflight-cli.ps1
-> live-cli-check.ps1 -PlanOnly
-> operator reviews the exact scope and resource
-> real live-cli-check.ps1
-> private/shareable artifacts
-> pull-request evidence
-> maintainer review
-> separate Product Live decision, when applicable
```

### Preflight

Run the hardware-free readiness check:

```powershell
.\scripts\preflight-cli.ps1
```

The existing parameters are:

| Parameter | Behavior |
| --- | --- |
| `-Suite` | `smoke` (default) or `full` |
| `-Target` | `all` by default, or one of `keysight-33510b`, `keysight-33512b`, `keysight-33521b` |
| `-Python` | Python executable; defaults to `.\.venv\Scripts\python.exe` |
| `-OutputRoot` | Artifact root; defaults to `.tmp_tests\cli_preflight` |

Preflight performs offline capability checks and per-channel dry-run and
simulator cases. It does not need a VISA resource and does not open hardware.

Running preflight manually is the recommended readiness step. The live runner
also invokes the target's preflight automatically before it proceeds, so a
contributor may see those cases run again.

### PlanOnly

Create the exact intended live plan before touching hardware:

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Resource "<EXPLICIT_VISA_RESOURCE>" `
  -Backend system `
  -PlanOnly
```

`-Target`, `-Connection`, and `-Resource` are required. The target must be one
exact model ID; `all` is not accepted. Connection is `usb` or `tcpip` and must
match the explicit resource prefix. `-Backend` defaults to `system`.
`-Python` and `-OutputRoot` are also supported; the live output root defaults
to `.tmp_tests\cli_live`.

PlanOnly does not create a VISA resource manager, open VISA, perform hardware
I/O, or send live SCPI. The resource remains required so that the plan
represents the exact intended connection. PlanOnly console output and
shareable evidence redact it under the existing privacy policy; private
evidence retains the exact value locally.

Review the target, connection, backend, channels, representative waveform,
planned cases, and safety statements before continuing.

### Real Live

Run the same command without `-PlanOnly` only after the operator is ready:

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Resource "<EXPLICIT_VISA_RESOURCE>" `
  -Backend system
```

Before any hardware I/O, the runner displays the exact target, connection,
backend, resource, channels, cases, and safety plan. The operator can inspect
the exact resource before confirmation. Redirected standard input is rejected;
only an interactive, exact uppercase `YES` starts the live run.

The runner never scans, guesses, infers, or auto-selects a resource. Resource
discovery is a separate user-invoked Direct CLI diagnostic and is not part of
contributor validation.

Both runners accept `-Suite smoke|full`, defaulting to `smoke`. Smoke is a
representative CLI Live sanity check: exact identity, stale-error drain,
capability-driven per-channel Sine configuration/readback, output OFF before
and after each channel, and a clean final error queue. Smoke never uses Output
ON. **smoke PASS != all implemented features validated**.

Full requires a readable Git HEAD and a clean working tree before creating
artifacts. Reports record that checked `git_head` and `git_clean=true`; a
changed HEAD or dirty tree before publishing PASS evidence fails the gate.
Scripts never stash, reset, or clean the working tree. Smoke has no clean-tree
requirement. Standalone Full preflight also requires Windows PowerShell Desktop
on Windows and project Python; it has no connection/backend parameters.

Full admission is limited to Windows PowerShell, the project's
`.venv/Scripts/python.exe`, an explicit operator-supplied USB resource, and
`-Backend system`. Other connection/backend combinations fail closed for Full;
Smoke retains its existing connection/backend behavior. Detected `*IDN?` must
exactly match `-Target`. Neither suite scans, guesses, resets, recalls a setup,
or automatically changes coupling/tracking.

Full uses one shared canonical inventory for preflight, PlanOnly, and Real
Live. CH1 covers all eight static waveforms; AM (normal and DSSC), FM, PM, FSK,
BPSK, PWM, Sum; Immediate/Bus/Timer/External Counted Burst and Gated Burst;
Sine Linear/Logarithmic/Bus/Timer and Square/Ramp/Triangle sweeps; the consecutive
Sine -> Square -> Ramp -> Triangle List transition and Sine CW recovery;
Bus-triggered List with public `trigger`; output configuration and dedicated
output control. Bus Burst and Bus Sweep also execute public `trigger`. Static
Pulse uses independent 40 ns leading and 60 ns trailing edges; PWM uses a
shared 50 ns edge. Both use 1 kHz, 100 us width, 0.1 Vpp, 0 V offset, and 50 ohms.
The inventory remains 33 CH1 recipes and eight CH2 parity recipes.

Channel count comes from Core capabilities. Dual-channel models add only CH2
status, Sine, AM, Immediate Counted Burst, Sine Sweep, the List transition,
output configuration, and output control. Single-channel models never plan CH2.
The dedicated output-control case configures Sine at 1 kHz, 0.1 Vpp, 0 V offset,
and a 50-ohm load setting, confirms OFF, briefly enables ON and confirms it,
then disables output and confirms OFF. No other case enables output.

Each Full case records command success, available public status readback,
bounded case-local error-queue diagnostics, and selected-channel OFF cleanup.
Modulation/Burst/Sweep/List details not exposed by public status are admitted
through CLI success, instrument acceptance, clean case errors, and safe
cleanup; this is not a measurement of analog performance or external triggering.
External/Gated cases validate configuration without an external signal.
Any required FAIL, N/A, missing result, cleanup failure, unconfirmed OFF, dirty
final queue, failure to reach code 0 within bounded reads, or shareable
artifact failure makes Full fail. Failures attempt best-effort OFF.

Use `-Suite full` consistently for the admission sequence:

```text
preflight full PASS
+ PlanOnly full PASS
+ Real Live full PASS
+ all required cases PASS
+ cleanup PASS
+ outputs safe OFF
+ final error queue clean
+ shareable evidence PASS
```

Together these constitute formal admission evidence for the exact model at
that revision, covering the currently implemented Direct CLI Product Live
feature scope in the reference environment. Preflight and PlanOnly alone are
not Live evidence. Preflight uses dry-run/simulator recipes without hardware;
standalone simulator processes do not prove live transitions. PlanOnly lists
the same intended inventory and explicitly reports `hardware_touched=false`
and `visa_io_performed=false` without constructing VISA or issuing live SCPI.

Scripts never change Product Live policy. Promotion still requires a separate,
explicit Core policy change; 33510B remains validation-pending. Evidence stays
in the existing local `private/` and sanitized `shareable/` artifacts.

## Artifact Privacy

After initial parameter admission creates a run directory, validation output is
separated into:

```text
private/
shareable/
```

`private/` contains raw or potentially sensitive evidence. It remains local:

- never commit it;
- never attach or upload it;
- never paste it into a public pull request;
- never use it as a fallback when shareable generation fails.

`shareable/` is produced by the existing sanitization and redaction flow. It is
the evidence contributors may publish. Attach the complete generated
`shareable/` directory, normally as a ZIP. Do not manually rebuild or rewrite
its metadata.

If shareable generation fails, the run is not acceptable as public evidence.
Private evidence stays local and may be used for diagnosis, but must not be
uploaded as a substitute.

Never commit or publish a real VISA resource, serial number, raw IDN response,
private or link-local address, private hostname, personal filesystem path, or
unredacted lab diagnostic.

## Hardware Evidence in Pull Requests

Keep the pull-request body short and attach the complete generated
`shareable/` directory. A suitable summary is:

```text
Hardware validation

Target:
Connection:
Backend:
Validation revision:

Preflight: PASS / FAIL
PlanOnly: PASS / FAIL
Real Live: PASS / FAIL

Attached evidence:
shareable/
```

A failed run still has diagnostic value. Attach its generated shareable
evidence when available, and distinguish `PASS`, `FAIL`, `N/A`, skipped, and
planned-only cases. Do not describe an unexercised case as passing.

## Maintainer Evidence Review

Maintainers should confirm that:

- evidence corresponds to the exact model, connection, backend, and revision;
- detected identity and the expected-model guard match;
- planned and exercised cases are distinguishable and complete for the runner;
- all required cases have recorded outcomes, with no required passing case
  reported as `FAIL` or `N/A`;
- shareable evidence was generated successfully;
- no resource, serial, IDN, address, hostname, or local-path leak is present;
- selected-channel and safe output handling match the current runner contract;
- required cleanup and safe-OFF outcomes are recorded;
- the final required error queue is clean and did not reach its read limit.

Cleanup failure, inability to confirm a required safe-OFF result, or a dirty
final required error queue must not be accepted as passing Product Live
evidence. Preserve the shareable output from such a run for diagnosis.

## Product Live Decisions

Passing Full admission validation provides evidence eligible for a subsequent
Product Live decision for the exact model and revision:

```text
passing validation != automatic Product Live promotion
```

Product Live support requires an explicit, independent maintainer decision and
corresponding runtime metadata and public documentation changes. A passing
33510B validation run does not make that model Product Live. Do not introduce
automatic promotion, an evidence registry, a contributor database, a lifecycle
state machine, a bot, or a promotion workflow.

## Pull Request Checklist

- [ ] The change is focused and excludes unrelated cleanup.
- [ ] Relevant hardware-free checks were run and reported accurately.
- [ ] Documentation links and privacy-sensitive examples were checked.
- [ ] Public behavior and contracts changed only where explicitly in scope.
- [ ] Hardware evidence is included only when the change requires it.
- [ ] The complete generated `shareable/` directory is attached when needed.
- [ ] `private/` remains local and uncommitted.
- [ ] Evidence claims only the exact cases actually exercised.
- [ ] Failed, skipped, `N/A`, cleanup, and error-queue outcomes are not
      misrepresented as passing.
- [ ] Passing validation is not described as automatic Product Live support.
