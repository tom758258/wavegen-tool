"""Strict parsing and exact support resolution for instrument identity."""

from __future__ import annotations

from dataclasses import dataclass, replace

from wavegen_tool_core.errors import MalformedIdnError, UnsupportedInstrumentError


CANONICAL_MANUFACTURER = "Keysight Technologies"
CANONICAL_MODEL = "33521B"
CANONICAL_MODEL_ID = "keysight-33521b"


@dataclass(frozen=True)
class InstrumentIdentity:
    """A parsed IDN response with optional exact support resolution."""

    manufacturer: str
    model: str
    serial: str
    firmware: str
    raw_response: str
    canonical_model_id: str | None = None
    model_supported: bool = False


def parse_idn(response: str) -> InstrumentIdentity:
    """Parse exactly four non-empty comma-separated IDN fields."""

    if not isinstance(response, str) or not response.strip():
        raise MalformedIdnError("Instrument returned an empty *IDN? response.")

    fields = response.strip().split(",")
    if len(fields) != 4:
        raise MalformedIdnError(
            f"Malformed *IDN? response: expected 4 comma-separated fields, received {len(fields)}."
        )

    manufacturer, model, serial, firmware = (field.strip() for field in fields)
    if not all((manufacturer, model, serial, firmware)):
        raise MalformedIdnError("Malformed *IDN? response: fields must not be empty.")

    return InstrumentIdentity(
        manufacturer=manufacturer,
        model=model,
        serial=serial,
        firmware=firmware,
        raw_response=response,
    )


def normalize_manufacturer(value: str) -> str:
    """Normalize case and ordinary whitespace for exact manufacturer matching."""

    return " ".join(value.split()).casefold()


def normalize_model(value: str) -> str:
    """Normalize case and surrounding whitespace without substring matching."""

    return value.strip().casefold()


def resolve_supported_identity(identity: InstrumentIdentity) -> InstrumentIdentity:
    """Resolve only the documented, recognized Keysight Technologies 33521B identity."""

    manufacturer_matches = (
        normalize_manufacturer(identity.manufacturer)
        == normalize_manufacturer(CANONICAL_MANUFACTURER)
    )
    model_matches = normalize_model(identity.model) == normalize_model(CANONICAL_MODEL)
    if not manufacturer_matches or not model_matches:
        raise UnsupportedInstrumentError(
            "Unsupported instrument manufacturer/model combination "
            f"{identity.manufacturer!r}/{identity.model!r}.",
            identity=identity,
        )

    return replace(
        identity,
        manufacturer=CANONICAL_MANUFACTURER,
        model=CANONICAL_MODEL,
        canonical_model_id=CANONICAL_MODEL_ID,
        model_supported=True,
    )
