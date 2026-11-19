"""Keysight volatile arbitrary-waveform and native sequence operations."""

from __future__ import annotations

from array import array
from collections.abc import Mapping
import csv
from dataclasses import dataclass
import json
import os
import re
import stat
import sys

from wavegen_tool_core.capabilities import WavegenCapabilities
from wavegen_tool_core.errors import (
    UnsupportedInstrumentError,
    VisaWriteError,
    WaveformParameterError,
    WaveformVerificationError,
)
from wavegen_tool_core.identity import SUPPORT_POLICY_MODE_PRODUCT
from wavegen_tool_core.simulator import SimulatedResourceManagerFactory
from wavegen_tool_core.visa import (
    SYSTEM_ERROR_QUERY,
    IdentificationResult,
    ResourceManagerFactory,
    VisaSession,
    _capabilities_for_identity,
    _check_independent_channel_guard,
    _format_scpi_number,
    _normalize_finite_number,
    _normalize_load,
    _parse_error_queue_entry,
    _parse_status_number,
    _parse_status_output,
    _require_hardware_free_model,
    _run_on_supported_instrument,
    _validate_channel,
    _validate_vpp_levels,
)

_MIN_POINTS = 8
_MAX_POINTS = 16_000_000
_MAX_CSV_ROW_BYTES = 256
_MAX_SEQUENCE_BYTES = 128 * 1024
_MAX_SEQUENCE_STEPS = 512
_MAX_REPEAT_COUNT = 1_000_000
_MAX_CATALOG_ENTRIES = 1024
_MAX_CATALOG_BYTES = 4 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,11}")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_PLAYBACK_FIELDS = frozenset({"name", "sample_rate_hz", "amplitude_vpp", "offset_v", "load"})
_SEQUENCE_FIELDS = _PLAYBACK_FIELDS | {"version", "trigger_source", "trigger_slope", "steps"}
_ARGUMENT_FIELDS = {
    "upload-arbitrary": {"name", "path"},
    "configure-arbitrary": _PLAYBACK_FIELDS,
    "configure-arbitrary-sequence": {"document"},
    "list-arbitrary-memory": set(),
    "clear-arbitrary-memory": {"confirm_clear"},
}
ARBITRARY_COMMANDS = frozenset(_ARGUMENT_FIELDS)


@dataclass(frozen=True)
class _Playback:
    name: str
    sample_rate_hz: float
    amplitude_vpp: float
    offset_v: float
    load: str


@dataclass(frozen=True)
class _Sequence:
    source: str
    slope: str | None
    steps: tuple[tuple[str, str, int], ...]

    def descriptor(self, name: str, waveforms: Mapping[str, str]) -> bytes:
        fields = [f'"{name}"']
        for waveform, mode, count in self.steps:
            fields.extend((f'"{waveforms[waveform]}"', str(count), mode, "lowAtStart", "4"))
        return ",".join(fields).encode("ascii")


def _open_regular(path: str, flags: int) -> int:
    # Avoid waiting on a named pipe or treating an instrument device as a local file.
    fd = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Expected a regular file.")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_samples(path: object) -> array:
    samples = array("f")
    try:
        with open(path, "rb", opener=_open_regular) as stream:
            while line := stream.readline(_MAX_CSV_ROW_BYTES + 1):
                if len(samples) == _MAX_POINTS:
                    raise WaveformParameterError("Arbitrary CSV exceeds the 16 MSa input limit.")
                row = len(samples) + 1
                if len(line) > _MAX_CSV_ROW_BYTES:
                    raise WaveformParameterError(f"Arbitrary CSV row {row} is too long.")
                encoding = "utf-8-sig" if row == 1 else "utf-8"
                fields = next(csv.reader([line.decode(encoding)], strict=True))
                if len(fields) != 1 or not _NUMBER.fullmatch(fields[0].strip()):
                    raise WaveformParameterError(
                        f"Arbitrary CSV row {row} must contain one finite number."
                    )
                value = float(fields[0])
                if not -1.0 <= value <= 1.0:
                    raise WaveformParameterError(f"Arbitrary CSV row {row} must be within [-1, 1].")
                samples.append(value)
    except (OSError, TypeError, ValueError, csv.Error) as exc:
        raise WaveformParameterError("Could not read a valid UTF-8 arbitrary CSV file.") from exc
    if len(samples) < _MIN_POINTS:
        raise WaveformParameterError("Arbitrary CSV requires at least 8 samples.")
    return samples


def read_arbitrary_sequence_file(path: str | os.PathLike[str]) -> dict[str, object]:
    """Read the bounded JSON document used by the Direct CLI."""
    try:
        with open(path, "rb", opener=_open_regular) as stream:
            raw = stream.read(_MAX_SEQUENCE_BYTES + 1)
        if len(raw) > _MAX_SEQUENCE_BYTES:
            raise WaveformParameterError("Arbitrary sequence JSON is too large.")
        document = json.loads(raw.decode("utf-8-sig"))
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        raise WaveformParameterError("Could not read a valid UTF-8 sequence JSON file.") from exc
    if not isinstance(document, dict):
        raise WaveformParameterError("Sequence document must be a JSON object.")
    return document


def _name(value: object) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise WaveformParameterError(
            "Arbitrary name must start with an ASCII letter and contain at most "
            "12 letters, digits or underscores."
        )
    return value


def _playback(arguments: Mapping[str, object]) -> _Playback:
    name = _name(arguments.get("name"))
    rate = _normalize_finite_number(
        arguments.get("sample_rate_hz"), "sample rate", waveform="Arbitrary"
    )
    load = _normalize_load(arguments.get("load", "50"), waveform="Arbitrary")
    amplitude = _normalize_finite_number(
        arguments.get("amplitude_vpp"), "amplitude", waveform="Arbitrary"
    )
    offset = _normalize_finite_number(arguments.get("offset_v", 0), "offset", waveform="Arbitrary")
    _validate_vpp_levels(amplitude, offset, load, "Arbitrary")
    if rate < 1e-6:
        raise WaveformParameterError("Arbitrary sample rate must be at least 0.000001 Sa/s.")
    return _Playback(name, rate, amplitude, offset, load)


def _sequence(document: object) -> tuple[_Playback, _Sequence]:
    if not isinstance(document, Mapping) or document.keys() - _SEQUENCE_FIELDS:
        raise WaveformParameterError("Sequence document has invalid or unknown fields.")
    if type(document.get("version")) is not int or document["version"] != 1:
        raise WaveformParameterError("Sequence version must be the integer 1.")
    playback = _playback(document)
    source = document.get("trigger_source")
    if source not in ("bus", "external"):
        raise WaveformParameterError("Sequence trigger_source must be bus or external.")
    slope = document.get("trigger_slope", "positive" if source == "external" else None)
    if source == "bus" and "trigger_slope" in document:
        raise WaveformParameterError("Sequence trigger_slope requires the external trigger source.")
    if source == "external" and slope not in ("positive", "negative"):
        raise WaveformParameterError(
            "External sequence trigger_slope must be positive or negative."
        )
    steps = document.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= _MAX_SEQUENCE_STEPS:
        raise WaveformParameterError("Sequence must contain 1 to 512 steps.")
    normalized = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, Mapping) or step.keys() - {
            "waveform",
            "play_control",
            "repeat_count",
        }:
            raise WaveformParameterError(f"Sequence step {index} has invalid or unknown fields.")
        waveform = _name(step.get("waveform"))
        mode = step.get("play_control")
        if mode not in ("once", "repeat", "onceWaitTrig", "repeatTilTrig"):
            raise WaveformParameterError(f"Sequence step {index} has an unsupported play_control.")
        count = step.get("repeat_count")
        if mode == "repeat":
            if type(count) is not int or not 1 <= count <= _MAX_REPEAT_COUNT:
                raise WaveformParameterError(
                    f"Sequence step {index} requires repeat_count from 1 to 1000000."
                )
        elif "repeat_count" in step:
            raise WaveformParameterError(
                f"Sequence step {index} only supports repeat_count with repeat."
            )
        normalized.append((waveform, mode, count if mode == "repeat" else 0))
    return playback, _Sequence(source, slope, tuple(normalized))


def _check_capabilities(
    channel: int,
    capabilities: WavegenCapabilities,
    model: str,
    playback: _Playback | None,
) -> None:
    _validate_channel(channel, capabilities, model)
    limit = capabilities.max_arbitrary_sample_rate_hz
    if limit is None:
        raise WaveformParameterError(
            f"Keysight {model} does not support standard Arbitrary operations."
        )
    if playback is not None and playback.sample_rate_hz > limit:
        raise WaveformParameterError(
            f"Arbitrary sample rate exceeds {model}'s {_format_scpi_number(limit)} Sa/s limit."
        )


def _catalog(session: VisaSession, source: str) -> tuple[str, ...]:
    try:
        raw = session.query(f"{source}:DATA:VOLatile:CATalog?").strip()
        if len(raw) > _MAX_CATALOG_BYTES or "\n" in raw or "\r" in raw:
            raise ValueError
        entries = next(csv.reader([raw], strict=True))
        if entries == [""]:
            return ()
        if any(not 1 <= len(name) <= 255 or not name.isprintable() for name in entries):
            raise ValueError
        return tuple(entries)
    except Exception as exc:
        raise WaveformVerificationError("Could not read the volatile Arbitrary catalog.") from exc


def _lookup(catalog: tuple[str, ...], name: str, *, waveform_only: bool = False) -> str | None:
    accepted = {name.casefold() + suffix for suffix in ("", ".arb", ".barb", ".seq")}
    if waveform_only:
        accepted.remove(name.casefold() + ".seq")
    # Catalog names can include the original instrument drive and directory.
    matches = [
        entry for entry in catalog if re.split(r"[:/\\]", entry)[-1].casefold() in accepted
    ]
    if len(matches) > 1:
        raise WaveformVerificationError(f"Arbitrary memory contains ambiguous matches for {name}.")
    entry = next(iter(matches), None)
    if entry is not None and (not entry.isascii() or '"' in entry or ";" in entry):
        raise WaveformVerificationError(
            f"Arbitrary memory contains an unsafe catalog name for {name}."
        )
    return entry


def _count_query(session: VisaSession, command: str) -> int:
    try:
        response = session.query(command).strip()
        if not re.fullmatch(r"\+?[0-9]{1,10}", response):
            raise ValueError
        count = int(response)
        if count > _MAX_POINTS:
            raise ValueError
        return count
    except Exception as exc:
        raise WaveformVerificationError(
            "Could not verify the instrument's Arbitrary memory count."
        ) from exc


def _require_no_error(session: VisaSession, *, before_write: bool = False) -> None:
    error = _parse_error_queue_entry(session.query(SYSTEM_ERROR_QUERY))
    if error.code:
        condition = "already contained" if before_write else "reported"
        raise WaveformVerificationError(f"Instrument {condition} SCPI error {error.code}.")


def _complete_write(session: VisaSession) -> None:
    if session.query("*OPC?").strip() != "1":
        raise WaveformVerificationError("Arbitrary operation did not confirm completion.")
    _require_no_error(session)


def _require_output_off(session: VisaSession, channel: int) -> None:
    if _parse_status_output(session.query(f"OUTPut{channel}?")) != "off":
        raise WaveformVerificationError("Selected output must be OFF for this Arbitrary operation.")


def _write_block(session: VisaSession, prefix: str, payload: bytes) -> None:
    length = str(len(payload))
    header = f"{prefix}#{len(length)}{length}".encode("ascii")
    termination = (session.write_termination or "\n").encode("ascii")
    message = header + payload + termination
    if session.write_raw(message) != len(message):
        raise OSError("Short VISA binary write.")


def _playback_commands(
    channel: int,
    name: str,
    playback: _Playback,
    sequence: _Sequence | None,
) -> tuple[str, ...]:
    source = f"SOURce{channel}"
    trigger = f"TRIGger{channel}"
    commands = (
        *(
            f"{source}:{mode}:STATe OFF"
            for mode in ("AM", "FM", "PM", "FSKey", "BPSK", "PWM", "BURSt", "SUM")
        ),
        f"{source}:FREQuency:MODE CW",
        f'{source}:FUNCtion:ARBitrary "{name}"',
        f"{source}:FUNCtion ARB",
        f"{source}:FUNCtion:ARBitrary:ADVance SRATe",
        f"OUTPut{channel}:LOAD {'50' if playback.load == '50' else 'INF'}",
        f"{source}:VOLTage:UNIT VPP",
        f"{source}:VOLTage {_format_scpi_number(playback.amplitude_vpp)}",
        f"{source}:VOLTage:OFFSet {_format_scpi_number(playback.offset_v)}",
        f"{source}:FUNCtion:ARBitrary:SRATe {_format_scpi_number(playback.sample_rate_hz)}",
    )
    if sequence is None:
        return commands
    if sequence.source == "bus":
        return (*commands, f"{trigger}:SOURce BUS")
    slope = "POSitive" if sequence.slope == "positive" else "NEGative"
    return (*commands, f"{trigger}:SOURce EXTernal", f"{trigger}:SLOPe {slope}")


def _plan(
    command: str,
    channel: int,
    source: str,
    name: str | None,
    playback: _Playback | None,
    sequence: _Sequence | None,
) -> tuple[str, ...]:
    if command == "list-arbitrary-memory":
        return (f"{source}:DATA:VOLatile:CATalog?", f"{source}:DATA:VOLatile:FREe?")
    if command == "upload-arbitrary":
        return (f"{source}:DATA:ARBitrary {name},<Float32 definite-length block>",)
    if command == "clear-arbitrary-memory":
        return (f"OUTPut{channel} OFF", f"{source}:DATA:VOLatile:CLEar")
    descriptor = (f"{source}:DATA:SEQuence <definite-length descriptor>",) if sequence else ()
    return (
        f"OUTPut{channel} OFF",
        *descriptor,
        *_playback_commands(channel, name, playback, sequence),
    )


def _upload(
    session: VisaSession,
    source: str,
    channel: int,
    name: str,
    samples: array,
) -> None:
    free = _count_query(session, f"{source}:DATA:VOLatile:FREe?")
    allocated_points = (len(samples) + 127) // 128 * 128
    if allocated_points > free:
        raise WaveformParameterError(
            f"Arbitrary upload requires {allocated_points} allocated points but only {free} are available."
        )
    _require_output_off(session, channel)
    order = session.query("FORMat:BORDer?").strip().upper()
    if order not in {"NORM", "NORMAL", "SWAP", "SWAPPED"}:
        raise WaveformVerificationError("Could not resolve instrument binary byte order.")
    little_endian = order.startswith("SWAP")
    if (sys.byteorder == "little") != little_endian:
        samples.byteswap()
    _require_no_error(session, before_write=True)
    try:
        _write_block(session, f"{source}:DATA:ARBitrary {name},", samples.tobytes())
        _complete_write(session)
        if _count_query(session, f'{source}:DATA:ATTRibute:POINts? "{name}"') != len(samples):
            raise WaveformVerificationError("Uploaded point count did not match.")
    except Exception as exc:
        raise VisaWriteError(
            "Arbitrary upload failed or could not be verified. Volatile memory state may be uncertain.",
            output_state="off",
        ) from exc


def _apply_playback(
    session: VisaSession,
    source: str,
    channel: int,
    catalog: tuple[str, ...],
    playback: _Playback,
    sequence: _Sequence | None,
) -> None:
    name = playback.name
    if sequence is None:
        name = _lookup(catalog, name, waveform_only=True)
        if name is None:
            raise WaveformParameterError(
                "The named Arbitrary waveform is missing from the selected channel."
            )
    else:
        waveforms = {}
        for waveform in dict.fromkeys(step[0] for step in sequence.steps):
            entry = _lookup(catalog, waveform, waveform_only=True)
            if entry is None:
                raise WaveformParameterError(
                    f"Sequence waveform {waveform} is missing from the selected channel."
                )
            if _count_query(session, f'{source}:DATA:ATTRibute:POINts? "{entry}"') < _MIN_POINTS:
                raise WaveformParameterError(f"Sequence waveform {waveform} has too few samples.")
            waveforms[waveform] = entry
    maximum = _parse_status_number(
        session.query(f"{source}:FUNCtion:ARBitrary:SRATe? MAXimum"),
        "Arbitrary maximum sample rate",
    )
    if playback.sample_rate_hz > maximum:
        raise WaveformParameterError(
            "Arbitrary sample rate exceeds the instrument's current limit."
        )
    _require_no_error(session, before_write=True)
    session.write(f"OUTPut{channel} OFF")
    _require_output_off(session, channel)
    if sequence is not None:
        try:
            _write_block(session, f"{source}:DATA:SEQuence ", sequence.descriptor(name, waveforms))
            _complete_write(session)
        except Exception as exc:
            raise VisaWriteError(
                "Arbitrary sequence creation failed or could not be verified. Volatile memory state may be uncertain.",
                output_state="off",
            ) from exc
    for write in _playback_commands(channel, name, playback, sequence):
        session.write(write)
    _complete_write(session)
    _require_output_off(session, channel)


def run_arbitrary(
    command: str,
    arguments: Mapping[str, object],
    *,
    model: str | None = None,
    resource: str | None = None,
    backend: str | None = None,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
    expected_model_id: str | None = None,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> dict[str, object]:
    """Plan or simulate Arbitrary operations, or execute them in validation mode."""
    if command not in ARBITRARY_COMMANDS:
        raise WaveformParameterError("Unknown Arbitrary operation.")
    if (model is None) == (resource is None):
        raise WaveformParameterError(
            "Arbitrary operations require either a planning model or a VISA resource."
        )
    if arguments.keys() - (_ARGUMENT_FIELDS[command] | {"channel"}):
        raise WaveformParameterError("Unsupported argument for this Arbitrary operation.")
    if (
        resource is not None
        and support_policy_mode == SUPPORT_POLICY_MODE_PRODUCT
        and not isinstance(resource_manager_factory, SimulatedResourceManagerFactory)
    ):
        raise UnsupportedInstrumentError(
            "Arbitrary operations are outside Product Live feature scope."
        )
    channel = _validate_channel(arguments.get("channel", 1))
    source = f"SOURce{channel}"
    playback = sequence = name = None
    if command == "configure-arbitrary":
        playback = _playback(arguments)
        name = playback.name
    elif command == "configure-arbitrary-sequence":
        playback, sequence = _sequence(arguments.get("document"))
        name = playback.name
    elif command == "upload-arbitrary":
        name = _name(arguments.get("name"))
    elif command == "clear-arbitrary-memory" and arguments.get("confirm_clear") is not True:
        raise WaveformParameterError(
            "Clearing volatile Arbitrary memory requires explicit confirmation."
        )

    if model is not None:
        model_info, capabilities = _require_hardware_free_model(model, "Arbitrary")
        _check_capabilities(channel, capabilities, model_info.canonical_model, playback)

    samples = _read_samples(arguments.get("path")) if command == "upload-arbitrary" else None
    result = {"channel": channel, "status": "planned"}
    if name is not None:
        result["name"] = name
    if samples is not None:
        result["sample_count"] = len(samples)
    if playback is not None:
        result.update(
            sample_rate_hz=playback.sample_rate_hz,
            amplitude_vpp=playback.amplitude_vpp,
            offset_v=playback.offset_v,
            load=playback.load,
            output_state="off",
        )
    if sequence is not None:
        result.update(step_count=len(sequence.steps), trigger_source=sequence.source)
        if sequence.slope is not None:
            result["trigger_slope"] = sequence.slope
    if command == "clear-arbitrary-memory":
        result["output_state"] = "off"

    if model is not None:
        return dict(
            result,
            model=model_info.canonical_model,
            canonical_model_id=model_info.model_id,
            executed=False,
            commands=_plan(command, channel, source, name, playback, sequence),
        )

    def operate(session: VisaSession, context: IdentificationResult) -> None:
        capabilities = _capabilities_for_identity(context.identity)
        _check_capabilities(channel, capabilities, context.identity.model, playback)
        if command == "list-arbitrary-memory":
            catalog = _catalog(session, source)
            result.update(
                names=catalog[:_MAX_CATALOG_ENTRIES],
                catalog_truncated=len(catalog) > _MAX_CATALOG_ENTRIES,
                free_points=_count_query(session, f"{source}:DATA:VOLatile:FREe?"),
            )
            return
        _check_independent_channel_guard(session, capabilities, context)
        if command == "clear-arbitrary-memory":
            _require_no_error(session, before_write=True)
            session.write(f"OUTPut{channel} OFF")
            _require_output_off(session, channel)
            session.write(f"{source}:DATA:VOLatile:CLEar")
            _complete_write(session)
            _require_output_off(session, channel)
            return
        catalog = _catalog(session, source)
        if (samples is not None or sequence is not None) and _lookup(catalog, name) is not None:
            raise WaveformParameterError(
                f"Arbitrary name {name} already exists. Explicitly clear volatile memory before reusing it."
            )
        if samples is not None:
            _upload(session, source, channel, name, samples)
            result["output_state"] = "off"
        else:
            _apply_playback(session, source, channel, catalog, playback, sequence)

    identity, _ = _run_on_supported_instrument(
        resource,
        backend,
        operate,
        support_policy_mode=support_policy_mode,
        expected_model_id=expected_model_id,
        resource_manager_factory=resource_manager_factory,
        output_state_after_operation=result.get("output_state"),
    )
    return dict(
        result,
        status="completed",
        executed=True,
        model=identity.identity.model,
        canonical_model_id=identity.identity.canonical_model_id,
        manufacturer=identity.identity.manufacturer,
        backend=identity.backend,
        transport=identity.transport,
    )
