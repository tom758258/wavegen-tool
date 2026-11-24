"""Strict parsing and exact support resolution for instrument identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from wavegen_tool_core.errors import MalformedIdnError, UnsupportedInstrumentError


CANONICAL_MODEL = "33521B"
CANONICAL_MODEL_ID = "keysight-33521b"
SUPPORT_POLICY_MODE_PRODUCT = "product"
SUPPORT_POLICY_MODE_VALIDATION = "validation"

# Bind live policy to the vendor and canonical model independently of registry metadata.
_PRODUCT_LIVE_MODEL_IDENTITIES = MappingProxyType(
    {
        "keysight-33512b": ("keysight", "33512B"),
        CANONICAL_MODEL_ID: ("keysight", CANONICAL_MODEL),
    }
)
_VALIDATION_PENDING_LIVE_MODEL_IDENTITIES = MappingProxyType(
    {"keysight-33510b": ("keysight", "33510B")}
)


@dataclass(frozen=True)
class VendorInfo:
    """Canonical identity metadata for one registered vendor."""

    vendor_id: str
    display_name: str
    canonical_manufacturer: str
    manufacturer_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelInfo:
    """Canonical identity metadata for one registered model."""

    model_id: str
    canonical_model: str
    vendor_id: str
    display_name: str


def normalize_manufacturer(value: str) -> str:
    """Normalize case and ordinary whitespace for exact manufacturer matching."""

    return " ".join(value.split()).casefold()


def normalize_model(value: str) -> str:
    """Normalize case and surrounding whitespace without substring matching."""

    return value.strip().casefold()


def _build_vendor_registry(
    vendors: tuple[VendorInfo, ...],
) -> tuple[Mapping[str, VendorInfo], Mapping[str, str]]:
    by_id: dict[str, VendorInfo] = {}
    by_manufacturer: dict[str, str] = {}
    for vendor in vendors:
        if not vendor.vendor_id.strip() or vendor.vendor_id in by_id:
            raise RuntimeError(f"Empty or duplicate vendor ID: {vendor.vendor_id!r}.")
        by_id[vendor.vendor_id] = vendor
        for manufacturer in (vendor.canonical_manufacturer, *vendor.manufacturer_aliases):
            key = normalize_manufacturer(manufacturer)
            if not key or key in by_manufacturer:
                raise RuntimeError(f"Empty or duplicate manufacturer name: {manufacturer!r}.")
            by_manufacturer[key] = vendor.vendor_id
    return MappingProxyType(by_id), MappingProxyType(by_manufacturer)


def _build_model_registry(
    vendors: Mapping[str, VendorInfo],
    models: tuple[ModelInfo, ...],
) -> tuple[Mapping[str, ModelInfo], Mapping[tuple[str, str], ModelInfo]]:
    by_id: dict[str, ModelInfo] = {}
    by_vendor_and_name: dict[tuple[str, str], ModelInfo] = {}
    for model in models:
        if not model.model_id.strip() or model.model_id in by_id:
            raise RuntimeError(f"Empty or duplicate model ID: {model.model_id!r}.")
        if model.vendor_id not in vendors:
            raise RuntimeError(f"Model {model.model_id!r} references unknown vendor {model.vendor_id!r}.")
        live_identity = (
            _PRODUCT_LIVE_MODEL_IDENTITIES.get(model.model_id)
            or _VALIDATION_PENDING_LIVE_MODEL_IDENTITIES.get(model.model_id)
        )
        if live_identity is not None and (model.vendor_id, model.canonical_model) != live_identity:
            raise RuntimeError(f"Model {model.model_id!r} conflicts with its live policy identity.")
        name = normalize_model(model.canonical_model)
        key = (model.vendor_id, name)
        if not name or key in by_vendor_and_name:
            raise RuntimeError(
                f"Empty or duplicate model name for {model.vendor_id!r}: {model.canonical_model!r}."
            )
        by_id[model.model_id] = model
        by_vendor_and_name[key] = model
    return MappingProxyType(by_id), MappingProxyType(by_vendor_and_name)


_VENDOR_REGISTRY, _VENDOR_ID_BY_MANUFACTURER = _build_vendor_registry(
    (
        VendorInfo(
            vendor_id="keysight",
            display_name="Keysight",
            canonical_manufacturer="Keysight Technologies",
            manufacturer_aliases=("Agilent Technologies",),
        ),
    ),
)
CANONICAL_MANUFACTURER = _VENDOR_REGISTRY["keysight"].canonical_manufacturer
RECOGNIZED_MANUFACTURERS = tuple(
    manufacturer
    for vendor in _VENDOR_REGISTRY.values()
    for manufacturer in (vendor.canonical_manufacturer, *vendor.manufacturer_aliases)
)
_MODEL_REGISTRY, _MODEL_BY_VENDOR_AND_NAME = _build_model_registry(
    _VENDOR_REGISTRY,
    (
        ModelInfo("keysight-33510b", "33510B", "keysight", "Keysight 33510B"),
        ModelInfo("keysight-33512b", "33512B", "keysight", "Keysight 33512B"),
        ModelInfo(CANONICAL_MODEL_ID, CANONICAL_MODEL, "keysight", "Keysight 33521B"),
    ),
)
_LIVE_SUPPORTED_MODEL_IDS = frozenset(_PRODUCT_LIVE_MODEL_IDENTITIES)
_VALIDATION_PENDING_LIVE_MODEL_IDS = frozenset(_VALIDATION_PENDING_LIVE_MODEL_IDENTITIES)
_SUPPORT_POLICY_MODES = frozenset(
    {SUPPORT_POLICY_MODE_PRODUCT, SUPPORT_POLICY_MODE_VALIDATION}
)


def model_info_for_model_id(model_id: str) -> ModelInfo | None:
    """Return metadata for an exact registered model ID."""

    return _MODEL_REGISTRY.get(model_id)


def registered_model_ids() -> tuple[str, ...]:
    """Return exact registered model IDs in stable registry order."""

    return tuple(_MODEL_REGISTRY)


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


def _match_registered_model(identity: InstrumentIdentity) -> ModelInfo | None:
    vendor_id = _VENDOR_ID_BY_MANUFACTURER.get(normalize_manufacturer(identity.manufacturer))
    if vendor_id is None:
        return None
    return _MODEL_BY_VENDOR_AND_NAME.get((vendor_id, normalize_model(identity.model)))


def resolve_supported_identity(
    identity: InstrumentIdentity,
    *,
    support_policy_mode: str = SUPPORT_POLICY_MODE_PRODUCT,
) -> InstrumentIdentity:
    """Resolve only exact registered identities that are supported for live use."""

    if support_policy_mode not in _SUPPORT_POLICY_MODES:
        raise ValueError(f"unsupported support policy mode: {support_policy_mode}")

    admitted_model_ids = _LIVE_SUPPORTED_MODEL_IDS
    if support_policy_mode == SUPPORT_POLICY_MODE_VALIDATION:
        admitted_model_ids = admitted_model_ids | _VALIDATION_PENDING_LIVE_MODEL_IDS

    model_info = _match_registered_model(identity)
    if model_info is None or model_info.model_id not in admitted_model_ids:
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


def classify_discovered_identity(
    identity: InstrumentIdentity,
) -> tuple[str | None, str]:
    """Classify a parsed IDN without granting permission for live control."""

    model_info = _match_registered_model(identity)
    if model_info is None:
        return None, "unsupported"
    status = (
        "product_live_model" if model_info.model_id in _LIVE_SUPPORTED_MODEL_IDS else "registered_only"
    )
    return model_info.model_id, status
