"""Strict parsing and exact support resolution for instrument identity."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType

from wavegen_tool_core.errors import MalformedIdnError, UnsupportedInstrumentError


CANONICAL_MANUFACTURER = "Keysight Technologies"
RECOGNIZED_MANUFACTURERS = (
    "Keysight Technologies",
    "Agilent Technologies",
)
CANONICAL_MODEL = "33521B"
CANONICAL_MODEL_ID = "keysight-33521b"


@dataclass(frozen=True)
class ModelInfo:
    """Canonical identity metadata for one registered model."""

    model_id: str
    canonical_model: str


_MODEL_REGISTRY = MappingProxyType(
    {
        "keysight-33510b": ModelInfo("keysight-33510b", "33510B"),
        "keysight-33512b": ModelInfo("keysight-33512b", "33512B"),
        CANONICAL_MODEL_ID: ModelInfo(CANONICAL_MODEL_ID, CANONICAL_MODEL),
    }
)
_LIVE_SUPPORTED_MODEL_IDS = frozenset({CANONICAL_MODEL_ID})


def model_info_for_model_id(model_id: str) -> ModelInfo | None:
    """Return metadata for an exact registered model ID."""

    return _MODEL_REGISTRY.get(model_id)


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
    """Resolve only exact registered identities that are supported for live use."""

    manufacturer_matches = normalize_manufacturer(identity.manufacturer) in (
        normalize_manufacturer(manufacturer)
        for manufacturer in RECOGNIZED_MANUFACTURERS
    )
    model_info = next(
        (
            registered_model
            for registered_model in _MODEL_REGISTRY.values()
            if normalize_model(identity.model)
            == normalize_model(registered_model.canonical_model)
        ),
        None,
    )
    if (
        not manufacturer_matches
        or model_info is None
        or model_info.model_id not in _LIVE_SUPPORTED_MODEL_IDS
    ):
        raise UnsupportedInstrumentError(
            "Unsupported instrument manufacturer/model combination "
            f"{identity.manufacturer!r}/{identity.model!r}.",
            identity=identity,
        )

    return replace(
        identity,
        model=model_info.canonical_model,
        canonical_model_id=model_info.model_id,
        model_supported=True,
    )
