# Wavegen Tool Core

`wavegen_tool_core` is the instrument-facing authority for Wavegen Tool. It
owns model identity, capabilities, request admission, command planning,
VISA/backend behavior, simulation, and hardware safety. The CLI and reserved
WebUI packages are adapters over Core; they must not create parallel identity,
capability, SCPI, VISA, or safety implementations.

## Purpose and Ownership

Core owns:

- exact live identity parsing and registered-model resolution;
- model capabilities, including channel count and frequency limits;
- request and configuration validation;
- static waveform, modulation, burst, sweep, List Sweep, output, status,
  trigger, and error-queue behavior;
- selected-channel, coupling, tracking, and output-safety rules;
- resource transport and backend normalization;
- live VISA session execution and cleanup;
- hardware-free dry-run plans and simulator behavior.

Core is the final authority for support, capabilities, live admission, and
instrument safety. Validation performed by an adapter does not authorize a
request that Core rejects.

## Adapter Boundary

The dependency direction is:

```text
CLI   -> Core
WebUI -> Core
```

Core must not import `wavegen_tool_cli` or `wavegen_tool_webui`. Adapters own
input parsing, presentation, serialization, and adapter-specific lifecycle
behavior. Core owns instrument-facing behavior and returns structured results
or structured errors.

The WebUI import package is currently reserved; it does not define a public
WebUI feature surface.

## Identity and Model Registry

Live identity comes from the instrument's `*IDN?` response. Core recognizes the
manufacturers `Keysight Technologies` and `Agilent Technologies` and the
registered model IDs:

- `keysight-33510b` (`33510B`)
- `keysight-33512b` (`33512B`)
- `keysight-33521b` (`33521B`)

Manufacturer matching ignores case and collapses ordinary whitespace. Model
matching ignores case and surrounding whitespace. Neither uses prefix,
substring, fuzzy, alias, resource-name, or guessed matching. Unknown,
malformed, unsupported, or mismatched identities fail closed.

A dry-run or simulator can use an explicitly selected registered planning
model. That selection does not establish the identity of a physical
instrument. Normal live operation uses the exact detected identity; an
expected model can only guard that result, never override it.

## Capabilities and Selected Channels

Capabilities are registry-owned Core data:

| Model | Channels | Shared sine/square/pulse/noise frequency limit |
| --- | ---: | ---: |
| 33510B | 2 | 20 MHz |
| 33512B | 2 | 20 MHz |
| 33521B | 1 | 30 MHz |

Every selected-channel request is checked against the resolved profile.
Channel 2 is rejected for the one-channel 33521B.

On a two-channel live instrument, independent state-changing operations fail
closed unless frequency coupling, voltage coupling, Channel 1 tracking, and
Channel 2 tracking are all reported off. Core never disables coupling or
tracking automatically. Read-only status does not require this independent
channel guard.

Waveform and output configuration turn off only the selected channel and leave
it off. Enabling output is a separate, explicit operation.

## Dry-run, Simulator, and Live Modes

Dry-run validates a registered planning model, selected channel, requested
configuration, and safety constraints, then returns the ordered SCPI plan. It
does not create a VISA resource manager, open a resource, query `*IDN?`, send
SCPI, or change hardware.

The simulator provides deterministic, process-local, hardware-free state. The
standalone CLI supports registered profiles and independent channel state for
two-channel models. A new standalone CLI process starts with fresh state;
direct callers can preserve state by reusing the simulator objects supplied by
the simulator module. Simulator behavior is not hardware validation.

Live operations validate the resource transport and backend before opening the
single explicit resource. Core resolves exact identity and applies the
applicable support and safety checks before operation-specific live I/O. There
is no model override or backend fallback.

## VISA and Backend Boundary

Direct identify and control operations admit only USB and TCPIP/LAN resources:

| Selector | Core backend identity | Live transport scope |
| --- | --- | --- |
| omitted or `system` | `system_visa` | USB and TCPIP/LAN |
| `@py` | `pyvisa_py` | TCPIP/LAN only |
| `@bt` | `pyvisa_bt` | No Product-open or validation-open scope |

An installed VISA runtime does not grant model or connection support. Resource
discovery is a separate, explicit diagnostic operation; it does not expand the
identify/control boundary.

## Output Safety and Fail-closed Behavior

Core rejects missing metadata, unsupported transports or backend combinations,
malformed or unsupported identities, expected-model mismatches, unavailable
channels, invalid parameter combinations, unsafe coupling/tracking state, and
verification failures. It does not use `*RST`, preset, or setup recall as part
of normal operation.

Identification and read-only diagnostics do not enable output. Configuration
does not enable output implicitly. The instrument-wide trigger operation sends
one `*TRG` and is not a selected-channel operation.

## Public Package Surface

The supported top-level import boundary is `wavegen_tool_core`. It exposes the
configuration and result types, structured errors, identity and backend
helpers, simulator primitives, and Core operations for identification,
resource listing, status, error-queue reads, waveform configuration, sweeps,
output, and bus triggering.

Some model-registry, capability, and multi-profile simulator helpers remain in
their focused Core submodules. Downstream adapters should prefer the package
root where an operation is exported and must not import underscore-prefixed
implementation helpers or construct SCPI directly.

## Testing

Core tests are hardware-free by default. Use dry-run, simulator, or fake VISA
sessions for planning, validation, routing, safety, and error-path coverage.
See the repository [Testing Guidelines](../testing-guidelines.md).

## Related Documentation

- [Core integration](integration.md)
- [Supported models](supported-models.md)
- [CLI guide](../cli/README.md)
- [Testing guidelines](../testing-guidelines.md)
- [Worker contract](../contracts/wavegen-worker-contract.md)
