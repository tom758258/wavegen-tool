# Wavegen CLI

The `wavegen-tool` command exposes two related surfaces:

- the Direct CLI identifies, inspects, plans, simulates, or controls one
  explicitly selected instrument resource;
- the Worker CLI starts or talks to a loopback-only Worker whose command and
  lifecycle surface is defined by the
  [Worker contract](../contracts/wavegen-worker-contract.md).

Direct CLI support does not imply Worker support. Both adapters rely on Core
for model identity, capabilities, SCPI, VISA/backend behavior, and safety.

## Installation and Invocation

From the repository root:

```powershell
uv sync --all-extras --locked --link-mode=copy
uv run wavegen-tool --help
uv run wavegen-tool <command> --help
```

Python 3.10 or newer is required. The installed console entry point is also
named `wavegen-tool`.

## Safe Quick Start

Inspect a registered profile offline:

```powershell
uv run wavegen-tool capabilities --model keysight-33521b --json
```

Preview configuration without VISA I/O:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0
```

Run the configuration against the in-memory simulator:

```powershell
uv run wavegen-tool configure-sine `
  --simulate `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0
```

Both paths are hardware-free. Neither is real-instrument validation.

## Safety and Execution Modes

### Live

Without `--dry-run` or `--simulate`, Direct CLI instrument inspection and
control commands require one explicit `--resource`. Core validates the
applicable backend, transport, identity, capabilities, and safety conditions
before performing the operation. Offline introspection and the explicit
resource-listing diagnostic do not require a selected resource.

Normal Direct CLI live use does not accept a planning model override. The
detected physical identity is authoritative. The validation runner supplies an
expected-model guard through its private validation route; the guard can reject
a mismatch but cannot override detected identity.

Normal live control never scans, guesses, infers, or auto-selects a resource.
Discovery happens only when the user explicitly invokes `list-resources`.

### Dry-run

Waveform, sweep, List Sweep, and output-configuration commands accept
`--dry-run`. Dry-run uses an exact registered `--model`, runs Core validation,
and returns the ordered SCPI plan with `executed` false. It does not create a
VISA resource manager, open a resource, query identity, send SCPI, or change
hardware.

The planning model choices are:

- `keysight-33510b`
- `keysight-33512b`
- `keysight-33521b` (default)

### Simulator

`--simulate` uses an in-memory simulator and performs no physical VISA I/O.
Standalone configuration supports the registered model profiles and
independent channel state for the two-channel 33510B and 33512B. Each standalone
CLI process starts with fresh state; a simulated Worker retains its state for
the Worker process lifetime.

Simulation cannot be combined with `--dry-run`, a physical resource, or a
non-system backend. Simulator behavior does not expand Product Live support.

### Common Direct CLI Options

Options appear only on commands that support them:

| Option | Meaning |
| --- | --- |
| `--channel {1,2}` | Selected channel; default `1` |
| `--resource RESOURCE` | Explicit USB or TCPIP/LAN VISA resource for live use |
| `--backend BACKEND` | Core-validated VISA backend; default `system` |
| `--simulate` | Use the in-memory simulator |
| `--dry-run` | Preview validated SCPI without VISA I/O |
| `--model MODEL_ID` | Registered dry-run/simulator planning model |
| `--json` | Emit one JSON result object |

The 33510B and 33512B profiles have two channels. The 33521B has one; Core
rejects Channel 2 in live, dry-run, and simulation.

All selected-channel state-changing operations on a two-channel live
instrument fail closed unless frequency coupling, voltage coupling, Channel 1
tracking, and Channel 2 tracking are reported off. Wavegen never disables
those states automatically. Read-only `status` does not require this guard.

## Offline Introspection

### `manifest`

Print static tool identity and Worker protocol compatibility without starting a
Worker or performing VISA I/O:

```powershell
uv run wavegen-tool manifest --json
```

### `capabilities`

Print the exact registered model identity and capabilities offline:

```powershell
uv run wavegen-tool capabilities `
  --model keysight-33521b `
  --json
```

Both `--model` and `--json` are required. See
[Supported Models](../core/supported-models.md) for the authoritative support
matrix.

## VISA Resource Listing

### `list-resources`

List backend-reported resources:

```powershell
uv run wavegen-tool list-resources --backend system
```

Raw listing returns resource strings reported by the selected backend. It does
not open instrument sessions or send SCPI. Options are:

- `--backend BACKEND` (default `system`)
- `--simulate`
- `--live-only`
- `--serial-baud-rate RATE`
- `--serial-read-termination {CR,LF,CRLF,NONE}`
- `--serial-write-termination {CR,LF,CRLF,NONE}`
- `--json`

`--live-only` is an explicit, opt-in discovery diagnostic. It opens eligible
candidates and sends at most one bounded `*IDN?` query to each. System VISA
checks USB, TCPIP/LAN, and configured ASRL candidates; `@py` checks TCPIP/LAN;
`@bt` has no eligible live-only connection scope. ASRL discovery does not make
ASRL an admitted identify/control transport.

Listing or receiving an IDN response does not establish Product Live support.
The Core identify/control matrix remains `system` with USB or TCPIP/LAN and
`@py` with TCPIP/LAN. There is no backend fallback.

## Instrument Inspection

### `identify`

Query one explicit resource with `*IDN?` and resolve its exact manufacturer and
model:

```powershell
uv run wavegen-tool identify `
  --resource "<EXPLICIT_VISA_RESOURCE>" `
  --backend system `
  --json
```

Options are `--simulate`, `--resource`, `--backend`, and `--json`.
Identification does not reset the instrument, change settings, or enable
output.

### `status`

Read selected-channel function, frequency, voltage, offset, load, and output
state without changing the instrument:

```powershell
uv run wavegen-tool status `
  --resource "<EXPLICIT_VISA_RESOURCE>" `
  --channel 1 `
  --json
```

Options are `--simulate`, `--channel`, `--resource`, `--backend`, and `--json`.

### `read-errors`

Read and drain the instrument system error queue through one identified
session:

```powershell
uv run wavegen-tool read-errors `
  --resource "<EXPLICIT_VISA_RESOURCE>" `
  --max-reads 20 `
  --json
```

`--max-reads` defaults to 20 and accepts 1 through 100. Returned entries are
removed from the instrument. A successful command may still report instrument
errors, so automation must inspect `has_errors`. `limit_reached=true` means the
command did not confirm an empty queue. The command does not send `*CLS`,
`*RST`, or waveform/output writes.

## Static Waveform Configuration

Every configuration command validates the complete request before live writes,
turns off only the selected output, and leaves it off. Only `output --state on`
enables output.

Voltage-bearing waveform commands accept exactly one voltage form:

- `--amplitude-vpp` with optional `--offset-v`; or
- `--high-level-v` together with `--low-level-v`.

The forms cannot be mixed, and high level must be greater than low level. Core
normalizes a high/low pair to amplitude and offset. For waveform commands,
`--load` is `50` or `high-z`; it configures the instrument's assumed load and
does not detect the physically connected load.

| Command | Required waveform input | Additional options |
| --- | --- | --- |
| `configure-sine` | `--frequency-hz`, one voltage form | modulation/burst, `--phase-deg`, `--load` |
| `configure-square` | `--frequency-hz`, one voltage form | modulation/burst, `--duty-cycle-percent`, `--phase-deg`, `--load` |
| `configure-ramp` | `--frequency-hz`, one voltage form | modulation/burst, `--symmetry-percent`, `--phase-deg`, `--load` |
| `configure-triangle` | `--frequency-hz`, one voltage form | modulation/burst, `--phase-deg`, `--load` |
| `configure-pulse` | `--frequency-hz`, `--pulse-width-s`, one voltage form | AM/PWM/Sum/burst, edge controls, `--phase-deg`, `--load` |
| `configure-dc` | `--voltage-v` | `--load` |
| `configure-noise` | `--bandwidth-hz`, one voltage form | `--load` |
| `configure-prbs` | `--bit-rate-bps`, one voltage form | pattern, edge time, burst, `--load` |

Sine, square, ramp, triangle, and pulse accept `--phase-deg` from -360 through
360 degrees, defaulting to 0. Status does not report phase.

Important waveform-specific limits include:

- sine, square, pulse, and noise use the selected model's 20 MHz or 30 MHz
  registered frequency capability where applicable;
- ramp and triangle frequency is 0.000001 Hz through 200 kHz;
- square duty cycle is 0.01% through 99.99% and is narrowed by the 16 ns
  minimum pulse width at higher frequencies;
- ramp symmetry is 0% through 100%;
- pulse width is at least 16 ns; shared or independent edge times are 8.4 ns
  through 1 microsecond and must fit the period/width constraints;
- noise bandwidth starts at 0.001 Hz and is capped by the selected model
  capability;
- PRBS bit rate is 0.001 through 50,000,000 bit/s, patterns are
  `PN7`, `PN9`, `PN11`, `PN15`, `PN20`, or `PN23`, and its shared edge time
  must fit one bit period.

For a 50-ohm load setting, the common waveform amplitude range is 0.001 through
10 Vpp and `abs(offset) + amplitude / 2` must not exceed 5 V. For high
impedance, it is 0.002 through 20 Vpp with a 10 V boundary. DC accepts -5
through +5 V at 50 ohms or -10 through +10 V at high impedance. Core remains
authoritative for every relationship and model-specific limit.

Representative pulse preview:

```powershell
uv run wavegen-tool configure-pulse `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --pulse-width-s 0.0001 `
  --amplitude-vpp 0.1 `
  --edge-time-s 0.00000001 `
  --load 50
```

Use `--leading-edge-s` and `--trailing-edge-s` together for independent pulse
edges. They cannot be mixed with `--edge-time-s`. Live pulse configuration
queries and verifies the instrument's applicable dynamic edge limits; dry-run
can validate only the hardware-free constraints.

## Modulation and Sum

Static carrier commands expose these optional, Core-validated groups:

| Mode | Carriers | Required option group |
| --- | --- | --- |
| AM | sine, square, ramp, triangle, pulse | `--am-frequency`, `--am-depth`; optional `--am-type {normal,dssc}` |
| FM | sine, square, ramp, triangle | `--fm-frequency`, `--fm-deviation` |
| PM | sine, square, ramp, triangle | `--pm-frequency`, `--pm-deviation-deg` |
| FSK | sine, square, ramp, triangle | `--fsk-hop-frequency`, `--fsk-rate` |
| BPSK | sine, square, ramp, triangle | `--bpsk-phase-shift-deg`, `--bpsk-rate` |
| PWM | pulse | `--pwm-frequency`, `--pwm-deviation-s` |
| Sum | sine, square, ramp, triangle, pulse | `--sum-frequency`, `--sum-amplitude-percent` |

The internal modulation source is fixed by the corresponding Core path; AM,
FM, PM, PWM, and Sum use an internal sine source. Partial groups and unsupported
or mutually exclusive combinations are rejected.

Key limits and relationships are:

- AM depth is 0% through 100%; `--am-type` defaults to `normal`.
- FM modulation frequency and deviation are at least 0.000001 Hz. Deviation is
  limited by the carrier, a 15 MHz ceiling, and the carrier function maximum.
- PM deviation is 0 through 360 degrees and carrier frequency must be greater
  than 20 times modulation frequency.
- FSK rate is 0.000125 through 1,000,000 Hz; hop frequency must fit the selected
  carrier capability.
- BPSK phase shift is 0 through 360 degrees and rate is 0.001 through
  1,000,000 Hz.
- PWM width deviation must stay strictly within the validated pulse width,
  period, and edge margins.
- Sum amplitude is 0% through 100% relative to carrier amplitude.

Example AM dry-run:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33512b `
  --channel 2 `
  --frequency-hz 1000000 `
  --amplitude-vpp 0.1 `
  --am-frequency 1000 `
  --am-depth 50
```

An ordinary static waveform or sweep configuration disables applicable prior
modulation state on the selected channel before configuring the new mode.

## Counted and Gated Burst

Sine, square, ramp, triangle, pulse, and PRBS support Counted Burst and Gated
Burst. Noise does not.

Counted Burst options are:

- `--burst-count`
- `--burst-period-s`
- `--burst-trigger-source {immediate,bus,timer,external}`
- `--burst-trigger-timer-s`
- `--burst-trigger-slope {positive,negative}`

Immediate uses count and period. Bus uses count and omits period/timer. Timer
uses count and `--burst-trigger-timer-s`. External uses count, may choose the
trigger slope, and omits internal period/timer. Count is waveform cycles, or
bits for PRBS. When Counted Burst is enabled, phase remains 0 degrees.

Count is 1 through 100,000,000. An Immediate period is 0.000001 through 8000
seconds. Immediate/Timer periods must allow the complete burst plus the Core
safety margin. Counted sine and square carriers are capped at 6 MHz. Counted
Burst is finite-only and mutually exclusive with modulation modes.

Gated Burst uses:

- `--gated-burst`
- optional `--gate-polarity {normal,inverted}` (default `normal`)

It is mutually exclusive with every Counted Burst option. Both burst modes
leave output off and affect only the selected channel.

## Linear and Logarithmic Frequency Sweeps

The Direct CLI provides:

- `configure-sine-sweep`
- `configure-square-sweep`
- `configure-ramp-sweep`
- `configure-triangle-sweep`

Required sweep options are:

- `--start-frequency-hz`
- `--stop-frequency-hz`
- `--spacing {linear,logarithmic}`
- `--sweep-time-s`

Optional common options include `--hold-time-s`, `--return-time-s`,
`--trigger-source {immediate,bus,timer}`, `--trigger-timer-s`, one voltage form,
`--phase-deg`, and `--load`. Hold and return default to zero; trigger defaults
to Immediate. A Timer interval must be 0.000001 through 8000 seconds and at
least the complete sweep, hold, and return duration.

Square sweeps add `--duty-cycle-percent`, validated at the higher endpoint.
Ramp sweeps add `--symmetry-percent`. Sweeps are selected-channel operations,
leave output off, and do not provide coupled dual-channel start.

```powershell
uv run wavegen-tool configure-sine-sweep `
  --dry-run `
  --model keysight-33521b `
  --start-frequency-hz 1000 `
  --stop-frequency-hz 10000 `
  --spacing logarithmic `
  --sweep-time-s 2 `
  --amplitude-vpp 0.1
```

## Frequency List Sweep

The Direct CLI provides:

- `configure-sine-list-sweep`
- `configure-square-list-sweep`
- `configure-ramp-list-sweep`
- `configure-triangle-list-sweep`

`--frequencies-hz` is a comma-separated list of 1 through 128 frequencies.
Input order is preserved and duplicates are allowed. Trigger defaults to
Immediate and can be changed with `--trigger-source bus`.

Immediate requires one shared `--dwell-s` from 0.000001 through 1000 seconds.
Bus trigger requires dwell to be omitted; each later instrument-wide bus
trigger advances the list. List Sweep configuration itself does not send that
trigger.

```powershell
uv run wavegen-tool configure-sine-list-sweep `
  --dry-run `
  --model keysight-33521b `
  --frequencies-hz "1000,3000,7000" `
  --dwell-s 0.005 `
  --trigger-source immediate `
  --amplitude-vpp 0.1
```

Square List Sweep adds duty cycle; Ramp List Sweep adds symmetry. Pulse, PRBS,
arbitrary, External, Timer, and Manual-triggered lists, per-point dwell, list
files, stored lists, and markers are outside the Direct CLI surface. List Sweep
is not a Worker command.

## Output and Trigger

### `configure-output`

Configure a partial selected-channel output load/polarity/limit/autorange update
while retaining output off:

```powershell
uv run wavegen-tool configure-output `
  --dry-run `
  --model keysight-33512b `
  --channel 2 `
  --load 1000 `
  --polarity inverted `
  --voltage-limit-low -1 `
  --voltage-limit-high 1 `
  --voltage-limits on `
  --autorange off
```

At least one field is required. Options are `--load <1..10000|high-z>`,
`--polarity {normal,inverted}`, `--voltage-limit-low`,
`--voltage-limit-high`, `--voltage-limits {on,off}`, and
`--autorange {on,off}`. Enabling limits requires a complete low/high pair, and
high must be at least 0.001 V above low. Changing load while limits are enabled
requires explicitly disabling or fully reconfiguring the limits in the same
request. `ONCE` autorange is not supported.

### `output`

Set only the selected output state:

```powershell
uv run wavegen-tool output `
  --resource "<EXPLICIT_VISA_RESOURCE>" `
  --channel 1 `
  --state off
```

`--state {on,off}` is required. Output-on is never implicit; review the
configured signal and physical load before explicitly selecting `on`.

### `trigger`

Send one instrument-wide IEEE-488.2 `*TRG` without waiting:

```powershell
uv run wavegen-tool trigger --resource "<EXPLICIT_VISA_RESOURCE>"
```

There is no channel option. Every armed channel using Bus trigger may respond.
The command does not change output state. In simulation only, `--model` selects
the registered profile.

## JSON and Errors

Use `--json` for one JSON object from a Direct CLI command. Do not parse
human-readable output as a stable machine API. Argument parsing errors retain
standard argparse stderr/usage behavior; after successful parsing, JSON mode
keeps the result on stdout.

Direct CLI exit categories are:

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 2 | CLI usage or waveform parameter error |
| 10 | Unsupported transport |
| 11 | Unsupported connection scope |
| 20 | Resource manager error |
| 21 | Resource open error |
| 22 | IDN query error |
| 23 | Malformed IDN |
| 24 | Unsupported instrument |
| 25 | VISA cleanup error |
| 26 | Resource discovery error |
| 27 | VISA write error |
| 28 | Status query error |
| 29 | Waveform verification error |
| 30 | Error-queue query error |
| 70 | Internal error |

## Worker CLI

The Worker is a loopback-only local HTTP control plane. It currently supports
only `keysight-33521b`; its simulator uses 33521B process-lifetime state.

Start a simulated Worker:

```powershell
uv run wavegen-tool worker --mode simulate --control-port 8765
```

Worker startup options are:

- `--mode {live,simulate}` (required)
- `--resource` (required for live, forbidden for simulate)
- `--backend` (default `system`)
- `--control-port` (default `0`; zero selects an available loopback port)
- `--allow-output-writes` (off by default)

Worker startup validates admission but does not open a live resource or
identify an instrument. A `ready` lifecycle event means the loopback HTTP plane
is serving; it does not mean that physical hardware is connected or identified.

The authoritative Worker command set is:

- `identify`, `status`, and `read-errors`;
- `configure-sine`, `configure-square`, `configure-ramp`,
  `configure-triangle`, `configure-pulse`, `configure-dc`, `configure-noise`,
  and `configure-prbs`;
- the four ordinary sine/square/ramp/triangle sweep commands;
- `output`.

The Worker does not expose Direct CLI resource listing, `trigger`,
`configure-output`, List Sweep, arbitrary SCPI, modulation/burst commands, or
any other unlisted command. Worker command names use kebab-case and request
argument fields use snake_case.

Live Worker write safety is narrower than Direct CLI invocation:

- identify/status/read-errors do not require `allow_output_writes`;
- `configure-*` requires Worker startup with `--allow-output-writes`;
- output-off is always allowed;
- output-on requires both `--allow-output-writes` and per-request
  `confirm_output=true`.

The Worker has one active job slot. A second request is rejected as `busy`, or
as `stopping` after a cooperative stop request.

Every `POST /command` supplies a request context. `simulate` and `dry_run`
contexts require `planning_model_id: "keysight-33521b"` and must omit
`expected_model_id`. A `live` context may supply
`expected_model_id: "keysight-33521b"` as a guard and must omit
`planning_model_id`. Exact context and argument schemas remain contract-owned.

An accepted command returns HTTP 202 and runs asynchronously. Malformed or
inadmissible requests return HTTP 400; busy or stopping admission returns HTTP
409. The Worker does not create per-job artifacts.

Worker machine stdout is schema-version-2 JSONL only. Events for one process
share a `run_id` and include lifecycle events such as `ready`, `job_accepted`,
`job_started`, `job_finished`, `job_failed`, `stop_requested`, `error`, and
`summary`. Human diagnostics are not a machine API.

### Worker Lifecycle Clients

Submit one domain command:

```powershell
uv run wavegen-tool send-command `
  --port 8765 `
  --command status `
  --context-json '{"mode":"simulate","planning_model_id":"keysight-33521b"}' `
  --json
```

`send-command` requires `--port`, `--command`, and `--context-json`. It also
accepts `--arguments-json` (default `{}`), `--job-id`, `--timeout-ms` (default
1000), and `--json`. Submission does not wait for job completion.

- `worker-status` reads memory-only lifecycle status; it is not instrument
  status. It accepts required `--port`, optional `--timeout-ms` (default 1000),
  and optional `--json`.
- `wait-ready` polls until ready or a bounded deadline. It adds
  `--wait-timeout-ms` (default 30000) and `--poll-ms` (default 200) to
  `--port`, `--timeout-ms`, and `--json`.
- `worker-stop` requests cooperative stop. It does not wait for process exit
  and does not turn output off automatically. It accepts `--port`,
  `--timeout-ms`, and `--json`.

With `--json`, each lifecycle client emits one compact schema-version-2 JSON
object. An accepted `send-command` result confirms admission, not job
completion; use lifecycle status to observe the result.

For normal live shutdown, submit explicit output-off before stopping. Use the
[Worker contract](../contracts/wavegen-worker-contract.md),
[common protocol](../contracts/common-worker-protocol.md), and
[orchestrator workflows](../contracts/common-orchestrator-workflows.md) for the
exact schemas, lifecycle meanings, and queue rules.

## CLI Validation Scripts

These scripts are contributor tools, not normal requirements for every pull
request. See [Contributing](../CONTRIBUTING.md) for evidence acceptance and
privacy policy.

### Hardware-free Preflight

```powershell
.\scripts\preflight-cli.ps1 -Target all
```

`-Target` accepts `all` or one registered model ID. `-Python` and `-OutputRoot`
are optional: Python defaults to `.\.venv\Scripts\python.exe` and the output
root defaults to `.tmp_tests\cli_preflight`. Preflight runs capability,
dry-run, and simulator checks without a resource or hardware I/O.

### PlanOnly

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Backend system `
  -Resource "<EXPLICIT_VISA_RESOURCE>" `
  -PlanOnly
```

PlanOnly requires the exact intended resource but does not open VISA, touch
hardware, or send live SCPI. Console and shareable output redact the resource;
private evidence retains it locally.

The live runner parameters are:

| Parameter | Behavior |
| --- | --- |
| `-Target` | Required exact model ID; `all` is rejected |
| `-Connection` | Required `usb` or `tcpip`, matching the resource prefix |
| `-Resource` | Required exact operator-supplied resource |
| `-Backend` | Optional; defaults to `system` |
| `-PlanOnly` | Optional hardware-free planning switch |
| `-Python` | Optional; defaults to `.\.venv\Scripts\python.exe` |
| `-OutputRoot` | Optional; defaults to `.tmp_tests\cli_live` |

### Real Live

Remove `-PlanOnly` only when explicit hardware validation is required. Before
any I/O, the runner prints the exact target, connection, backend, resource,
channels, cases, representative waveform, and safety plan. Redirected input is
rejected and only exact uppercase `YES` authorizes the run. The runner does not
scan, guess, or auto-select a resource and never enables output.

After initial parameter admission, a run creates separate `private/` and
`shareable/` artifacts. Invalid target, connection, or resource admission exits
before creating the run directory. Private artifacts may contain raw or
sensitive evidence: never commit, attach, upload, or paste them into a public
pull request. Shareable artifacts are produced by the existing sanitization
and redaction flow; attach the complete generated directory, normally as a ZIP,
without manually rebuilding its metadata. Passing evidence applies only to the
cases exercised. See [Contributing](../CONTRIBUTING.md) for acceptance and
Product Live decision policy.

## Related Documentation

- [Core overview](../core/README.md)
- [Core integration](../core/integration.md)
- [Supported models](../core/supported-models.md)
- [Contributing](../CONTRIBUTING.md)
- [Testing guidelines](../testing-guidelines.md)
- [Worker contract](../contracts/wavegen-worker-contract.md)
