# Wavegen Tool

Wavegen Tool is a safety-focused Python toolkit for identifying and controlling
explicitly selected waveform generators through VISA. One distribution provides
a reusable Core package, a Direct CLI, and a local Worker control plane. A
WebUI import package is reserved but is not yet an operational user interface.

Live use is fail-closed: Core checks the detected instrument identity, selected
resource, backend, model support, channel, and requested operation. Configuration
leaves the selected output off; enabling output always requires an explicit
command. Normal instrument control does not reset the instrument or automatically
select a VISA resource. The explicitly initiated Real Live Full validation suite
is a separate, bounded reset exception described below.

## Features

- Exact Keysight/Agilent model identification and registered capabilities
- Hardware-free dry-run SCPI planning and a stateful in-memory simulator
- Static sine, square, ramp, triangle, pulse, DC, noise, and PRBS waveforms
- Supported AM, FM, PM, FSK, BPSK, PWM, and Sum configurations
- Counted Burst, Gated Burst, linear/logarithmic frequency sweeps, and List Sweep
- Named arbitrary-waveform CSV upload, playback, native sequences, and
  selected-channel volatile memory management in dry-run and simulation
- Selected-channel status, output configuration, output control, and bounded
  error-queue reads
- Explicit USB or TCPIP/LAN Product Live access through admitted VISA backends
- Human-readable and JSON Direct CLI output; loopback-only Worker lifecycle
  and instrument commands
- Hardware-free preflight and separately approved real-instrument validation

See the [CLI guide](docs/cli/README.md) for exact commands, parameters, safety
rules, and practical examples.

### Supported Models

| Model | Channels | Dry-run | Simulator | Product Live |
| --- | ---: | ---: | ---: | ---: |
| Keysight/Agilent 33510B | 2 | Yes | Yes | No |
| Keysight/Agilent 33512B | 2 | Yes | Yes | Yes |
| Keysight/Agilent 33521B | 1 | Yes | Yes | Yes |

The 33510B is a registered hardware-free profile with a separate contributor
validation route; it is not Product Live. Product Live admission does not mean
that every command, backend, or transport combination has been independently
verified on hardware. The [supported-models matrix](docs/core/supported-models.md)
is authoritative for model, transport, backend, and feature boundaries.

## Project Structure

```text
src/
  wavegen_tool_core/   Identity, capabilities, validation, SCPI, VISA, safety
  wavegen_tool_cli/    Direct CLI and local Worker
  wavegen_tool_webui/  Reserved WebUI package (not an operational UI)
docs/
  core/                Core and model support documentation
  cli/                 CLI and Worker usage
  contracts/           CLI JSON/JSONL and Worker/orchestrator contracts
scripts/               Hardware-free and opt-in live validation scripts
tests/                 Hardware-free Core, CLI, Worker, and script tests
```

Core does not import CLI or WebUI. Adapters use Core for instrument commands,
model-specific decisions, capabilities, and safety checks.

## Key Dependencies

- **Python 3.10+** for the application and contributor commands
- **PyVISA** (`pyvisa`), installed as a required Python dependency
- **System VISA runtime** (for example, Keysight IO Libraries Suite) for the
  documented default `--backend system` live path; it is not bundled
- **PyVISA-py** (`pyvisa-py`), optional and installed separately for
  `--backend @py` TCPIP/LAN use
- **uv**, used by the documented contributor workflow; **pytest**, **Ruff**,
  and **build** are available through the project's development dependencies

The root [pyproject.toml](pyproject.toml) is the sole distribution and console
entry-point definition.

## Install

From the repository root, install the locked contributor environment:

```powershell
uv sync --all-extras --locked --link-mode=copy
uv run wavegen-tool --help
```

To use the optional `@py` backend, install `pyvisa-py` **after** syncing:

```powershell
uv pip install pyvisa-py
```

A later `uv sync` may remove manually installed packages. The default
`--backend system` uses PyVISA's runtime resolution; it does not guarantee a
particular underlying library and does not fall back to another backend.
Installing a backend does not expand Product Live scope: `system` admits USB
and TCPIP/LAN, `@py` admits TCPIP/LAN only, and `@bt` has no Product Live scope.
See [CLI installation and safety](docs/cli/README.md#installation-and-invocation).

## Quick Start

Inspect a registered profile without touching hardware:

```powershell
uv run wavegen-tool capabilities --model keysight-33521b --json
```

Preview a sine configuration without any VISA I/O:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1
```

Run the same configuration in the in-memory simulator:

```powershell
uv run wavegen-tool configure-sine `
  --simulate `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1
```

With a VISA runtime installed, explicitly list backend-reported resources
without opening instrument sessions:

```powershell
uv run wavegen-tool list-resources --backend system
```

The optional `--live-only` discovery mode **does** open eligible resources and
query `*IDN?`. Neither listing nor a responding IDN automatically grants
Product Live control. See [resource listing](docs/cli/README.md#visa-resource-listing).

Dry-run and simulation are hardware-free, not real-instrument validation.
Direct CLI Live needs an operator-supplied `--resource` or the operator-set
`WAVEGEN_TOOL_RESOURCE`; Worker Live and live validation scripts require
explicit resource arguments. Signal configuration stays OFF until a separate
`output --state on` command.

## Build

Build the existing Python wheel and source distribution (not a standalone
Windows executable or Desktop application):

```powershell
uv run python -m build
```

Artifacts are written to `dist/`. This uses the root `pyproject.toml` and its
`setuptools` build backend; no separate component packages are defined.

## Test

Run the hardware-free repository tests and source lint checks:

```powershell
uv run python -m pytest -q -rs -p no:cacheprovider --basetemp .tmp_tests/readme-pytest
uv run python -m ruff check src tests
```

The focused checks to choose for a change are described in the
[testing guidelines](docs/testing-guidelines.md). Windows contributors can
also perform a hardware-free CLI preflight:

```powershell
.\scripts\preflight-cli.ps1 -Target all
```

The separately initiated Real Live Full suite is **not** a default test. As a
specific validation exception, it performs a bounded `*RST` before each
independent case, only after verifying all outputs OFF, then checks completion
and the safe-off state. Normal CLI, Worker, dry-run, simulator, and preflight
paths do not perform this reset. Real hardware validation requires an explicit
operator resource and the procedures in
[Contributing](docs/CONTRIBUTING.md).

## Documentation

- [Core overview](docs/core/README.md)
- [Core integration guide](docs/core/integration.md)
- [Supported models](docs/core/supported-models.md)
- [CLI guide and examples](docs/cli/README.md)
- [Contributing and validation workflows](docs/CONTRIBUTING.md)
- [Testing guidelines](docs/testing-guidelines.md)
- [Wavegen CLI JSON / JSONL contract](docs/contracts/wavegen-cli-jsonl-contract.md)
- [Wavegen Worker contract](docs/contracts/wavegen-worker-contract.md)
- [Wavegen orchestrator workflows](docs/contracts/wavegen-orchestrator-workflows.md)
- [Common CLI JSONL contract](docs/contracts/common-cli-jsonl-contract.md)
- [Common Worker protocol](docs/contracts/common-worker-protocol.md)
- [Common orchestrator workflows](docs/contracts/common-orchestrator-workflows.md)

## Contributing

Start with [CONTRIBUTING.md](docs/CONTRIBUTING.md). Ordinary changes use
focused, hardware-free checks. Changes involving real instruments require
separate opt-in validation and private evidence handling.

## License and Disclaimer

Wavegen Tool is available under the [MIT License](LICENSE). It is an
instrument-control tool, not a substitute for verifying instrument settings,
connected loads, external triggering, or bench safety. Operators must review
their setup before explicitly enabling output. Hardware-free results must
not be represented as physical-instrument validation.
