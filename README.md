# Wavegen Tool

Wavegen Tool is a safety-focused Python toolkit for identifying explicitly
selected waveform generators through VISA. The project is in early development;
the current milestone supports read-only identification of the Keysight 33521B.

## Current Scope

- Exact manufacturer-and-model recognition for Keysight Technologies 33521B
- System VISA explicitly selects the IVI VISA backend and accepts explicit USB
  and TCPIP/LAN resources
- The `@py` backend from `pyvisa-py` accepts explicit TCPIP/LAN resources only
- Human-readable and JSON CLI output
- Hardware-free tests with injectable fake VISA sessions

GPIB and all other transports are rejected. Other Trueform models are not
treated as supported.

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
resolution. JSON output applies to identify outcomes after successful argument
parsing; argument usage errors retain argparse's standard format.

The resource strings, serial number, and documentation-only IP address above
are fictional. Raw IDN data remains local to the running process and is not
written to files or artifacts.

## Safety Boundary

The current CLI opens only the resource supplied by the user, issues exactly
one `*IDN?` query, and closes the VISA session and ResourceManager. It does not
scan resources, retry, switch backends automatically, send reset or diagnostic
commands, configure a waveform, or control output state.

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
uv run wavegen-tool identify --help
```
