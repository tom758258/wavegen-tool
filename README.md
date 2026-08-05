# Wavegen Tool

Wavegen Tool is a safety-focused Python toolkit for identifying explicitly
selected waveform generators and performing bounded control through VISA. The
current milestone supports identification, read-only Channel 1 status, bounded
instrument error-queue reads, and basic Channel 1
sine/square/ramp/triangle/pulse/DC/noise/PRBS/output control and sine, square,
ramp, and triangle frequency sweep configuration for the Keysight or Agilent
33521B.

## Current Scope

- Exact manufacturer-and-model recognition for Keysight Technologies 33521B
  and Agilent Technologies 33521B
- Parameter-validated Channel 1 sine configuration with explicit load, frequency,
  amplitude, and offset
- Hardware-free Channel 1 dry-run preview for all eight waveform configurations
- Stateful, hardware-free Keysight 33521B Channel 1 simulator for all public CLI
  commands
- Parameter-validated Channel 1 square configuration
- Parameter-validated Channel 1 ramp configuration
- Parameter-validated Channel 1 triangle configuration
- Parameter-validated Channel 1 pulse configuration
- Parameter-validated Channel 1 sine linear and logarithmic frequency sweep
  configuration with Immediate trigger
- Hardware-unvalidated, parameter-validated Channel 1 square, ramp, and triangle
  linear and logarithmic frequency sweep configuration with Immediate trigger
- Phase offset control for sine, square, ramp, triangle, and pulse in degrees
- Parameter-validated Channel 1 DC voltage configuration
- Parameter-validated Channel 1 noise configuration
- Parameter-validated Channel 1 PRBS configuration
- Explicit Channel 1 output on/off control
- Read-only Channel 1 status query
- Bounded instrument error queue reads
- System VISA explicitly selects the IVI VISA backend and accepts explicit USB
  and TCPIP/LAN resources
- The `@py` backend from `pyvisa-py` accepts explicit TCPIP/LAN resources only
- Raw VISA resource listing and opt-in live-only connectivity checks
- Human-readable and JSON CLI output
- Hardware-free tests with injectable fake VISA sessions

The identify command rejects GPIB and all other transports outside its explicit
scope. Other Trueform models are not treated as supported.

These are the only recognized manufacturer/model pairs. Matching ignores case
and ordinary whitespace differences but does not use prefix, substring, or
fuzzy matching. A successful identify result preserves the manufacturer
reported by the instrument while normalizing the model to `33521B`. The Local
records document live control/readback path validation against an Agilent
Technologies 33521B through System VISA over USB for exact identification,
read-only Channel 1 status, bounded public error-queue reads, explicit output
on/off state control, sine, square, ramp, triangle, Pulse, DC, noise, and PRBS
configuration, phase writes with bounded direct `SOURce1:PHASe?` readback,
High/Low input canonicalization and its corresponding control path, and
linear/logarithmic sine sweep configuration with Immediate trigger and recorded
sweep-to-CW recovery. These paths cover the tested commands, applicable
readback, error-queue checks, and output-off safety; they do not claim
connector-level amplitude, frequency, phase, timing, jitter, distortion,
bandwidth, calibration, or waveform quality.

Raw resource listing and live-only connectivity checks remain documented
capabilities. The Local records reviewed here do not establish a separate
USB/ASRL discovery hardware-validation claim. The newly added
`configure-square-sweep`, `configure-ramp-sweep`, and
`configure-triangle-sweep` commands remain hardware-unvalidated; their Core,
CLI, Worker, dry-run, simulator, and automated-test behavior is not physical-
instrument validation.

Other waveform types, automatic resource scanning, WebUI features, and release
executables are not implemented. Identification sends only `*IDN?`; it does
not reset the instrument, change settings, or enable an output.

## Requirements and Installation

Python 3.10 or newer and [uv](https://docs.astral.sh/uv/) are required for the
documented development workflow.

```powershell
uv sync --all-extras --locked --link-mode=copy
```

The repository contains one `wavegen-tool` distribution with Core, CLI, and
reserved WebUI import packages.

## Stateful Simulator

The simulator supports `list-resources`, `identify`, `status`, `read-errors`,
all eight waveform configuration commands, the sine, square, ramp, and triangle
sweep commands, and explicit
output control for one
simulated Keysight 33521B Channel 1. It uses the fixed resource
`USB0::SIM::33521B::INSTR` and never creates a real VISA ResourceManager, runs
real resource discovery, opens hardware, or performs hardware I/O. Supported
writes update in-memory state, subsequent queries read that state, and
unsupported resources or SCPI fail closed.

Use `--simulate` without `--resource`:

```powershell
uv run wavegen-tool list-resources `
  --simulate `
  --json

uv run wavegen-tool identify `
  --simulate `
  --json

uv run wavegen-tool configure-square `
  --simulate `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --duty-cycle-percent 50 `
  --load 50 `
  --json

uv run wavegen-tool status `
  --simulate `
  --json

uv run wavegen-tool output `
  --simulate `
  --state on `
  --json
```

Each standalone CLI invocation creates a fresh simulator environment, so the
separate commands above do not share state. A caller performing multiple Core
operations in one Python process can preserve state by reusing the same
`Simulated33521BState` through simulated ResourceManager factories. Closing a
simulated session or manager does not clear that shared state. A simulate
Worker instead shares one simulator state for its process lifetime; neither
mode provides cross-process persistence.

Simulator mode is distinct from dry-run: dry-run only previews validated SCPI,
while the simulator executes supported SCPI against in-memory state. Neither
mode is hardware validation. Live commands still require an explicit real VISA
resource. `output --simulate --state on` enables only the simulated Channel 1
state and never a physical output.

## Worker and Lifecycle Clients

The Worker control plane is loopback-only. A simulate Worker shares simulator
state across commands in the same process, while each standalone `--simulate`
CLI invocation starts with fresh state. A live Worker `ready` event means only
that its local HTTP control plane is accepting requests; it does not mean that
the physical instrument has been connected or identified.

Start a simulate Worker on a fixed example port:

```powershell
uv run wavegen-tool worker `
  --mode simulate `
  --control-port 8765
```

In another terminal, wait for the control plane:

```powershell
uv run wavegen-tool wait-ready `
  --port 8765 `
  --json
```

Submit a representative read-only Worker command:

```powershell
uv run wavegen-tool send-command `
  --port 8765 `
  --command status `
  --context-json '{"mode":"simulate","planning_model_id":"keysight-33521b"}' `
  --json
```

Query lifecycle status:

```powershell
uv run wavegen-tool worker-status `
  --port 8765 `
  --json
```

Request cooperative stop:

```powershell
uv run wavegen-tool worker-stop `
  --port 8765 `
  --json
```

For a live Worker, provide an explicit resource:

```powershell
uv run wavegen-tool worker `
  --mode live `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --control-port 8765
```

For normal live shutdown, first submit `output` with `enabled=false`, confirm
that the job succeeded, and then run `worker-stop`. `worker-stop` never turns
the instrument output off automatically.

## List VISA Resources

Raw listing accesses the selected VISA backend, calls
`ResourceManager.list_resources()` once, preserves every returned resource
string and its order, and closes the ResourceManager. It does not open an
instrument session or send SCPI. Results may include stale resources and
transports or backend combinations that the identify command does not support.

For normal use, omit `--backend`. Wavegen Tool uses the system VISA runtime,
which selects `@ivi`, by default:

```powershell
uv run wavegen-tool list-resources
```

Use `--live-only` to open each candidate allowed by the current
backend/transport verification scope, apply a fixed one-second timeout, and
send one `*IDN?` query. Only resources returning a non-empty response are
shown. When the response is a standard four-field identity, the output shows
its manufacturer, model, and resource. A non-standard but non-empty response
is shown as `Unknown instrument`:

```powershell
uv run wavegen-tool list-resources --live-only
uv run wavegen-tool list-resources --live-only --json
```

System live verification accepts USB, TCPIP/LAN, and ASRL/RS-232 candidates.
`@py` live verification accepts TCPIP/LAN candidates only. GPIB, PXI, VXI,
unknown transports, and unsupported backend/transport combinations remain
visible in raw results but are skipped in live-only mode.

Provide serial settings explicitly when an ASRL candidate requires them. These
settings apply only to system-backend ASRL candidates; omitted settings leave
the VISA session defaults unchanged:

```powershell
uv run wavegen-tool list-resources `
  --live-only `
  --serial-baud-rate 9600 `
  --serial-read-termination LF `
  --serial-write-termination LF
```

Termination values are `CR`, `LF`, `CRLF`, and `NONE`; `NONE` means Python
`None`. Live ASRL discovery does not add ASRL support to identify. Identify
continues to accept only system USB, system TCPIP/LAN, and `@py` TCPIP/LAN.

For advanced LAN/TCPIP diagnostics, select `pyvisa-py` explicitly:

```powershell
uv run wavegen-tool list-resources --live-only --backend "@py"
```

A non-empty response confirms connectivity only. It does not establish that
the resource is a recognized 33521B or authorize the model for later control.
Live-only output does not display or save serial numbers, firmware, or raw IDN
responses. Checks do not retry or switch backends. They do not send cleanup,
remote/local, reset, or any command other than the single `*IDN?`.

Copy the selected USB or TCPIP resource into a PowerShell environment variable
for the current session:

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "USB0::...::INSTR"
```

This variable is only a documentation convenience; it is not a hidden CLI
default. The identify command still receives the selected resource explicitly:

```powershell
uv run wavegen-tool identify `
  --resource "$env:WAVEGEN_TOOL_RESOURCE"
```

Neither listing nor identification falls back to another backend.

## Identify an Instrument

The resource is always required and must be supplied explicitly. System VISA is
the default and explicitly selects the IVI VISA backend.

System VISA with USB:

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "USB0::...::INSTR"

uv run wavegen-tool identify `
  --resource "$env:WAVEGEN_TOOL_RESOURCE"
```

System VISA with TCPIP/LAN:

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "TCPIP0::...::INSTR"

uv run wavegen-tool identify `
  --resource "$env:WAVEGEN_TOOL_RESOURCE"
```

Select `pyvisa-py` explicitly for TCPIP/LAN:

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "TCPIP0::...::INSTR"

uv run wavegen-tool identify `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --backend "@py"
```

Request one JSON object:

```powershell
uv run wavegen-tool identify `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --backend "@py" `
  --json
```

JSON identify outcomes use `model_supported` to report recognized-model
resolution. JSON output applies to command outcomes after successful argument
parsing; argument usage errors retain argparse's standard format.

The placeholder resource values above are fictional. Raw IDN data remains local
to the running process and is not written to files or artifacts.

## Read Channel 1 Status

Read the current Channel 1 status without changing the instrument:

```powershell
uv run wavegen-tool status `
  --resource "$env:WAVEGEN_TOOL_RESOURCE"
```

Add `--json` to request one JSON object:

```powershell
uv run wavegen-tool status `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --json
```

Status reports the Channel 1 output state, function, offset, and instrument
output-load setting. Frequency, amplitude, and amplitude unit are reported only
when they are valid for the active function. DC does not report stale retained
frequency or amplitude values. Noise reports its bandwidth rather than treating
the ordinary frequency query as bandwidth. In JSON output, fields that do not
apply to the active function are `null`. Status does not modify settings or turn
the output on or off. The output-load setting does not detect or verify the
physically connected load.

## Read the Instrument Error Queue

Read and drain the selected instrument's system error queue:

```powershell
uv run wavegen-tool read-errors `
  --resource "$env:WAVEGEN_TOOL_RESOURCE"
```

Use `--json` for automation:

```powershell
uv run wavegen-tool read-errors `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --json
```

The default `--max-reads` is 20, and the allowed range is 1 through 100. The
command first validates the selected instrument with `*IDN?`, then drains
`SYSTem:ERRor?` through the same session. Every returned queue entry is removed
from the instrument. It does not send `*CLS`, `*RST`, or waveform/output writes.
Successfully reading instrument errors still returns exit code 0. Automation
should inspect `has_errors`; `limit_reached=true` means the command did not
confirm that the queue was empty.

## Configure a Channel 1 Sine Wave

Configure a 1 kHz, 0.1 Vpp sine wave with zero offset and set the instrument's
output-load setting to 50 ohms:

```powershell
uv run wavegen-tool configure-sine `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --load 50
```

Use `--load high-z` to set the instrument's output-load setting to high
impedance and `--json` for one JSON object. The load setting does not detect or
verify the physically connected load. The command validates all waveform
parameters before opening VISA. It then identifies the instrument in the same
session, rejects anything except an exactly recognized 33521B, turns Channel 1
output off, explicitly sets the angle unit to degrees, and applies the settings.
It never turns the output on. Sine, square, ramp, triangle, and pulse accept
`--phase-deg`, defaulting to 0 degrees and allowing -360 through +360 degrees.
The phase write and bounded direct `SOURce1:PHASe?` readback path was validated
as a live control/readback path for the documented sine, square, ramp,
triangle, and pulse cases; public `status` does not expose phase.

Sine, square, ramp, triangle, pulse, noise, and PRBS configuration accept either
`--amplitude-vpp` with optional `--offset-v`, or a complete
`--high-level-v`/`--low-level-v` pair. These forms cannot be mixed, and the
High/Low pair must have a high level greater than its low level. For example,
`--high-level-v 3.3 --low-level-v 0` is canonicalized to `3.3 Vpp` with a
`1.65 V` offset. Results and dry-run previews continue to report and command
the canonical amplitude and offset values.

The supported 33521B sine limits are:

- Frequency: 0.000001 Hz to 30000000 Hz
- 50-ohm load setting: 0.001 Vpp to 10 Vpp, with
  `abs(offset) + amplitude / 2 <= 5 V`
- High-impedance load setting: 0.002 Vpp to 20 Vpp, with
  `abs(offset) + amplitude / 2 <= 10 V`

Preview the validated sine SCPI plan without a VISA resource or instrument:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --phase-deg 45 `
  --load 50
```

Dry-run supports sine, square, ramp, triangle, pulse, DC, noise, PRBS, and
square/ramp/triangle frequency sweep configuration.
Each dry-run uses the same Core parameter normalization, waveform-specific
validation, and SCPI command planning as its live command, but does not create
a ResourceManager, open a session, query, or write. The command list is only a
preview and is not executed. The sine preview includes the explicit
`UNIT:ANGLe DEGree` and `SOURce1:PHASe 45` commands. Dry-run is neither a
simulator nor hardware validation. Live waveform configuration continues to
require an explicit VISA resource, and configuration leaves output off.

## Configure a Channel 1 Sine Frequency Sweep

Sine sweeps support linear or logarithmic spacing, separate start and stop
frequencies, sweep time, hold time, and return time. The documented sine sweep
command path was validated as a live control/readback path on the tested setup.
This Part uses the Immediate trigger source only and leaves output off:

```powershell
uv run wavegen-tool configure-sine-sweep `
  --dry-run `
  --start-frequency-hz 1000 `
  --stop-frequency-hz 10000 `
  --spacing logarithmic `
  --sweep-time-s 2 `
  --hold-time-s 0 `
  --return-time-s 0 `
  --amplitude-vpp 0.1 `
  --phase-deg 0 `
  --load 50
```

The dry-run previews the start/stop, spacing, sweep/hold/return time, Immediate
trigger, and sweep-mode SCPI commands without VISA I/O. The recorded live path
also covered sweep-to-CW recovery.
Normal sine, square, ramp, triangle, and pulse configuration explicitly restores
CW frequency mode after a sweep while leaving output off.

## Configure Channel 1 Square, Ramp, and Triangle Frequency Sweeps

The `configure-square-sweep`, `configure-ramp-sweep`, and
`configure-triangle-sweep` commands support linear or logarithmic spacing,
separate start and stop frequencies, sweep time, hold time, and return time.
They use the Immediate trigger source only, leave Channel 1 output off, and
remain hardware-unvalidated. Core validation, dry-run, simulator, CLI, Worker,
and automated tests do not constitute physical-instrument validation.

Square sweeps accept `--duty-cycle-percent`; its frequency-dependent limit is
validated at the higher endpoint of the complete sweep. Ramp sweeps accept
`--symmetry-percent` from 0% through 100%. Triangle sweeps have no additional
waveform-specific input. The carrier frequency command is set to the requested
start frequency, including for downward sweeps.

Preview one representative square sweep without VISA I/O:

```powershell
uv run wavegen-tool configure-square-sweep `
  --dry-run `
  --model keysight-33521b `
  --start-frequency-hz 1000 `
  --stop-frequency-hz 30000 `
  --spacing logarithmic `
  --sweep-time-s 2 `
  --hold-time-s 0 `
  --return-time-s 0 `
  --amplitude-vpp 0.1 `
  --duty-cycle-percent 50 `
  --load 50
```

The dry-run uses the same Core validation and ordered SCPI planning as live
configuration. It does not create a ResourceManager, open a session, query, or
write. Live use continues to require an explicit VISA resource.

## Configure a Channel 1 Square Wave

Configure a 1 kHz, 0.1 Vpp square wave with zero offset, 50% duty cycle,
and a 50-ohm instrument output-load setting:

```powershell
uv run wavegen-tool configure-square `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --duty-cycle-percent 50 `
  --load 50
```

The command first turns Channel 1 output off and leaves it off. It never
enables output; only an explicit `output --state on` command can do that.
Square frequency must be from 0.000001 Hz to 30000000 Hz. Duty cycle has a
basic range of 0.01% to 99.99%, narrowed at higher frequencies by the 16 ns
minimum pulse width. The amplitude, offset, and output-load setting limits are
the same as for sine configuration above. Square accepts `--phase-deg` with a
default of 0 degrees and an inclusive range of -360 through +360 degrees.

The load value is the instrument output-load setting and does not detect or
verify the physically connected load. Square configuration was included in the
documented live control/readback path validation; this does not claim
connector-level electrical performance.

Preview the validated square SCPI plan without a VISA resource or instrument:

```powershell
uv run wavegen-tool configure-square `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --duty-cycle-percent 50 `
  --load 50
```

Square dry-run shares live square parameter validation, including the
frequency-dependent duty-cycle limits, and SCPI command planning. It does not
create a ResourceManager, open a session, query, or write. The command list is
only a preview and is not executed. Dry-run is neither a simulator nor hardware
validation. Live `configure-square` continues to require an explicit VISA
resource.

## Configure a Channel 1 Ramp Wave

Configure a 1 kHz, 0.1 Vpp rising ramp with zero offset and a 50-ohm
instrument output-load setting:

```powershell
uv run wavegen-tool configure-ramp `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --symmetry-percent 100 `
  --load 50
```

Ramp frequency must be from 0.000001 Hz to 200000 Hz. Symmetry is the
percentage of each cycle spent rising and must be from 0% to 100%: 0% is a
falling ramp, 50% is a triangle wave, and 100% is a rising ramp. The amplitude,
offset, and output-load setting limits are the same as for sine and square
configuration. Ramp accepts `--phase-deg` with a default of 0 degrees and an
inclusive range of -360 through +360 degrees.

The command first turns Channel 1 output off and leaves it off. Configuration
never enables output; only an explicit `output --state on` command can do that.
The load value is the instrument output-load setting and does not detect or
verify the physically connected load. Ramp configuration was included in the
documented live control/readback path validation; this does not claim
connector-level electrical performance.

Preview the validated ramp SCPI plan without a VISA resource or instrument:

```powershell
uv run wavegen-tool configure-ramp `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --symmetry-percent 25 `
  --load 50
```

Ramp dry-run shares live ramp parameter normalization, frequency and symmetry
limits, Vpp and output-load validation, and SCPI command planning. It does not
create a ResourceManager, open a session, query, or write. The command list is
only a preview and is not executed. Dry-run is neither a simulator nor hardware
validation. Live `configure-ramp` continues to require an explicit VISA
resource.

## Configure a Channel 1 Triangle Wave

Configure a 1 kHz, 0.1 Vpp triangle wave with zero offset and a 50-ohm
instrument output-load setting:

```powershell
uv run wavegen-tool configure-triangle `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --phase-deg 45 `
  --load 50
```

Triangle frequency must be from 0.000001 Hz to 200000 Hz. The amplitude,
offset, and output-load setting limits are the same as for sine and square
configuration. Triangle configuration uses the instrument's dedicated
`TRIangle` function. Triangle configuration was included in the documented live
control/readback path validation; this does not claim connector-level
electrical performance. Configuration first turns Channel 1 output off and
leaves it off; it never enables output.
Triangle accepts `--phase-deg` with a default of 0 degrees and an inclusive
range of -360 through +360 degrees.

Preview the validated Triangle SCPI plan without a VISA resource or instrument:

```powershell
uv run wavegen-tool configure-triangle `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --load 50
```

Triangle dry-run uses the same Core validation and SCPI planning as live
configuration, but performs no VISA I/O. Live `configure-triangle` continues
to require an explicit VISA resource.

## Configure a Channel 1 Pulse Wave

Configure a 1 kHz, 0.1 Vpp pulse with a 100 us width, zero offset, equal 10 ns
leading and trailing edge times, and a 50-ohm instrument output-load setting
using shared-edge mode:

```powershell
uv run wavegen-tool configure-pulse `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --pulse-width-s 0.0001 `
  --offset-v 0 `
  --edge-time-s 0.00000001 `
  --load 50
```

Use independent edge control by providing both options together:

```powershell
uv run wavegen-tool configure-pulse `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --pulse-width-s 0.0001 `
  --leading-edge-s 0.00000001 `
  --trailing-edge-s 0.00000002 `
  --load 50
```

Pulse frequency must be from 0.000001 Hz to 30000000 Hz, pulse width is at
least 16 ns, and each documented leading/trailing edge range is 8.4 ns to
1 us. `--edge-time-s` sets both edges; `--leading-edge-s` and
`--trailing-edge-s` must be provided together, and shared and independent
edge options cannot be mixed. Width must also fit within the waveform period
and the selected edge-time constraints. The amplitude, offset, and
output-load setting limits are the same as for the other waveform
configuration commands. Pulse accepts `--phase-deg` with a default of 0
degrees and an inclusive range of -360 through +360 degrees.

Live configuration first turns Channel 1 output off and applies safe
intermediate pulse width and edge settings before selecting Pulse mode. Shared
mode queries the dynamic BOTH-edge maximum; independent mode queries the
leading maximum, writes the leading edge, then queries the trailing maximum
before writing the trailing edge. Before reporting success, it reads back the
function, frequency, width, applicable edge values, and output state; the
output must still be off. If the instrument limit rejects or clips an edge,
the command fails instead of reporting a false success. Configuration never
enables output; only an explicit `output --state on` command can do that. The
load value is the instrument output-load setting and does not detect or
verify the physically connected load. Independent edge control was included in
the documented live control/readback path validation; this does not claim
connector-level pulse timing or waveform quality.

Preview the pulse plan without a VISA resource:

```powershell
uv run wavegen-tool configure-pulse `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --pulse-width-s 0.0001 `
  --offset-v 0 `
  --edge-time-s 0.00000001 `
  --load 50
```

Pulse dry-run applies the live frequency, edge, and pulse-width relationship
validation and previews the safe intermediate SCPI plan. It does not query
the instrument's dynamic edge maximum or perform readback verification because
no VISA I/O occurs; live `configure-pulse` still requires an explicit resource.

## Configure a Channel 1 DC Voltage

Configure a 1.5 V DC voltage with a 50-ohm instrument output-load setting:

```powershell
uv run wavegen-tool configure-dc `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --voltage-v 1.5 `
  --load 50
```

DC mode selects `FUNCtion DC` and sets the output voltage through the offset
voltage setting. The valid range is -5 V to +5 V with the 50-ohm load setting,
or -10 V to +10 V with the high-impedance load setting. DC mode has no
frequency or amplitude parameter.

The command first turns Channel 1 output off and leaves it off. Configuration
never enables output; only an explicit `output --state on` command can do that.
The load value is the instrument output-load setting and does not detect or
verify the physically connected load. Actual terminal voltage still depends
on the physically connected load. DC configuration was included in the
documented live control/readback path validation; this does not claim actual
terminal-voltage accuracy.

Preview the DC plan without a VISA resource:

```powershell
uv run wavegen-tool configure-dc `
  --dry-run `
  --model keysight-33521b `
  --voltage-v 1.5 `
  --load 50
```

DC dry-run applies the live load-dependent voltage limits and previews the same
SCPI plan. It performs no VISA I/O; live `configure-dc` still requires an
explicit resource.

## Configure a Channel 1 Noise Wave

Configure white, quasi-Gaussian noise with 0.1 Vpp amplitude, 100000 Hz
bandwidth, and a 50-ohm instrument output-load setting:

```powershell
uv run wavegen-tool configure-noise `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --amplitude-vpp 0.1 `
  --offset-v 0 `
  --bandwidth-hz 100000 `
  --load 50
```

Noise bandwidth must be from 0.001 Hz to 30000000 Hz and controls the frequency
range where noise energy is concentrated. Noise has no ordinary waveform
frequency parameter. Amplitude, offset, and output-load limits are the same as
for the other Vpp waveform configuration commands. The Vpp value represents
the output boundary; because of the noise's statistical characteristics, the
signal rarely reaches the full peak-to-peak boundary.

The command first turns Channel 1 output off and leaves it off. Configuration
never enables output; only an explicit `output --state on` command can do that.
The load value is the instrument output-load setting and does not detect or
verify the physically connected load. Noise configuration was included in the
documented live control/readback path validation; its statistical output was
not electrically characterized.

Preview the noise plan without a VISA resource:

```powershell
uv run wavegen-tool configure-noise `
  --dry-run `
  --model keysight-33521b `
  --amplitude-vpp 0.1 `
  --bandwidth-hz 1000000 `
  --offset-v 0 `
  --load 50
```

Noise dry-run applies the live bandwidth and Vpp/output-load limits and
previews the same SCPI plan. It performs no VISA I/O; live `configure-noise`
still requires an explicit resource.

## Configure a Channel 1 PRBS Waveform

Configure a 1 Mbps PN15 waveform with 0.1 Vpp amplitude, zero offset, equal
10 ns rising and falling edge times, and a 50-ohm instrument output-load
setting:

```powershell
uv run wavegen-tool configure-prbs `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --bit-rate-bps 1000000 `
  --amplitude-vpp 0.1 `
  --pattern PN15 `
  --offset-v 0 `
  --edge-time-s 0.00000001 `
  --load 50
```

Bit rate must be from 0.001 bit/s to 50000000 bit/s. Supported patterns are
PN7, PN9, PN11, PN15, PN20, and PN23. Pattern input is case-insensitive and
results are normalized to uppercase canonical values. The common rising and falling edge time
must be from 8.4 ns to 1 µs and must fit within one bit period. With the
50-ohm load setting, amplitude must be from 0.001 Vpp to 10 Vpp and
`abs(offset) + amplitude / 2` must not exceed 5 V. With the high-impedance
setting, amplitude must be from 0.002 Vpp to 20 Vpp and the same expression
must not exceed 10 V.

The command first turns Channel 1 output off and leaves it off. Configuration
never enables output; only an explicit `output --state on` command can do that.
The load value is the instrument output-load setting and does not detect or
verify the physically connected load. PRBS configuration was included in the
documented live control/readback path validation; this does not claim
connector-level electrical performance.

Preview the PRBS plan without a VISA resource:

```powershell
uv run wavegen-tool configure-prbs `
  --dry-run `
  --model keysight-33521b `
  --bit-rate-bps 1000000 `
  --amplitude-vpp 0.1 `
  --pattern PN9 `
  --offset-v 0 `
  --edge-time-s 0.0000000084 `
  --load 50
```

PRBS dry-run applies the live pattern, bit-rate, and edge-time/bit-period
validation and previews the same SCPI plan. It performs no VISA I/O; live
`configure-prbs` still requires an explicit resource.

## Control Channel 1 Output

Output state changes are always explicit. Turn Channel 1 on only after
reviewing the configured signal and connected load:

```powershell
uv run wavegen-tool output `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --state on
```

Turn Channel 1 off:

```powershell
uv run wavegen-tool output `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --state off
```

The `output` command identifies the instrument in the same session and writes
only the requested Channel 1 output state. Only an explicit
`output --state on` command enables the output.

## Safety Boundary

The identify command opens only the resource supplied by the user, issues
exactly one `*IDN?` query, and closes the VISA session and ResourceManager. Raw
listing only returns the backend-reported resource strings; it never opens an
instrument session or sends SCPI. Live-only listing opens only eligible
candidates and sends each at most one `*IDN?` query with fixed open/session
timeouts where applicable. It closes the session without clear, remote/local,
reset, cleanup, diagnostic, or other commands. Control commands validate the
backend and transport, open only the explicit resource, and resolve the exact
manufacturer/model identity before any write. They use the same session for
identification and control, do not retry or switch backends, and never send
`*RST`. There is no automatic or background resource scan.

Live instrument operations on USB with the system VISA backend attempt to return
the instrument to local before closing the session. Resource listing, dry-run,
simulator, TCPIP/LAN, and `@py` operations do not make this attempt.

The `status` command resolves the exact manufacturer/model identity before its
read-only Channel 1 queries. It does not write, reset, clear, inspect the error
queue, or change output state.

`configure-sine`, `configure-sine-sweep`, `configure-square-sweep`,
`configure-ramp-sweep`, `configure-triangle-sweep`, `configure-square`,
`configure-ramp`, `configure-triangle`, `configure-pulse`, `configure-dc`,
`configure-noise`, and `configure-prbs`
first turn Channel 1 off and leave it off after configuration. They cannot
enable output. The `output` command changes only the Channel 1 output state
and does not reconfigure or reset the instrument.

For identify, the `@py` plus USB combination is rejected before the
ResourceManager is created or any VISA I/O occurs. USB resources remain
available through the system backend. There is no automatic backend fallback.

Do not run live identification until the resource, backend, and read-only scope
have been reviewed for the target setup.

## Development Checks

```powershell
uv lock
uv sync --all-extras --locked --link-mode=copy
uv run python -m ruff check src tests
uv run python -m pytest tests -q -p no:cacheprovider
uv run python -m build
uv run python -c "import wavegen_tool_core; import wavegen_tool_cli; import wavegen_tool_webui"
uv run wavegen-tool --help
uv run wavegen-tool list-resources --help
uv run wavegen-tool identify --help
uv run wavegen-tool status --help
uv run wavegen-tool configure-sine --help
uv run wavegen-tool configure-sine-sweep --help
uv run wavegen-tool configure-square-sweep --help
uv run wavegen-tool configure-ramp-sweep --help
uv run wavegen-tool configure-triangle-sweep --help
uv run wavegen-tool configure-square --help
uv run wavegen-tool configure-ramp --help
uv run wavegen-tool configure-triangle --help
uv run wavegen-tool configure-pulse --help
uv run wavegen-tool configure-dc --help
uv run wavegen-tool configure-noise --help
uv run wavegen-tool configure-prbs --help
uv run wavegen-tool output --help
```
