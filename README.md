# Wavegen Tool

Wavegen Tool is a safety-focused Python toolkit for identifying and controlling
explicitly selected waveform generators through VISA. It provides a reusable
Core package, a command-line interface, and a reserved WebUI package in one
distribution.

Live operation is fail-closed: the instrument identity, connection, backend,
model support, channel, and requested configuration must all be admitted by
Core. Waveform configuration keeps the selected output off, output-on is always
explicit, and the tool does not reset the instrument or auto-select a VISA
resource.

## Features

- Exact Keysight or Agilent model identity and capability lookup
- Hardware-free dry-run planning and a stateful in-memory simulator
- Static sine, square, ramp, triangle, pulse, DC, noise, and PRBS waveforms
- AM, FM, PM, FSK, BPSK, PWM, and Sum configuration on supported carriers
- Counted Burst, Gated Burst, linear/logarithmic sweep, and List Sweep support
- Selected-channel status, output configuration, output control, and bounded
  error-queue reads
- Explicit USB or TCPIP/LAN live access through admitted VISA backends
- Human-readable and JSON CLI output, plus a local Worker control plane
- Hardware-free preflight and operator-confirmed live validation workflows

Detailed commands, option groups, limitations, and examples are in the
[CLI guide](docs/cli/README.md).

## Current Product Support

| Model | Channels | Dry-run | Simulator | Product Live |
| --- | ---: | ---: | ---: | ---: |
| Keysight/Agilent 33510B | 2 | Yes | Yes | No |
| Keysight/Agilent 33512B | 2 | Yes | Yes | Yes |
| Keysight/Agilent 33521B | 1 | Yes | Yes | Yes |

The 33510B is a registered hardware-free profile and has a contributor
validation route, but it is not Product Live. See the authoritative
[supported-models matrix](docs/core/supported-models.md) for identity,
capability, transport, backend, and support boundaries.

## Project Structure

```text
src/
  wavegen_tool_core/   Identity, capabilities, validation, SCPI, VISA, safety
  wavegen_tool_cli/    Direct CLI and local Worker adapters
  wavegen_tool_webui/  Reserved WebUI package
docs/
  core/                Core ownership and integration guidance
  cli/                 Complete CLI reference
  contracts/           Stable Worker and orchestration contracts
scripts/               Hardware-free and opt-in live validation runners
tests/                 Hardware-free tests and validation-script checks
```

Core does not depend on CLI or WebUI. Adapters use Core for model-specific,
instrument-command, capability, and safety decisions.

## Requirements and Installation

Python 3.10 or newer is required. The documented contributor workflow also
uses [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --all-extras --locked --link-mode=copy
```

This installs the `wavegen-tool` entry point and the `wavegen_tool_core`,
`wavegen_tool_cli`, and `wavegen_tool_webui` import packages from the single
root distribution.

## Quick Start

Inspect an offline model profile:

```powershell
uv run wavegen-tool capabilities --model keysight-33521b --json
```

Preview a safe sine configuration without VISA I/O:

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1
```

Try the same configuration in the in-memory simulator:

```powershell
uv run wavegen-tool configure-sine `
  --simulate `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1
```

Dry-run and simulator results are not real-instrument validation. Live use
requires an explicit resource and follows the identity, backend, channel, and
output-safety rules in the [CLI guide](docs/cli/README.md).

## Documentation

- [Core overview](docs/core/README.md)
- [Core integration guide](docs/core/integration.md)
- [Supported models](docs/core/supported-models.md)
- [CLI guide](docs/cli/README.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Testing guidelines](docs/testing-guidelines.md)
- [Worker contract](docs/contracts/wavegen-worker-contract.md)
- [Common CLI JSONL contract](docs/contracts/common-cli-jsonl-contract.md)
- [Common Worker protocol](docs/contracts/common-worker-protocol.md)
- [Common orchestrator workflows](docs/contracts/common-orchestrator-workflows.md)

## Contributing

Start with [CONTRIBUTING.md](docs/CONTRIBUTING.md). Normal contributions use
focused hardware-free checks. Changes that affect live hardware behavior use
the separate, explicit validation and evidence workflow described there.

## License

Wavegen Tool is available under the [MIT License](LICENSE).
