# Wavegen Tool

Wavegen Tool is a safety-focused Python toolkit for identifying explicitly
selected waveform generators and performing bounded control through VISA. The
current milestone supports identification, read-only Channel 1 status, and
basic Channel 1 sine/square/ramp/pulse/DC/output control for the Keysight or
Agilent 33521B.

## Current Scope

- Exact manufacturer-and-model recognition for Keysight Technologies 33521B
  and Agilent Technologies 33521B
- Parameter-validated Channel 1 sine configuration with explicit load, frequency,
  amplitude, and offset
- Hardware-unvalidated, parameter-validated Channel 1 square configuration
- Hardware-unvalidated, parameter-validated Channel 1 ramp configuration
- Hardware-unvalidated, parameter-validated Channel 1 pulse configuration
- Hardware-unvalidated, parameter-validated Channel 1 DC voltage configuration
- Explicit Channel 1 output on/off control
- Hardware-unvalidated, read-only Channel 1 status query
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
reported by the instrument while normalizing the model to `33521B`. It does not
claim that every live backend/transport scope or control feature is
hardware-validated. Identification has been hardware-validated against an
Agilent Technologies 33521B through system VISA over USB. Live-only discovery
has also been validated with USB and ASRL resources. Sine, square, ramp, pulse,
and DC configuration, output control, and status readback have not yet been
hardware-validated.

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

Status reports the Channel 1 output state, function, frequency, amplitude and
current amplitude unit, offset, and instrument output-load setting. It does not
modify settings or turn the output on or off. The output-load setting does not
detect or verify the physically connected load. Status readback has not yet
been hardware-validated.

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
output off, and applies the settings. It never turns the output on.

The supported 33521B sine limits are:

- Frequency: 0.000001 Hz to 30000000 Hz
- 50-ohm load setting: 0.001 Vpp to 10 Vpp, with
  `abs(offset) + amplitude / 2 <= 5 V`
- High-impedance load setting: 0.002 Vpp to 20 Vpp, with
  `abs(offset) + amplitude / 2 <= 10 V`

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
the same as for sine configuration above.

The load value is the instrument output-load setting and does not detect or
verify the physically connected load. Square configuration has not yet been
hardware-validated.

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
configuration.

The command first turns Channel 1 output off and leaves it off. Configuration
never enables output; only an explicit `output --state on` command can do that.
The load value is the instrument output-load setting and does not detect or
verify the physically connected load. Ramp configuration has not yet been
hardware-validated.

## Configure a Channel 1 Pulse Wave

Configure a 1 kHz, 0.1 Vpp pulse with a 100 µs width, zero offset, equal 10 ns
leading and trailing edge times, and a 50-ohm instrument output-load setting:

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

Pulse frequency must be from 0.000001 Hz to 30000000 Hz, pulse width is at
least 16 ns, and edge time must be from 8.4 ns to 1 µs. This command applies
the same edge time to the leading and trailing edges. Width must also fit
within the waveform period and the selected edge-time constraints. The
amplitude, offset, and output-load setting limits are the same as for the
other waveform configuration commands.

The command first turns Channel 1 output off and leaves it off. Configuration
never enables output; only an explicit `output --state on` command can do that.
The load value is the instrument output-load setting and does not detect or
verify the physically connected load. Pulse configuration has not yet been
hardware-validated.

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
on the physically connected load. DC configuration has not yet been
hardware-validated.

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

The `status` command resolves the exact manufacturer/model identity before its
read-only Channel 1 queries. It does not write, reset, clear, inspect the error
queue, or change output state.

`configure-sine`, `configure-square`, `configure-ramp`, `configure-pulse`, and
`configure-dc` first turn Channel 1 off and leave it off after configuration.
They cannot enable output. The `output` command changes only the Channel 1
output state and does not reconfigure or reset the instrument.

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
uv run wavegen-tool configure-square --help
uv run wavegen-tool configure-ramp --help
uv run wavegen-tool configure-pulse --help
uv run wavegen-tool configure-dc --help
uv run wavegen-tool output --help
```
