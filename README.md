# Wavegen Tool

Wavegen Tool is a safety-focused Python toolkit for identifying explicitly
selected waveform generators through VISA. The project is in early development;
the current milestone supports read-only identification of the Keysight 33521B.

## Current Scope

- Exact manufacturer-and-model recognition for Keysight Technologies 33521B
- System VISA explicitly selects the IVI VISA backend and accepts explicit USB
  and TCPIP/LAN resources
- The `@py` backend from `pyvisa-py` accepts explicit TCPIP/LAN resources only
- Raw VISA resource listing and opt-in live-only connectivity checks
- Human-readable and JSON CLI output
- Hardware-free tests with injectable fake VISA sessions

The identify command rejects GPIB and all other transports outside its explicit
scope. Other Trueform models are not treated as supported.

The identify result confirms that the reported instrument is a recognized
model. It does not claim that every live backend/transport scope or future
control feature is hardware-validated. This milestone has not been validated
against real hardware.

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

List raw resources through the default system backend:

```powershell
uv run wavegen-tool list-resources
```

Select system VISA explicitly, which uses `@ivi`:

```powershell
uv run wavegen-tool list-resources --backend system
```

Select `pyvisa-py`, which uses `@py`:

```powershell
uv run wavegen-tool list-resources --backend "@py"
```

Use `--live-only` to open each candidate allowed by the current
backend/transport verification scope, apply a fixed one-second timeout, and
send one `*IDN?` query. Only resources returning a non-empty response are
shown:

```powershell
uv run wavegen-tool list-resources --live-only --backend system
uv run wavegen-tool list-resources --live-only --backend "@py"
uv run wavegen-tool list-resources --live-only --backend system --json
```

System live verification accepts USB and TCPIP/LAN candidates. `@py` live
verification accepts TCPIP/LAN candidates only. GPIB, ASRL, PXI, VXI, unknown
transports, and `@py` plus USB candidates remain visible in raw results but are
skipped in live-only mode.

A non-empty response confirms connectivity only. It does not establish that
the resource is a Keysight 33521B or authorize the model for later control.
Live-only checks do not retry, switch backends, change instrument settings, or
enable output. After reviewing the results, choose a resource and pass it
explicitly to identify:

```powershell
uv run wavegen-tool identify `
  --resource "<EXPLICIT_RESOURCE>" `
  --backend system
```

Neither listing nor identification falls back to another backend.

## Identify an Instrument

The resource is always required and must be supplied explicitly. System VISA is
the default and explicitly selects the IVI VISA backend.

System VISA with USB:

```powershell
uv run wavegen-tool identify `
  --resource "USB0::0x0000::0x0000::MY00000000::INSTR"
```

System VISA with TCPIP/LAN:

```powershell
uv run wavegen-tool identify `
  --resource "TCPIP0::192.0.2.10::inst0::INSTR"
```

Select `pyvisa-py` explicitly for TCPIP/LAN:

```powershell
uv run wavegen-tool identify `
  --resource "TCPIP0::192.0.2.10::inst0::INSTR" `
  --backend "@py"
```

Request one JSON object:

```powershell
uv run wavegen-tool identify `
  --resource "TCPIP0::192.0.2.10::inst0::INSTR" `
  --backend "@py" `
  --json
```

JSON identify outcomes use `model_supported` to report recognized-model
resolution. JSON output applies to command outcomes after successful argument
parsing; argument usage errors retain argparse's standard format.

The resource strings, serial number, and documentation-only IP address above
are fictional. Raw IDN data remains local to the running process and is not
written to files or artifacts.

## Safety Boundary

The identify command opens only the resource supplied by the user, issues
exactly one `*IDN?` query, and closes the VISA session and ResourceManager. Raw
listing never opens an instrument session or sends SCPI. Live-only listing
opens only eligible candidates and sends each at most one `*IDN?` query with a
fixed timeout. No command retries, switches backends automatically, sends
reset or diagnostic commands, configures a waveform, or controls output state.
There is no automatic or background resource scan.

The `@py` plus USB combination is rejected before the ResourceManager is
created or any VISA I/O occurs. USB resources remain available through the
system backend. There is no automatic backend fallback.

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
