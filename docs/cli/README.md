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

Normal installation requires `pyvisa` and does not install `pyvisa-py`.
Developers using `--backend @py` must install it manually after `uv sync`:

```powershell
uv pip install pyvisa-py
```

`uv sync` performs an exact sync and may remove manually installed packages;
sync the environment first, then install `pyvisa-py`.

The default `--backend system` requires a system-provided VISA runtime, such as
Keysight IO Libraries Suite. It calls `pyvisa.ResourceManager()` using PyVISA's
own default backend resolution, which does not guarantee selection of `@ivi`.
Explicit `--backend @py` calls `pyvisa.ResourceManager("@py")`. Wavegen adds no
backend fallback. Product Live still admits `system` with USB or TCPIP/LAN and
`@py` with TCPIP/LAN; `@py` with USB is rejected, and `@bt` has no Product Live
scope.

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
control commands use an explicit `--resource` when provided; otherwise they
use the operator-set `WAVEGEN_TOOL_RESOURCE` environment variable. Core validates the
applicable backend, transport, identity, capabilities, and safety conditions
before performing the operation. Offline introspection and the explicit
resource-listing diagnostic do not require a selected resource.

Normal Direct CLI live use does not accept a planning model override. The
detected physical identity is authoritative. The validation runner supplies an
expected-model guard through its private validation route; the guard can reject
a mismatch but cannot override detected identity.

Normal live control never scans, guesses, or auto-selects a resource.
The environment variable must be set by the operator to one exact resource.
Discovery happens only when the user explicitly invokes `list-resources`.

For example, in PowerShell (replace the placeholder with your instrument's
VISA resource):

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "USB0::...::INSTR"
uv run wavegen-tool identify --json
```

A command's `--resource` takes precedence over `WAVEGEN_TOOL_RESOURCE`.
If neither specifies a usable resource, Direct CLI live use fails without
opening a VISA connection. Worker Live and validation scripts still require
an explicit resource argument.

### Dry-run

Waveform, sweep, List Sweep, output-configuration, and Arbitrary commands accept
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
| `--resource RESOURCE` | Explicit USB or TCPIP/LAN VISA resource; overrides `WAVEGEN_TOOL_RESOURCE` in Direct CLI live use |
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

For a JSON inventory of those resource strings (still no instrument sessions):

```powershell
uv run wavegen-tool list-resources --backend system --json
```

To explicitly query eligible resources and show only those responding to
`*IDN?`, use `--live-only`. This actively opens sessions; it is **not** a
passive scan:

```powershell
uv run wavegen-tool list-resources --backend system --live-only
uv run wavegen-tool list-resources --backend system --live-only --json
```

For a TCPIP/LAN discovery check using separately installed PyVISA-py:

```powershell
uv run wavegen-tool list-resources --backend '@py' --live-only --json
```

To inspect the simulated resource inventory without physical VISA access:

```powershell
uv run wavegen-tool list-resources --simulate --json
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

With `--live-only`, each reported responder is labeled by its model support
status: `PRODUCT_LIVE_MODEL` (model admitted by Product policy),
`REGISTERED_ONLY` (registered but not Product Live), `UNSUPPORTED`
(unregistered manufacturer/model combination), or `UNIDENTIFIED`
(nonempty but unparseable IDN). Unsupported and unidentified responders remain
listed. JSON entries also provide `model_id` and `model_support_status`;
raw listing uses `null` for these fields because it performs no IDN query.
No serial number, firmware, or raw IDN is included in list output.

Listing or receiving an IDN response does not establish usable Product Live
control over the selected backend/transport. The Core identify/control matrix
remains `system` with USB or TCPIP/LAN and `@py` with TCPIP/LAN. There is
no backend fallback.

## Instrument Inspection

### `identify`

Query one explicit resource with `*IDN?` and resolve its exact manufacturer and
model:

```powershell
uv run wavegen-tool identify `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
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
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --channel 1 `
  --json
```

Options are `--simulate`, `--channel`, `--resource`, `--backend`, and `--json`.

### `readback-advanced`

Inspect the **currently active** Advanced Mode settings, without reconfiguring
the instrument. The response includes `advanced_mode` and `settings`. When no
Advanced Mode is enabled it returns `CW` with an empty settings object.
Unknown or conflicting instrument responses fail rather than returning CW.

```powershell
uv run wavegen-tool readback-advanced --resource "$env:WAVEGEN_TOOL_RESOURCE" --channel 1 --json
uv run wavegen-tool readback-advanced --simulate --model keysight-33512b --channel 2 --json
```

This is a read-only configuration inspection, not a waveform measurement or a
comparison with a previous request. It never sets output, trigger, or waveform
parameters. The `--simulate` Direct CLI command starts a fresh simulator on
each invocation; use a persistent Worker simulator to test readback after a
separate simulated configure job. The existing `--verify-readback` remains
the opt-in configure-and-compare operation. For active Counted Burst and
ordinary Sweep, `settings.trigger_delay_s` reports the delay stored on the
selected channel, including a setting retained from an earlier operation.

### `read-errors`

Read and drain the instrument system error queue through one identified
session:

```powershell
uv run wavegen-tool read-errors `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
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

Additional static waveform previews, each hardware-free and independent:

```powershell
uv run wavegen-tool configure-square --dry-run --model keysight-33521b --frequency-hz 1000 --amplitude-vpp 0.1 --duty-cycle-percent 50
uv run wavegen-tool configure-ramp --dry-run --model keysight-33521b --frequency-hz 1000 --amplitude-vpp 0.1 --symmetry-percent 50
uv run wavegen-tool configure-triangle --dry-run --model keysight-33521b --frequency-hz 1000 --amplitude-vpp 0.1
uv run wavegen-tool configure-dc --dry-run --model keysight-33521b --voltage-v 0.1
uv run wavegen-tool configure-noise --dry-run --model keysight-33521b --bandwidth-hz 1000 --amplitude-vpp 0.1
uv run wavegen-tool configure-prbs --dry-run --model keysight-33521b --bit-rate-bps 1000000 --amplitude-vpp 0.1 --pattern PN7
```

Sine is shown in [Safe Quick Start](#safe-quick-start); Pulse is shown above.

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

Select the internal modulation shape with `--am-shape`, `--fm-shape`,
`--pm-shape`, `--pwm-shape`, or `--sum-shape`. Each accepts `sine` (the
default), `square` (fixed 50% duty cycle), `up-ramp`, `triangle`, or
`down-ramp`. Supply the required options for that mode when setting a shape.
Product Live requires the `sine` shape. Dry-run and simulation accept all
five shapes for the supported carriers.

Key limits and relationships are:

- AM, FM, PM, PWM, and Sum modulation frequencies start at 0.000001 Hz.
  The maximum for `sine` and `square` is 20 MHz on 33510B and 33512B and
  30 MHz on 33521B. Both ramps and triangle have a 200 kHz maximum.
- AM depth is 0% through 100%. The default AM type is `normal`.
- FM deviation starts at 0.000001 Hz. Its maximum depends on the carrier,
  the carrier function maximum, and a 15 MHz ceiling.
- PM deviation is 0 through 360 degrees and carrier frequency must be greater
  than 20 times modulation frequency.
- FSK rate is 0.000125 through 1,000,000 Hz; hop frequency must fit the selected
  carrier capability.
- BPSK phase shift is 0 through 360 degrees and rate is 0.001 through
  1,000,000 Hz.
- PWM width deviation must stay strictly within the validated pulse width,
  period, and edge margins.
- Sum amplitude is 0% through 100% relative to carrier amplitude.

The following dry-run plans AM with a square modulation shape.

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33512b `
  --channel 2 `
  --frequency-hz 1000000 `
  --amplitude-vpp 0.1 `
  --am-frequency 1000 `
  --am-depth 50 `
  --am-shape square --json
```

Other internal modulation and Sum examples (run each independently):

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --fm-frequency 5 --fm-deviation 100

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --pm-frequency 5 --pm-deviation-deg 90

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --fsk-hop-frequency 2000 --fsk-rate 10

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --bpsk-phase-shift-deg 180 --bpsk-rate 10

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --sum-frequency 10 --sum-amplitude-percent 20
```

Pulse PWM requires both frequency and width deviation. PWM and AM are
mutually exclusive. The following dry-run uses a triangle modulation shape.

```powershell
uv run wavegen-tool configure-pulse `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --pulse-width-s 0.0001 `
  --amplitude-vpp 1 `
  --edge-time-s 0.00000005 `
  --pwm-frequency 5 `
  --pwm-deviation-s 0.00002 `
  --pwm-shape triangle
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
- `--burst-trigger-delay-s`
- `--burst-trigger-slope {positive,negative}`

Immediate uses count and period. Bus uses count and omits period/timer. Timer
uses count and `--burst-trigger-timer-s`. External uses count, may choose the
trigger slope, and omits internal period/timer. Count is waveform cycles, or
bits for PRBS.

Count is 1 through 100,000,000. An Immediate period is 0.000001 through 8000
seconds. Immediate/Timer periods must allow the complete burst plus the Core
safety margin. Counted sine and square carriers are capped at 6 MHz. Counted
Burst is finite-only and mutually exclusive with modulation modes.

`--burst-trigger-delay-s` requires `--burst-count` and the options for the
selected trigger source. It accepts 0 through 1000 seconds in exact 4 ns
increments. Omitting it leaves the instrument's stored delay in place.
Explicitly passing `0` programs zero seconds. This option is available in
dry-run, simulation, and Direct CLI contributor validation. Product Live
and Worker Live reject every explicit value, including zero.

Gated Burst uses:

- `--gated-burst`
- optional `--gate-polarity {normal,inverted}` (default `normal`)

It is mutually exclusive with every Counted Burst option. Both burst modes
leave output off and affect only the selected channel.

Both modes accept `--burst-start-phase-deg`. The range is −360.0° through
+360.0° in 0.1° increments, and the default is 0.0°. The ordinary waveform
`--phase-deg`, where available, must remain 0° when Burst is enabled.
Product Live supports a Burst start phase of 0°. Nonzero values are available
in dry-run, simulation, and Direct CLI contributor validation.

The following are **alternative** Sine previews, not options to combine.
Immediate Counted Burst supplies a period:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-period-s 0.1 --burst-start-phase-deg 90
```

Bus, Timer, and External Counted Burst use their own trigger parameters:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-trigger-source bus --burst-trigger-delay-s 0.1

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-trigger-source timer `
  --burst-trigger-timer-s 0.1

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-trigger-source external `
  --burst-trigger-slope positive
```

Gated Burst is configured independently and needs a separate physical gate
signal for live gated operation:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --gated-burst --gate-polarity normal --burst-start-phase-deg 45.1
```

Bus-triggered Burst requires a later explicit instrument-wide `trigger`
after live configuration and any separately authorized output-on. External
trigger/gate operation requires a compatible external source.

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
`--trigger-source {immediate,bus,timer,external}`, `--trigger-timer-s`,
`--trigger-slope {positive,negative}`, `--trigger-delay-s`, one voltage form,
`--phase-deg`, and `--load`. Hold and return default to zero. The trigger source defaults to
Immediate. A Timer interval must be 0.000001 through 8000 seconds and at least
the complete sweep, hold, and return duration. Only External accepts
`--trigger-slope`, which defaults to `positive` and is always programmed.
Omit `--trigger-timer-s` with External. External Sweep is available in dry-run,
simulation, and contributor validation.

`--trigger-delay-s` uses the same range and access rules as Counted Burst
delay. It applies to linear and logarithmic sweeps with any otherwise
supported trigger source. List Sweeps have no delay option.

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

Other representative sweep previews include a linear square sweep, a
Bus-triggered ramp sweep, and a Timer-triggered triangle sweep:

```powershell
uv run wavegen-tool configure-square-sweep `
  --dry-run --model keysight-33521b `
  --start-frequency-hz 1000 --stop-frequency-hz 2000 `
  --spacing linear --sweep-time-s 1 `
  --duty-cycle-percent 50 --amplitude-vpp 0.1

uv run wavegen-tool configure-ramp-sweep `
  --dry-run --model keysight-33521b `
  --start-frequency-hz 100 --stop-frequency-hz 1000 `
  --spacing linear --sweep-time-s 1 `
  --symmetry-percent 50 --amplitude-vpp 0.1 `
  --trigger-source bus

uv run wavegen-tool configure-triangle-sweep `
  --dry-run --model keysight-33521b `
  --start-frequency-hz 100 --stop-frequency-hz 1000 `
  --spacing linear --sweep-time-s 1 `
  --amplitude-vpp 0.1 `
  --trigger-source timer --trigger-timer-s 2 --trigger-delay-s 0.1
```

Bus-triggered sweeps require a separate `trigger` command after live
configuration and explicit output-on. Timer sweep timing must accommodate
sweep, hold, and return time.

## Arbitrary Waveforms and Sequences

Arbitrary commands are available in dry-run and simulator modes for 33512B
Channels 1 and 2 and 33521B Channel 1. See
[Supported Models](../core/supported-models.md) for the Product Live scope.

Create a UTF-8 CSV of at least eight samples with one numeric value per row
and no header. Values must be finite and within `[-1, 1]`. Use a name that
starts with an ASCII letter and contains at most 12 letters, digits, or
underscores. A CSV may contain at most 16,000,000 samples. Upload with the
selected output off, then configure playback using the loaded name.

```console
wavegen-tool upload-arbitrary --name WAVE_A --file wave-a.csv --dry-run
wavegen-tool list-arbitrary-memory --simulate
wavegen-tool configure-arbitrary --name WAVE_A --sample-rate-hz 1000000 --amplitude-vpp 1 --dry-run
```

Upload only stores the segment. Configuration selects it and leaves the
output off. Arb playback uses Sa/s and the instrument's current filter
settings. Core checks the instrument's free points and accounts for 128-point
allocation. A duplicate name is rejected. To remove **all** volatile segments
and sequences on the selected channel, use
`wavegen-tool clear-arbitrary-memory --simulate --confirm-clear`. Clear turns
off the selected output and reloads the default Arbitrary waveform.
For a loaded file shown as a path in the catalog, use its base name without
the extension in playback or Sequence steps. Matching is case-insensitive,
and an ambiguous base name is rejected.

To select a Keysight built-in waveform without a CSV, add `--load-builtin`:

```console
wavegen-tool configure-arbitrary --name SINC --load-builtin --sample-rate-hz 1000000 --amplitude-vpp 1 --simulate
```

Built-in names are case-insensitive: `CARDIAC`, `EXP_RISE`, `EXP_FALL`,
`GAUSSIAN`, `HAVERSINE`, `LORENTZ`, `D_LORENTZ`, `NEG_RAMP`, and `SINC`.
You can also quote `Exponential Rise`, `Exponential Fall`, `D-Lorentz`, or
`Negative Ramp`. Core loads a missing waveform into the selected channel or
reuses the verified built-in catalog entry. A same-name entry with an
unconfirmed origin causes an error. Configuration leaves the selected output
off and preserves existing memory. A dry-run shows the load-if-absent path.

Use `configure-arbitrary-sequence --file sequence.json` with a version 1
document. For example, upload each named segment before applying this
configuration.

```json
{
  "version": 1,
  "name": "TEST_SEQ",
  "sample_rate_hz": 1000000,
  "amplitude_vpp": 1,
  "offset_v": 0,
  "load": "50",
  "trigger_source": "bus",
  "steps": [
    {"waveform": "WAVE_A", "play_control": "repeat", "repeat_count": 3},
    {"waveform": "WAVE_B", "play_control": "onceWaitTrig"},
    {"waveform": "WAVE_C", "play_control": "repeatTilTrig"}
  ]
}
```

A sequence accepts 1–512 steps. Each channel has space for 512 steps across
its stored sequences. Use `once` for one playback, `repeat` with
`repeat_count` from 1 to 1,000,000, `onceWaitTrig` to wait after playing, or
`repeatTilTrig` to repeat until triggered. The marker is fixed to
`lowAtStart`. For external triggering, set `trigger_source` to `"external"`
and optionally set `trigger_slope` to `"negative"` or `"positive"`
(default). With Bus, an explicit `wavegen-tool trigger` sends the
instrument-wide trigger. Turn the output on only through the separate
`output` command.

Use `--dry-run` to validate local input and inspect the plan without VISA.
Memory availability and loaded references are checked when executing against a
device or simulator. For several simulated operations sharing a volatile
catalog, use the Worker in simulate mode because each Direct CLI
`--simulate` invocation starts a fresh simulator. In Worker requests, pass
CSV as an absolute Worker-host path in `arguments.path` and submit the
Sequence object in `arguments.document`.

## Software Sequences

Use `run-software-sequence --file` to run an ordered set of operations on one
instrument. Save a version 1 document such as:

```json
{
  "version": 1,
  "loop_count": 2,
  "steps": [
    {"action": "configure-sine", "arguments": {"channel": 1, "frequency_hz": 1000, "amplitude_vpp": 0.1}},
    {"action": "output", "arguments": {"channel": 1, "enabled": true}},
    {"action": "wait", "arguments": {"seconds": 1}},
    {"action": "output", "arguments": {"channel": 1, "enabled": false}}
  ]
}
```

```powershell
wavegen-tool run-software-sequence --file sequence.json --dry-run --model keysight-33512b --json
wavegen-tool run-software-sequence --file sequence.json --simulate --model keysight-33512b --json
```

Dry-run shows the reviewable step order and total scheduled wait once, even
when the document repeats. Simulation shares waveform memory across steps.
The [Software Sequence contract](../contracts/wavegen-worker-contract.md#software-sequences)
defines the allowed actions and their arguments. For a CSV upload, use
`arguments.path` with an absolute path or a path relative to the current
working directory. `configure-arbitrary-sequence` takes the existing native
sequence document inside the step's `arguments.document`.

Live execution uses `--resource` or the operator's
`WAVEGEN_TOOL_RESOURCE`. Core preflights the entire sequence against detected
identity and live policy and requires controlled outputs to be OFF at entry.
Press Ctrl+C to interrupt at a controllable step or wait. The sequence
attempts to turn off and confirm every output it tried to enable, including
after failure or interruption. A communication loss or a forcibly terminated
process can leave the output state unknown. Configuration and uploaded
waveforms can persist after a failure.

## Basic Workflows

The following examples plan each workflow without opening VISA. Use
`--simulate` in place of `--dry-run` to execute against a simulator. Each
command accepts `--channel` (default 1) and `--json`. Execution requires the
selected channel to be OFF at entry and confirms OFF after execution. Inspect
`cleanup` on success or failure before relying on the final output state.

| Command | Example arguments |
| --- | --- |
| `run-timed-output` | `--file waveform.json --duration-s 2 --dry-run` |
| `run-amplitude-ramp` | `--start-vpp 0.1 --stop-vpp 0.25 --step-vpp 0.1 --dwell-s 1 --dry-run` |
| `run-output-cycle` | `--on-s 1 --off-s 1 --cycles 2 --dry-run` |
| `run-waveform-sequence` | `--file waveforms.json --dry-run` |
| `run-frequency-step` | `--start-frequency-hz 1000 --stop-frequency-hz 3000 --frequency-step-hz 1000 --dwell-s 1 --dry-run` |
| `apply-quick-output-preset` | `--preset square --dry-run` |

For Timed Output, `waveform.json` contains one static waveform configuration
such as `{"action":"configure-sine","arguments":{"frequency_hz":1000,"amplitude_vpp":0.1}}`.
For Waveform Sequence, `waveforms.json` is an array with up to 62 entries.
Each entry contains that configuration under `waveform` and its own positive
`duration_s`. The tool turns output OFF between waveform configurations.
Output Cycle uses the channel's current waveform and waits through each ON
and OFF interval, including the last OFF interval.

Amplitude Ramp configures a sine at `--frequency-hz` (default 1000 Hz),
zero offset, and a 50-ohm load setting. Enter a positive step magnitude.
The ramp automatically selects the direction and includes both endpoints,
using a shorter final step when necessary. All points, including the first
and last, get a dwell. The 124-point limit follows from 250 Sequence steps:
three for configure, ON and OFF, one wait per point, and one amplitude
update per later point. Total scheduled dwell is at most 600 seconds, plus
instrument communication time. Output stays enabled between amplitude
updates. The instrument retains its current autorange setting and may
produce transients when its hardware changes range. The ramp is available
in dry-run, simulation, and the explicit Direct CLI Contributor route.

Frequency Step configures the sine at the first frequency with output OFF,
then reads back the starting frequency and Immediate List Sweep settings.
It enables output before starting a counted native list for one pass. The
instrument advances each point using the configured dwell. The workflow
allows the final dwell to finish, switches output OFF, and returns to CW.
The documented scheduled duration covers the list itself. Enabling and
disabling output also take instrument communication time.

The generated list includes the exact stop frequency and uses the existing
128-point and dwell limits. `--amplitude-vpp`, `--offset-v`, and `--load`
select the sine voltage settings.

Quick Output Preset supports `sine`, `square`, and `triangle`, each
configured at 1 kHz, 0.1 Vpp, zero offset, and high impedance. The square
uses 50% duty. All three keep output OFF.


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

For a Bus-triggered List Sweep, omit `--dwell-s`. Each later instrument-wide
`trigger` advances the configured list; configuring the list does not send it:

```powershell
uv run wavegen-tool configure-sine-list-sweep `
  --dry-run --model keysight-33521b `
  --frequencies-hz "1000,3000,7000" `
  --trigger-source bus --amplitude-vpp 0.1
```

Square List Sweep adds duty cycle; Ramp List Sweep adds symmetry. Pulse, PRBS,
arbitrary, External, Timer, and Manual-triggered lists, per-point dwell, list
files, stored lists, and custom List Sweep markers are outside the Direct CLI surface.

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
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --channel 1 `
  --state off
```

`--state {on,off}` is required. Output-on is never implicit; review the
configured signal and physical load before explicitly selecting `on`.

### `configure-sync` and `sync-output`

The front-panel Sync connector has one instrument-wide ON/OFF state. Both
commands leave the main CH1/CH2 output states alone.

```powershell
uv run wavegen-tool configure-sync --dry-run --model keysight-33512b --source-channel 2 --mode carrier --polarity inverted
uv run wavegen-tool sync-output --simulate --state on --json
```

`configure-sync` requires `--source-channel 1|2`, `--mode normal|carrier`,
and `--polarity normal|inverted`. Use CH1 on a single-channel model.
Configuration verifies Sync OFF before applying the settings and again during
readback. `sync-output --state on|off` sets and verifies Sync independently
of its configuration. Neither command takes `--channel`.

Both commands support `--simulate` and `--json`. Only configuration supports
`--dry-run`. Each Direct CLI simulation starts with fresh state. Use a
simulated Worker to retain Sync settings across commands. Instrument readback
reports connector state and settings. Carrier timing depends on the active
waveform and operating mode.

Product Live supports explicit Sync OFF. For live Sync configuration or Sync ON,
use the Direct CLI [contributor validation workflow](../CONTRIBUTING.md#real-live).

### `trigger`

Send one instrument-wide IEEE-488.2 `*TRG` without waiting:

```powershell
uv run wavegen-tool trigger --resource "$env:WAVEGEN_TOOL_RESOURCE"
```

There is no channel option. Every armed channel using Bus trigger may respond.
The command does not change output state. In simulation only, `--model` selects
the registered profile.

### Explicit Live Output Sequence

Only run the following with an **operator-selected** admitted VISA resource,
verified wiring and load, and a safe intended waveform. These commands are
separate invocations; there is no automatic output-on or guaranteed output-off
if the shell is interrupted:

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "<your exact VISA resource>"
uv run wavegen-tool identify --json
uv run wavegen-tool configure-sine --frequency-hz 1000 --amplitude-vpp 0.1
uv run wavegen-tool output --state on
uv run wavegen-tool status --channel 1 --json
uv run wavegen-tool output --state off
```

Configuration leaves the output OFF. `output --state on` is a separate,
deliberate action; always issue `output --state off` when finished. For a Bus
List Sweep or Bus Burst/Sweep, `trigger` is also a separate action and may
affect **every armed channel**, not only Channel 1.

## Dual-channel operations

These operations are supported in dry-run and simulation for 33510B/33512B;
`phase-sync` is also supported for 33521B. Contributor validation requires the
existing explicit resource, expected model and contributor authorization.
They do not extend Product Live or Worker Live support. A simulator process
starts with fresh state; use a software sequence or persistent Worker for
dependent operations.

| Command | Arguments |
| --- | --- |
| `phase-sync` | No channel argument; resets the shared phase reference, preserves configured phase values |
| `output` | `--channel all --state on\|off` |
| `configure-frequency-coupling` | `--state on --mode ratio --ratio 2`, or `--mode offset --offset-hz 500`; optional `--reference-channel 1\|2`; `--state off` alone disables |
| `update-coupled-frequency` | `--channel 1\|2 --frequency-hz 2000` |
| `configure-voltage-coupling` | `--state on\|off` |
| `update-coupled-voltage` | `--channel 1\|2 --amplitude-vpp 0.2 --offset-v 0.05` |
| `configure-tracking` | `--mode on\|off\|invert`; optional `--reference-channel 1\|2` |
| `configure-tracked-waveform` | `--waveform sine --arguments-json '{"channel":1,"frequency_hz":2000,"amplitude_vpp":0.2}'`; static waveform arguments |
| `configure-combine` | `--channel 1 --source ch2\|none` or `--channel 2 --source ch1\|none` |
| `dual-channel-status` | No channel argument; both channel states and coupling, tracking and Combine settings |

Set both waveforms and their relative `--phase-deg` before `phase-sync`.
The existing `trigger` sends one instrument-wide Bus trigger. Group ON is
explicit and sequential at the command level; it does not guarantee simultaneous
hardware output edges. Configuration and coupled updates require both main
outputs confirmed OFF. Group OFF always attempts both outputs and verifies
both; a partial ON failure attempts both OFF. Conflicting settings are
rejected rather than implicitly released. Joint configuration readback checks
the requested topology change and preserves other coupling, tracking and
Combine states, including the active frequency coupling relation when it is
not being changed. Non-release configuration also checks any active voltage
coupling relation. Explicit release remains available from a conflicting
topology. Native tracking copies reference
channel settings (including frequency list settings and loaded Arbitrary
memory) and may change the Sync source. To avoid unsupported memory changes,
the tool rejects active Arbitrary or List waveform settings and permits only an empty catalog or the default built-in EXP_RISE entry
on both channels, when Arbitrary is supported. No memory is cleared automatically. Inverted tracking
preserves the DC offset. Coupled frequency changes require CW frequency mode
and valid resulting modulation, duty cycle, pulse and Burst timing. Voltage coupling maintains
amplitude, offset, range, load and units. Mixed DC/non-DC voltage coupling
cannot verify equal amplitudes; coupled amplitude/offset updates require two
non-DC waveforms. Two DC channels may use voltage coupling for their offset
settings. Combine adds the source signal to
the receiver; the source's DC offset is not added.

For AM, FM, PM, PWM and Sum, use `--am-source`, `--fm-source`,
`--pm-source`, `--pwm-source` or `--sum-source` with `ch1` or `ch2`.
Select the other channel, supply the usual depth/deviation/amplitude,
and omit the internal modulation frequency and shape. Internal modulation
remains the default. Configure the source before the receiver. Both outputs
must be OFF before configuring this route. Other-channel PM checks the source
frequency range and the actual programmed carrier frequency; sources whose
frequency range cannot be confirmed are rejected. Reconfigure the receiving waveform
with both outputs OFF to release the route. FSK and BPSK are unchanged.

```powershell
wavegen-tool configure-frequency-coupling --model keysight-33512b --dry-run --state on --mode ratio --ratio 2 --json
wavegen-tool configure-sine --model keysight-33512b --dry-run --channel 2 --frequency-hz 10000 --amplitude-vpp 0.1 --am-source ch1 --am-depth 40 --json
wavegen-tool output --model keysight-33512b --simulate --channel all --state off --json
```

Sequence and Worker arguments are defined in the
[Worker contract](../contracts/wavegen-worker-contract.md#dual-channel-arguments).
Sequence cleanup turns affected outputs OFF and leaves topology configured.
Release topology explicitly before returning to independent configuration or
output-on writes. Selected-channel OFF retains the original coupling/tracking
guard without the additional Combine/other-channel modulation guard.

## Optional Advanced Mode Readback

Supported Advanced Mode Direct CLI configurations accept `--verify-readback`. When enabled,
the instrument's programmed mode settings are queried and compared in the same VISA
session after configuration, with output remaining OFF. This covers AM/DSSC, FM,
PM, FSK, BPSK, PWM, Sum, Counted/Gated Burst, linear/logarithmic Sweep, and List
Sweep on their already supported carriers; it does not introduce new combinations.

Ordinary static sine, square, ramp, triangle, pulse, or PRBS configuration
without an Advanced Mode option rejects this flag before sending any settings.
DC, noise, and output configuration do not accept this flag.

Hardware-free simulator example:

```powershell
uv run wavegen-tool configure-sine --simulate --frequency-hz 1000 `
  --amplitude-vpp 0.1 --am-frequency 10 --am-depth 80 `
  --verify-readback --json
```

A successful executed verification adds `readback_verified: true` to Direct CLI
JSON and `Advanced Mode readback: PASS` to text output. A mismatch, malformed
response, or failed query returns a waveform verification error (exit code 29),
with expected and actual values where available. Verification queries Trigger
Delay only when this configuration explicitly sets it.

An explicit delay also checks the instrument error queue before and after
configuration, even without `--verify-readback`. Each check reads at most one
entry. If the first check finds a queued error, the command reports it and
stops before writing any settings. The second check reports any error it reads
after the writes. Use `read-errors` to inspect remaining entries.

The `--simulate` path checks an in-memory model only. The `--dry-run` path, even
with `--verify-readback`, returns the normal offline plan and does not claim a
readback was performed. Programmed-settings verification does not prove analog
waveform accuracy or actual external trigger/gate behavior.

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

The Worker is a loopback-only local HTTP control plane. Model admission follows
Core: dry-run and simulator requests accept registered 33510B, 33512B, and
33521B planning models, while Product Live admits 33512B and 33521B. The
33510B remains unavailable for Product Live.

Start a simulated Worker:

```powershell
uv run wavegen-tool worker --mode simulate --control-port 8765
```

Worker startup options are:

- `--mode {live,simulate}` (required)
- `--resource` (required for live, forbidden for simulate; Worker does not read `WAVEGEN_TOOL_RESOURCE`)
- `--backend` (default `system`)
- `--control-port` (default `0`; zero selects an available loopback port)
- `--allow-output-writes` (off by default)

Worker startup validates admission but does not open a live resource or
identify an instrument. A `ready` lifecycle event means the loopback HTTP plane
is serving; it does not mean that physical hardware is connected or identified.

See the [Worker contract](../contracts/wavegen-worker-contract.md) for the 31
domain commands and their request fields. Worker command names use kebab-case
and request argument fields use snake_case. Selected-channel results retain
Core's integer `channel`, defaulting to 1. A Bus `trigger` applies instrument-wide.

Live waveform configuration, Sync configuration, and trigger require Worker
startup with `--allow-output-writes`. Simulated Arbitrary clear requires `confirm_clear: true`
in the request. Main output ON and Sync ON require both
`--allow-output-writes` and `confirm_output: true`. Read-only commands and
explicit main output OFF and Sync OFF are available without the startup flag.

The Worker has one active job slot. A second request is rejected as `busy`, or
as `stopping` after a cooperative stop request.

Every `POST /command` supplies a request context. `simulate` and `dry_run`
contexts require an exact registered `planning_model_id` and must omit
`expected_model_id`. Dry-run never binds simulator state. The first admitted
simulate job binds the Worker to its planning model for the process lifetime;
later requests cannot silently switch models.

A `live` context may supply an exact registered `expected_model_id` as an
optional mismatch guard and must omit `planning_model_id`. Detected `*IDN?`
identity and Core Product policy remain authoritative. Omitting the guard does
not default to 33521B. Exact context and argument schemas remain
contract-owned.

An accepted command returns HTTP 202 and runs asynchronously. Malformed or
inadmissible requests return HTTP 400; busy or stopping admission returns HTTP
409. The Worker does not create per-job artifacts.

Worker HTTP requests/responses and machine stdout JSONL use schema version 3.
Events for one process share a `run_id` and include lifecycle events such as
`ready`, `job_accepted`, `job_started`, `job_finished`, `stop_requested`,
`error`, and `summary`.
`job_finished` is the common terminal event for both successful and failed
async jobs. Human diagnostics are not a machine API.

Common `worker_state="running"` with `fatal_error=null` means the control
interface is ready, even while a job is busy; it does not imply an idle command
slot.

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
- `wait-ready` polls until the command slot is idle or a bounded deadline,
  continuing to wait while a job is busy despite Common control readiness.
  It adds `--wait-timeout-ms` (default 30000) and `--poll-ms` (default 200) to
  `--port`, `--timeout-ms`, and `--json`.
- `worker-stop` requests cooperative stop. It does not wait for process exit
  and does not turn output off automatically. It accepts `--port`,
  `--timeout-ms`, and `--json`.

With `--json`, each lifecycle client emits one compact schema-version-2 JSON
object. The original Worker v3 HTTP response is retained under
`worker_response`. An accepted `send-command` result confirms admission, not
job completion; use lifecycle status to observe the result.

For normal live shutdown, submit explicit output-off before stopping. Use the
[Wavegen CLI JSON / JSONL contract](../contracts/wavegen-cli-jsonl-contract.md),
[Worker contract](../contracts/wavegen-worker-contract.md),
[Wavegen orchestrator workflows](../contracts/wavegen-orchestrator-workflows.md),
and [common protocol](../contracts/common-worker-protocol.md) for the exact
schemas, lifecycle meanings, and queue rules.

### Simulated Worker Walkthrough

In one PowerShell terminal, start the local Worker; leave it running:

```powershell
uv run wavegen-tool worker --mode simulate --control-port 8765
```

In another terminal, wait for readiness, submit a simulated configuration,
observe job completion, then request a cooperative stop:

```powershell
uv run wavegen-tool wait-ready --port 8765 --json

uv run wavegen-tool send-command `
  --port 8765 --command configure-sine `
  --context-json '{"mode":"simulate","planning_model_id":"keysight-33521b"}' `
  --arguments-json '{"frequency_hz":1000,"amplitude_vpp":0.1}' --json

uv run wavegen-tool wait-ready --port 8765 --json
uv run wavegen-tool worker-status --port 8765 --json
uv run wavegen-tool worker-stop --port 8765 --json
```

This uses the simulator only, not real hardware. `send-command` returning
HTTP 202 means admission, not completion; the later readiness/status calls
provide lifecycle information. The Worker stop request does not turn a
physical output off: send an explicit `output` command with
`{"enabled":false}` and await its completion before stopping a **Live** Worker.

## CLI Validation Scripts

These contributor scripts are separate from normal Product Live and require
operator authorization before physical VISA. They reuse one Full case inventory.

| Script | Purpose | Physical VISA |
| --- | --- | --- |
| `scripts/preflight-cli.ps1` | Offline capability, CLI dry-run and simulator recipes | Never |
| `scripts/live-cli-check.ps1 -PlanOnly` | Review selected cases, exact intended connection and risks | Never |
| `scripts/live-cli-check.ps1` | Interactive bounded physical validation | Only after separate approval |

### Full validation modes

`-Modes` is valid only with `-Suite full`. A Full run without `-Modes`
selects every applicable family in canonical order. Use a single mode,
a comma-separated list or a PowerShell array; duplicates run once. An invalid
or empty selection, or Smoke combined with Modes, fails closed. Common safety,
identity and final error-queue cases remain mandatory regardless of selection.

| Mode | Coverage |
| --- | --- |
| `static` | Sine, Square, Ramp, Triangle and Pulse with nonzero phase, DC, Noise, all six PRBS patterns and a non-default PRBS edge time; 33521B phase synchronization |
| `modulation` | AM/DSSC, FM, PM, FSK, BPSK, PWM and Sum, with representative carriers and non-sine internal shapes where supported |
| `burst` | Counted and Gated Burst, representative carriers and trigger sources, explicit zero and nonzero trigger delay, Counted 90° and Gated 45.1° start phase |
| `sweep` | Linear and logarithmic Sine, Square, Ramp and Triangle sweeps, representative triggers, explicit zero and nonzero delay, Square duty cycle and Ramp symmetry |
| `list-sweep` | Native frequency List and Bus triggers, CW recovery |
| `output` | Output setup including a 1000 Ω load, controlled ON/OFF, and representative Sync source, mode and polarity settings |
| `arbitrary` | Synthetic CSV upload, memory inspection, playback, confirmed volatile Clear, all supported built-in waveforms and SINC reuse |
| `sequence` | Native Bus and both External trigger edges, representative CH2 Bus sequence on supported models, and once, repeat, onceWaitTrig and repeatTilTrig |
| `software-sequence` | Ordered software-controlled actions, supported arbitrary operations and confirmed output cleanup, including joint output on dual-channel models |
| `dual-channel` | Dual-channel phase, group output and Bus trigger, frequency/voltage coupling, tracking, other-channel modulation and Combine on 33510B/33512B |
| `basic-workflows` | Timed output, both directions of Amplitude Ramp and Frequency Step, Output Cycle, Waveform Sequence, Sine/Square/Triangle Quick Output Presets and simulated Worker cancellation and stop |

The Full Static and Sweep recipes use `--verify-waveform-readback` to compare
the programmed Square, Ramp, and Triangle phase, PRBS pattern and edge time,
Square Sweep duty cycle, and Ramp Sweep symmetry with readback while output
is OFF. Pulse phase uses the readback already built into Pulse configuration.
Preflight checks simulated readback, and PlanOnly shows the intended verification.

`-Suite full -Modes burst` includes both nonzero Burst start phase cases.
These contributor validation cases keep output OFF and verify the programmed
phase through readback. Measuring physical waveform behavior requires a
separately authorized observation. Whenever Burst is selected, Preflight
also checks that an invalid phase is rejected. Use the same mode selection for
Preflight and PlanOnly before an authorized Real Live run.

33512B admits CH1/CH2 Arbitrary; 33521B admits CH1; 33510B has no
Arbitrary capability. All-target Preflight selects applicable recipes per
Core capabilities; explicitly requesting unsupported Arbitrary/Sequence on
one target is refused. A selected subset PASS only proves that subset.
Arbitrary/Sequence physical execution uses the contributor-only Core path,
not ordinary CLI or Worker Product Live. Real Full alone can reset between
independent cases, explicitly enable output for authorized observed trials,
and must independently confirm safe OFF. The selected-channel volatile Clear
erases ALL segments and sequences: PlanOnly identifies it, and Real Live
requires the operator to type `CLEAR` separately at that exact step.

### Script parameters

| Parameter | Preflight | Live/PlanOnly | Default / limits |
| --- | --- | --- | --- |
| `-Suite` | Optional | Optional | `smoke`; other value `full` |
| `-Modes` | Full only | Full only | All applicable families when omitted; comma-separated or array |
| `-Target` | Optional | Required | Preflight `all`; Live one exact registered model |
| `-Connection` | Not used | Required | `usb` or `tcpip` (LAN), matching the resource prefix |
| `-Resource` | Not used | Required | Explicit operator-chosen VISA resource, including PlanOnly |
| `-Backend` | Not used | Optional | `system`; Full requires System VISA |
| `-PlanOnly` | Not used | Optional switch | No VISA manager, hardware I/O, reset or output |
| `-Python` | Optional | Optional | `.\.venv\Scripts\python.exe`; Full requires project Python |
| `-OutputRoot` | Optional | Optional | `.tmp_tests\cli_preflight` / `.tmp_tests\cli_live` |
| `-DebugArtifacts` | Optional | Optional | Off; additional private diagnostics, sanitized public evidence |

`Full` additionally requires Windows PowerShell Desktop and a clean unchanged
Git HEAD. Normal Smoke does not require a clean Git tree. No script scans or
infers the Resource; redirected stdin cannot approve a Real Live run.
Before Real Full, check the oscilloscope/termination, external trigger source
when relevant, output/grounding limits and manual safe-off contingency.

Examples from the repository root:

```powershell
# Existing representative Sine smoke; no hardware.
.\scripts\preflight-cli.ps1

# Every applicable family on every registered model, hardware-free.
.\scripts\preflight-cli.ps1 -Target all -Suite full

# Focused hardware-free Full preflight.
.\scripts\preflight-cli.ps1 -Target keysight-33512b -Suite full -Modes arbitrary,sequence

# Hardware-free Full plan with explicit intended resource (not opened).
.\scripts\live-cli-check.ps1 -Target keysight-33512b -Connection usb `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" -Backend system `
  -Suite full -Modes arbitrary,sequence -PlanOnly

# Full plan of all applicable modes: omit -Modes.
.\scripts\live-cli-check.ps1 -Target keysight-33521b -Connection usb `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" -Suite full -PlanOnly
```

Remove `-PlanOnly` only during a separately authorized, interactive physical
session. Do not treat Preflight, PlanOnly, simulation, selected-mode Real Live,
or contributor-only Arbitrary cases as automatic Product Live admission.

These scripts are contributor tools, not normal requirements for every pull
request. See [Contributing](../CONTRIBUTING.md) for evidence acceptance and
privacy policy.

`smoke` preserves the representative per-channel Sine sanity check;
**smoke PASS != all implemented features validated**. `full` uses the shared
canonical inventory to validate the currently implemented Direct CLI Live
feature families, with primary CH1 coverage and only necessary CH2 parity.

Full requires a readable Git HEAD and a clean working tree before artifacts
are created, and records `git_head` and `git_clean=true`. Smoke has no clean-tree
requirement. Full preflight also requires Windows PowerShell Desktop on Windows
and the project Python environment.

Full requires Windows PowerShell, the project `.venv/Scripts/python.exe`, and
`-Backend system`. Use `-Connection usb` for an explicit `USB...` resource or
`-Connection tcpip` for an explicit `TCPIP...` (LAN) resource. The identity
detected during Real Live must exactly match `-Target`. Add `-Suite full` to
each command below for Full admission. Preflight Full, PlanOnly Full, and
Real Full must all pass, with all required cases PASS, successful cleanup,
outputs confirmed safe OFF, a clean final error queue, and successfully
generated shareable evidence. For currently admitted Product Live families this is eligible evidence for a
separate model/revision review, not automatic promotion. Contributor-only
Arbitrary/Sequence results do not open Product Live; 33510B remains validation-pending.
See [the full inventory and acceptance rules](../CONTRIBUTING.md#real-live).

### Hardware-free Preflight

```powershell
.\scripts\preflight-cli.ps1 -Target all
```

`-Suite` accepts `smoke` (default) or `full`. `-Target` accepts `all` or one registered model ID. `-Python` and `-OutputRoot`
are optional: Python defaults to `.\.venv\Scripts\python.exe` and the output
root defaults to `.tmp_tests\cli_preflight`. Preflight runs capability,
dry-run, and simulator checks without a resource or hardware I/O.

### PlanOnly

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Backend system `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
  -PlanOnly
```

PlanOnly requires the exact intended resource but does not open VISA, touch
hardware, or send live SCPI. Console and shareable output redact the resource;
`-DebugArtifacts` retains it in local private evidence.

The live runner parameters are:

| Parameter | Behavior |
| --- | --- |
| `-Suite` | `smoke` (default) or `full` |
| `-Modes` | Optional for Full only; single/comma-separated modes, default all applicable |
| `-Target` | Required exact model ID; `all` is rejected |
| `-Connection` | Required `usb` or `tcpip`, matching the resource prefix |
| `-Resource` | Required exact operator-supplied resource |
| `-Backend` | Optional; defaults to `system` |
| `-PlanOnly` | Optional hardware-free planning switch |
| `-DebugArtifacts` | Optional detailed private and sanitized command artifacts, also passed to Preflight |
| `-Python` | Optional; defaults to `.\.venv\Scripts\python.exe` |
| `-OutputRoot` | Optional; defaults to `.tmp_tests\cli_live` |

### Real Live

Remove `-PlanOnly` only when explicit hardware validation is required. Before
any I/O, the runner prints the exact target, connection, backend, resource,
channels, cases, representative waveform, and safety plan. Redirected input is
rejected and Enter starts the run. Ctrl+C cancels. The runner does not scan, guess, or
auto-select a resource. Smoke keeps output off. Full uses output only for
output control and confirmed observation cases. Real Full requires a scope
and a separate Ext Trig/Gate source. An unconfirmed observation fails Full.

Both scripts normally retain `shareable/report.json` and
`shareable/summary.md`. Use `-DebugArtifacts` for detailed diagnostics.
Keep any `private/` artifacts local. See [Contributing](../CONTRIBUTING.md)
for artifact handling, evidence acceptance, and Product Live decisions.

### Full and Debug Examples

The following **hardware-free** Full checks require a clean Git working tree
and the documented Windows PowerShell/project-Python reference environment:

```powershell
.\scripts\preflight-cli.ps1 -Target all -Suite full -DebugArtifacts

.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Backend system `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
  -Suite full `
  -PlanOnly
```

`-DebugArtifacts` is optional and can also be used on the live runner to
retain detailed private diagnostics. PlanOnly requires an intended resource
but does not open it.

**Real Live Full is an opt-in hardware operation**, not a normal documentation
or CI check. Only after preparing the physical instrument, oscilloscope,
separate external Trigger/Gate source, clean Git tree, and operator confirmation,
the same runner can be invoked without `-PlanOnly`:

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Backend system `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
  -Suite full
```

Unlike normal CLI/Worker control, explicitly initiated Real Live Full performs
a bounded `*RST` before **each independent case**, after confirming the main
outputs and Sync are OFF. It waits for `*OPC?`, turns Sync OFF again, verifies
safe-off state and checks the bounded error queue. **Reset may briefly enable
the Sync BNC. Check its wiring and disconnect sensitive equipment before
confirming Real Full.** An unsafe or failed reset blocks subsequent cases.
Real Full can explicitly turn main output on for permitted observation cases,
which require a scope observation and safe-off cleanup.
The dedicated Sync control case checks Sync ON then OFF by instrument readback.
Only Real Live Full performs this reset.
