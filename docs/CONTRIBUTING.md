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

Create the exact intended live plan before touching hardware. Set a
PowerShell session variable to the operator-selected VISA resource and pass
it explicitly to the validation script:

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "USB0::...::INSTR"
```

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
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
shareable evidence redact it under the existing privacy policy. Debug
artifacts retain the exact value locally.

Review the target, connection, backend, channels, representative waveform,
planned cases, and safety statements before continuing.

### Real Live

Run the same command without `-PlanOnly` only after the operator is ready:

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
  -Backend system
```

Before any hardware I/O, the runner displays the exact target, connection,
backend, resource, channels, cases, and safety plan. The operator can inspect
the exact resource before confirmation. Redirected standard input is rejected.
Press Enter to start or Ctrl+C to cancel.

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

Full uses the same cases for Preflight, PlanOnly, and Real Live. CH1 covers
all eight static waveforms, internal AM (normal and DSSC), FM, PM, FSK, BPSK,
PWM, and Sum. It also covers Counted and Gated Burst, Linear and Logarithmic
Sweeps, List Sweeps, output settings, and explicit output ON/OFF.
The static cases include representative duty cycle, symmetry, phase, high/low
levels, independent Pulse edges, and PRBS pattern. PWM uses a shared Pulse
edge. The List transition checks Sine, Square, Ramp, Triangle, and CW recovery.
Output configuration exercises High-Z and inverted polarity with limits and
autorange off, then sets 50 ohms and normal polarity with both enabled.

Core capabilities determine the channel count. Dual-channel models also
exercise CH2 status, Sine, AM, Immediate Counted Burst, Linear Sweep, List
transition, and output configuration and control. The runner checks that the
other output stays off. Cases sample representative combinations for each model.

For Real Full, prepare a scope with a 50-ohm termination for the CH1 Output
BNC and a separate source for the rear-panel Ext Trig BNC. The Ext Trig
connector is chassis-referenced. Follow the target model's input and grounding
instructions and verify Trigger Out is disabled before connecting the source.
See the [Keysight Trueform operating and service guide](https://www.keysight.com/us/en/assets/9018-03714/service-manuals/9018-03714.pdf).

The runner prompts for equipment readiness while the output is off. Press
Enter to continue the case, or type `Skip` if you cannot perform the
observation. For External Counted Burst, send one requested edge and look
for three pulses followed by idle. For each Gate polarity, observe pulses
while the gate is active and their cessation when it becomes inactive.
Bus and Timer Burst and Sweep, including Sweep Hold/Return, and Bus List
progression also require operator observation. Each observation has a fixed
window. The runner turns the output off and checks cleanup before asking for
`Yes` or `No`. Disable and disconnect the external source when prompted.
Only the dedicated output-control case and confirmed observation cases use
Output ON.

The report identifies the case, channel, requested settings, status checks,
verification method, operator response, and cleanup result. A passed
`live-config` case establishes command and configuration acceptance.
`live-functional` records a confirmed behavior, such as output ON/OFF or
the operator-observed response. Mode parameters returned by configuration
commands describe normalized request values. Instrument status provides
independent readback for the fields it exposes. Physical observations verify
behavior without calibrating output frequency, amplitude, or jitter.

Required failures, skipped or missing observations, unsuccessful cleanup,
unconfirmed OFF, a dirty or unbounded final error queue, or failed shareable
publication cause Full to fail. Failures attempt best-effort OFF.

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
that revision, covering the exercised Direct CLI scenarios in the reference
environment. Direct CLI Live evidence also covers the Core control paths
shared by Worker. Worker HTTP and lifecycle checks and the offline manifest
use their own automated tests.

Preflight uses hardware-free dry-run and simulator recipes. PlanOnly lists
the same intended inventory and explicitly reports `hardware_touched=false`
and `visa_io_performed=false` without constructing VISA or issuing live SCPI.

Scripts never change Product Live policy. Promotion still requires a separate,
explicit Core policy change; 33510B remains validation-pending. Evidence stays
in the sanitized `shareable/` artifacts.

## Artifact Privacy

Both runners default to a sanitized `shareable/report.json` and
`shareable/summary.md`. Add `-DebugArtifacts` to retain detailed raw command
artifacts in `private/` and sanitized command artifacts in `shareable/`.
The Live runner passes this option to its Preflight run.

Keep `private/` local. It can contain raw resources, identity data, logs, and
paths. Attach the complete generated `shareable/` directory, normally as a ZIP,
and preserve its metadata. If publication fails, the run fails and the runner
attempts to retain only the safe `private/error-summary.md`.

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
