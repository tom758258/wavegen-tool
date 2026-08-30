# Supported Models

This page is the authoritative user-facing Wavegen Tool support matrix.
Registration, hardware-free planning, simulation, contributor validation, and
Product Live support are distinct.

## Support Matrix

| Model | Model ID | Channels | Frequency capability | Registered | Dry-run | Simulator | Product Live |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 33510B | `keysight-33510b` | 2 | 20 MHz | Yes | Yes | Yes | No |
| 33512B | `keysight-33512b` | 2 | 20 MHz | Yes | Yes | Yes | Yes |
| 33521B | `keysight-33521b` | 1 | 30 MHz | Yes | Yes | Yes | Yes |

The frequency value is the Core registry's shared maximum for sine, square,
pulse, and noise operations. Each waveform, modulation, burst, and sweep path
also applies its own request-specific limits. See the [CLI guide](../cli/README.md)
for command details.

The 33510B has a contributor-only validation route for its exact registered
profile. It is available only through the explicit contributor workflow and
does not alter the default Product policy or make the model Product Live.
Evidence handling and support decisions are documented in
[Contributing](../CONTRIBUTING.md).

## Exact Live Identity

Core recognizes these manufacturer fields:

- `Keysight Technologies`
- `Agilent Technologies`

The model must resolve exactly to 33510B, 33512B, or 33521B. Manufacturer
matching ignores case and collapses ordinary whitespace; model matching ignores
case and surrounding whitespace. Neither uses prefix, substring, fuzzy, alias,
resource-name, or guessed matching. Normal Product Live admission then limits
the detected model to 33512B or 33521B.

The manufacturer reported by a supported live instrument is preserved in the
identity result while the model is normalized to its canonical registered
form.

## Hardware-free Support

All three registered profiles can be selected explicitly for supported dry-run
and standalone simulator configuration paths. The two-channel 33510B and
33512B profiles maintain independent simulated channel state. Channel 2 is
rejected for the one-channel 33521B in dry-run, simulator, and live paths.

Dry-run does not execute VISA I/O. Simulator results are deterministic test
behavior, not evidence that a physical model, backend, connection, or command
family has been validated.

## Product Live Connection Scope

Direct Product Live identification and control use an explicit USB or
TCPIP/LAN VISA resource and the following backend admission:

| Backend selector | Core identity | USB admission | TCPIP/LAN admission |
| --- | --- | ---: | ---: |
| omitted or `system` | `system_visa` | Accepted | Accepted |
| `@py` | `pyvisa_py` | Rejected | Accepted |
| `@bt` | `pyvisa_bt` | Rejected | Rejected |

These entries describe Core transport/backend admission. Actual live operation
also requires an admitted model, command, channel, and safety state; the table
does not claim a separate hardware-validation record for every combination.
`@bt` is a recognized backend identity but has no Product-open or
validation-open live scope. Direct identify/control rejects GPIB, ASRL, PXI,
VXI, unknown transports, and unsupported backend/transport combinations.
There is no automatic backend fallback.

Normal live control does not scan, infer, guess, or auto-select a resource.
Resource discovery occurs only through the explicit `list-resources`
diagnostic command and does not grant direct-control support.

## Product Live Feature Scope

For admitted 33512B and 33521B resources, Product Live uses the same Core
request validation and safety rules documented for:

- static sine, square, ramp, triangle, pulse, DC, noise, and PRBS;
- supported AM, FM, PM, FSK, BPSK, PWM, and Sum carrier combinations;
- Counted Burst and Gated Burst on supported carriers;
- sine, square, ramp, and triangle ordinary and List Sweeps;
- selected-channel status, output configuration, and output control;
- bounded error-queue reads, identity, and instrument-wide bus trigger.

This overview does not remove command-specific limits. An invalid carrier,
option combination, channel, value, trigger source, or safety state still fails
closed. Product Live means the model is admitted by current Core policy; it
does not claim that every command/backend combination has been independently
exercised on hardware.

## Selected-channel Safety

On two-channel live instruments, Core requires frequency coupling, voltage
coupling, Channel 1 tracking, and Channel 2 tracking to be reported off before
an independent state-changing operation. Core does not disable them
automatically.

Configuration leaves the selected output off. Output-on remains an explicit
operation. The tool does not use `*RST` as part of normal operation.

## Adapter Exposure

The Direct CLI exposes the broader Core-backed waveform, modulation, burst,
sweep, List Sweep, output, trigger, and diagnostic surface documented in the
[CLI guide](../cli/README.md).

The local Worker is a narrower adapter surface, not a second model support
matrix. It uses Core's registered models for dry-run and simulator planning and
Core's Product policy for live admission. Therefore 33512B and 33521B are
Product Live within the existing Worker command surface, while 33510B remains
hardware-free only.

Worker model admission does not broaden Worker command exposure. Only commands
listed in the authoritative
[Worker contract](../contracts/wavegen-worker-contract.md) are accepted; Direct
Core or CLI command availability must not be used to infer Worker commands.
