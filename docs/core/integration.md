# Core Integration

`wavegen_tool_core` is the shared instrument boundary for the CLI and future
adapters. A caller supplies operation inputs and handles presentation; Core
owns identity, capability lookup, request admission, SCPI planning and
execution, VISA/backend handling, simulation, and instrument safety.

## Public Import Boundary

Core is installed by the single root `wavegen-tool` distribution and imported
as `wavegen_tool_core`. Core must not import the CLI or WebUI packages.

Prefer names exported from the package root for configuration/result types,
structured errors, identity and backend helpers, simulator primitives, and
instrument operations. Focused helpers that are not exported, including the
full model/capability registry and multi-profile simulator factory, remain in
their Core submodules. Underscore-prefixed helpers and internal SCPI builders
are not adapter APIs.

Adapters must not reimplement model matching, capability decisions, SCPI,
VISA sessions, selected-channel guards, or output safety.

## Planning Model and Physical Identity

Dry-run and simulator callers select one exact registered model ID:

- `keysight-33510b`
- `keysight-33512b`
- `keysight-33521b`

This is a planning identity only. It must not be presented as the detected
identity of physical hardware.

For live operation, Core opens only the explicit resource and queries `*IDN?`.
The response must contain exactly four non-empty fields. In normal Product
mode, its manufacturer/model must resolve to a Product Live scope. The explicit
contributor validation mode may additionally admit the registered 33510B route.
Accepted manufacturers are `Keysight Technologies` and
`Agilent Technologies`.

An expected model ID supplied by an adapter is a guard. It must equal the
canonical model ID resolved from the live identity. It cannot replace or
override that identity.

## Capability Lookup and Admission

Resolve a canonical model ID before capability lookup or model-specific
planning. Core owns the current channel counts and frequency limits described
in [Supported Models](supported-models.md).

Validate the complete request before hardware writes. Admission includes the
model, channel, waveform values, voltage representation, modulation or burst
option group, sweep/list parameters, output settings, trigger routing, and all
applicable safety constraints. Invalid partial option groups and mutually
exclusive modes fail before execution.

The CLI defaults selected-channel commands to Channel 1, but callers must still
pass the selected channel to Core. Channel 2 is invalid for 33521B. On a
two-channel live instrument, Core queries frequency coupling, voltage coupling,
and both tracking states before independent state-changing operations. Any
enabled, indeterminate, or failed guard fails closed; Core does not change
those states.

## Support Policy

Normal Product Live support is limited to 33512B and 33521B. The registered
33510B profile supports hardware-free planning and has a validation-only
contributor route. That route does not authorize normal Product Live operation
and cannot update support metadata automatically.

Callers must not infer support from a related model, successful dry-run,
simulator behavior, a VISA resource string, or an installed backend.

## Dry-run Boundary

Dry-run validates the selected registered model and returns a structured,
ordered command plan with `executed` false. It must not create a VISA resource
manager, open a resource, query identity, send SCPI, or mutate a device.

Use dry-run for inspection and hardware-free tests. Do not describe it as
real-instrument validation.

## Simulator Boundary

Simulator mode uses Core's in-memory resource manager and state. Registered
profiles provide deterministic planning and selected-channel behavior; callers
that need state across calls must reuse the same simulator state/factory.

The Worker is a separate adapter with its own command contract. Its dry-run and
simulator requests accept registered planning model IDs. The first admitted
simulate job binds one persistent model-aware Core simulator state; dry-run
does not bind it, and later requests cannot silently switch models.

Worker live admission follows Core Product support. Detected identity remains
authoritative and an optional `expected_model_id` is only a mismatch guard.
Do not infer Worker command exposure from the broader Direct Core/CLI surface.

## Live Resource and Backend Boundary

Live callers must provide one exact resource. Core classifies and validates the
resource transport and normalized backend before creating a resource manager:

| Selector | Normalized backend | Direct live transport scope |
| --- | --- | --- |
| omitted or `system` | `system_visa` | USB, TCPIP/LAN |
| `@py` | `pyvisa_py` | TCPIP/LAN only |
| `@bt` | `pyvisa_bt` | None |

There is no automatic backend fallback. GPIB, ASRL, PXI, VXI, unknown
transports, and unsupported backend/transport combinations are rejected for
direct identify and control.

Normal live control must not scan, guess, infer, or auto-select a resource.
Resource enumeration happens only when a caller explicitly invokes the
resource-listing diagnostic. Listing a resource does not admit it for direct
control.

## SCPI Planning and Execution

Core owns command construction, ordering, writes, bounded queries,
verification, timeout/session handling, and cleanup for its operations.
Adapters own argument parsing, user confirmation, text/JSON/JSONL rendering,
process exit mapping, and adapter-specific artifacts.

Live state-changing operations configure only the selected channel. Waveform
and output configuration begin by turning that output off and leave it off.
Only an explicit output-on operation enables output. Identification, status,
error reads, and configuration do not implicitly enable output, and Core does
not use `*RST`.

For system VISA USB sessions, cleanup also attempts return-to-local before
closing the resource and resource manager. Cleanup failures remain structured
Core errors and must not be hidden by an adapter.

## Related Documentation

- [Core overview](README.md)
- [Supported models](supported-models.md)
- [CLI guide](../cli/README.md)
- [Testing guidelines](../testing-guidelines.md)
- [Worker contract](../contracts/wavegen-worker-contract.md)
