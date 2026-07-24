# Wavegen Tool

Wavegen Tool is a safety-focused Python toolkit for identifying explicitly
selected waveform generators through VISA. The project is in early development;
the current milestone supports read-only identification of the Keysight 33521B.

## Current Scope

- Exact manufacturer-and-model recognition for Keysight Technologies 33521B
  and Agilent Technologies 33521B
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
claim that every live backend/transport scope or future control feature is
hardware-validated. This milestone has been validated against an Agilent
Technologies 33521B through system VISA over USB. Live-only discovery has also
been validated with USB and ASRL resources.

Waveform configuration, output control, automatic resource scanning, WebUI
features, and release executables are not implemented. Identification sends
only `*IDN?`; it does not reset the instrument, change settings, or enable an
output.

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

## Safety Boundary

The identify command opens only the resource supplied by the user, issues
exactly one `*IDN?` query, and closes the VISA session and ResourceManager. Raw
listing only returns the backend-reported resource strings; it never opens an
instrument session or sends SCPI. Live-only listing opens only eligible
candidates and sends each at most one `*IDN?` query with fixed open/session
timeouts where applicable. It closes the session without clear, remote/local,
reset, cleanup, diagnostic, or other commands. No command retries, switches
backends automatically, configures a waveform, or controls output state. There
is no automatic or background resource scan.

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
```
