import pytest

import wavegen_tool_core.identity as identity_module
from wavegen_tool_core.errors import MalformedIdnError, UnsupportedInstrumentError
from wavegen_tool_core.identity import (
    CANONICAL_MANUFACTURER,
    CANONICAL_MODEL_ID,
    RECOGNIZED_MANUFACTURERS,
    SUPPORT_POLICY_MODE_VALIDATION,
    ModelInfo,
    VendorInfo,
    classify_discovered_identity,
    model_info_for_model_id,
    parse_idn,
    registered_model_ids,
    resolve_supported_identity,
)


VALID_IDN = "KEYSIGHT TECHNOLOGIES,33521B,MY00000000,1.00-0.00-0.00"
AGILENT_IDN = "Agilent Technologies,33521B,MY00000001,1.00-0.00-0.00"


@pytest.mark.parametrize(
    ("model_id", "canonical_model"),
    [
        ("keysight-33510b", "33510B"),
        ("keysight-33512b", "33512B"),
        ("keysight-33521b", "33521B"),
    ],
)
def test_registered_model_lookup_is_exact(model_id, canonical_model):
    model_info = model_info_for_model_id(model_id)

    assert model_info is not None
    assert model_info.model_id == model_id
    assert model_info.canonical_model == canonical_model
    assert model_info.vendor_id == "keysight"
    assert model_info.display_name == f"Keysight {canonical_model}"
    assert model_info_for_model_id(model_id.upper()) is None


def test_identity_registry_preserves_legacy_manufacturer_names_and_model_order():
    assert CANONICAL_MANUFACTURER == "Keysight Technologies"
    assert RECOGNIZED_MANUFACTURERS == ("Keysight Technologies", "Agilent Technologies")
    assert registered_model_ids() == ("keysight-33510b", "keysight-33512b", "keysight-33521b")


def test_idn_matching_uses_vendor_and_model_without_expanding_live_policy(monkeypatch):
    example = VendorInfo("example", "Example", "Example Instruments")
    vendors, manufacturer_index = identity_module._build_vendor_registry(
        (*identity_module._VENDOR_REGISTRY.values(), example)
    )
    _, model_index = identity_module._build_model_registry(
        vendors,
        (
            *identity_module._MODEL_REGISTRY.values(),
            ModelInfo("shared-33521b", "33521B", example.vendor_id, "Example 33521B"),
        ),
    )
    monkeypatch.setattr(identity_module, "_VENDOR_ID_BY_MANUFACTURER", manufacturer_index)
    monkeypatch.setattr(identity_module, "_MODEL_BY_VENDOR_AND_NAME", model_index)

    keysight = parse_idn(VALID_IDN)
    shared_model = parse_idn("Example Instruments,33521B,EXAMPLE-SERIAL,1.00")
    wrong_vendor = parse_idn("Example Instruments,33512B,EXAMPLE-SERIAL,1.00")

    assert classify_discovered_identity(keysight) == (CANONICAL_MODEL_ID, "product_live_model")
    assert classify_discovered_identity(shared_model) == ("shared-33521b", "registered_only")
    assert classify_discovered_identity(wrong_vendor) == (None, "unsupported")
    assert resolve_supported_identity(keysight).canonical_model_id == CANONICAL_MODEL_ID
    for identity in (shared_model, wrong_vendor):
        with pytest.raises(UnsupportedInstrumentError):
            resolve_supported_identity(identity)


@pytest.mark.parametrize(
    "vendor",
    [
        pytest.param(VendorInfo("keysight", "Example", "Example Instruments"), id="duplicate-id"),
        pytest.param(
            VendorInfo("example", "Example", "Example Instruments", (" agilent  technologies ",)),
            id="conflicting-manufacturer",
        ),
        pytest.param(
            VendorInfo("example", "Example", "Example Instruments", (" example instruments ",)),
            id="duplicate-manufacturer",
        ),
    ],
)
def test_vendor_registry_rejects_conflicting_entries(vendor):
    with pytest.raises(RuntimeError):
        identity_module._build_vendor_registry((*identity_module._VENDOR_REGISTRY.values(), vendor))


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(
            ModelInfo(CANONICAL_MODEL_ID, "Different Model", "keysight", "Example"),
            id="duplicate-id",
        ),
        pytest.param(
            ModelInfo("example-model", " 33521b ", "keysight", "Example"), id="duplicate-name"
        ),
        pytest.param(
            ModelInfo("example-model", "Different Model", "unknown", "Example"), id="unknown-vendor"
        ),
    ],
)
def test_model_registry_rejects_conflicting_entries(model):
    with pytest.raises(RuntimeError):
        identity_module._build_model_registry(
            identity_module._VENDOR_REGISTRY, (*identity_module._MODEL_REGISTRY.values(), model)
        )


def test_valid_keysight_33521b_idn_resolves_exact_support():
    identity = resolve_supported_identity(parse_idn(VALID_IDN))

    assert identity.manufacturer == "KEYSIGHT TECHNOLOGIES"
    assert identity.model == "33521B"
    assert identity.serial == "MY00000000"
    assert identity.firmware == "1.00-0.00-0.00"
    assert identity.canonical_model_id == CANONICAL_MODEL_ID
    assert identity.model_supported is True
    assert not hasattr(identity, "supported")
    assert identity.raw_response == VALID_IDN


def test_matching_normalizes_case_and_ordinary_whitespace_but_preserves_reported_value():
    raw_idn = "  keysight   technologies , 33521b , MY00000000 , 1.00-0.00-0.00  \n"

    identity = resolve_supported_identity(parse_idn(raw_idn))

    assert identity.manufacturer == "keysight   technologies"
    assert identity.model == "33521B"
    assert identity.serial == "MY00000000"
    assert identity.firmware == "1.00-0.00-0.00"
    assert identity.raw_response == raw_idn


def test_agilent_technologies_33521b_resolves_and_preserves_manufacturer():
    identity = resolve_supported_identity(parse_idn(AGILENT_IDN))

    assert identity.manufacturer == "Agilent Technologies"
    assert identity.model == "33521B"
    assert identity.canonical_model_id == CANONICAL_MODEL_ID
    assert identity.model_supported is True


@pytest.mark.parametrize(
    "manufacturer",
    [
        "Agilent",
        "HP",
        "Hewlett-Packard",
        "Agilent Technologies Extra",
        "Prefix Agilent Technologies",
    ],
)
def test_manufacturer_aliases_are_exact_not_prefix_substring_or_fuzzy(manufacturer):
    identity = parse_idn(f"{manufacturer},33521B,MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError):
        resolve_supported_identity(identity)


def test_manufacturer_mismatch_fails_closed():
    identity = parse_idn("Example Instruments,33521B,MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError) as error:
        resolve_supported_identity(identity)

    assert error.value.identity == identity


def test_model_mismatch_fails_closed_even_for_keysight():
    identity = parse_idn("Keysight Technologies,33522B,MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError):
        resolve_supported_identity(identity)


def test_agilent_other_model_fails_closed():
    identity = parse_idn("Agilent Technologies,33520B,MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError):
        resolve_supported_identity(identity)


def test_registered_but_not_live_supported_33510b_fails_closed():
    identity = parse_idn("Keysight Technologies,33510B,MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError):
        resolve_supported_identity(identity)


@pytest.mark.parametrize(
    "manufacturer",
    ["Keysight Technologies", "Agilent Technologies"],
)
def test_product_policy_accepts_exact_33512b_identity(manufacturer):
    identity = resolve_supported_identity(
        parse_idn(f"{manufacturer},33512B,MY00000000,1.00")
    )

    assert identity.manufacturer == manufacturer
    assert identity.model == "33512B"
    assert identity.canonical_model_id == "keysight-33512b"
    assert identity.model_supported is True


def test_validation_policy_accepts_product_supported_33512b_identity():
    identity = resolve_supported_identity(
        parse_idn("Keysight Technologies,33512B,MY00000000,1.00"),
        support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
    )

    assert identity.model == "33512B"
    assert identity.canonical_model_id == "keysight-33512b"
    assert identity.model_supported is True


def test_validation_policy_accepts_candidate_33510b_identity():
    identity = resolve_supported_identity(
        parse_idn("Keysight Technologies,33510B,MY00000000,1.00"),
        support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
    )

    assert identity.model == "33510B"
    assert identity.canonical_model_id == "keysight-33510b"
    assert identity.model_supported is True


@pytest.mark.parametrize("model", ["33522B"])
def test_validation_policy_rejects_unadmitted_or_unknown_models(model):
    identity = parse_idn(
        f"Keysight Technologies,{model},MY00000000,1.00"
    )

    with pytest.raises(UnsupportedInstrumentError):
        resolve_supported_identity(
            identity,
            support_policy_mode=SUPPORT_POLICY_MODE_VALIDATION,
        )


@pytest.mark.parametrize(
    "raw_idn",
    [
        "",
        "   ",
        "Keysight Technologies,33521B,MY00000000",
        "Keysight Technologies,33521B,MY00000000,1.00,EXTRA",
        "Keysight Technologies,,MY00000000,1.00",
        ",33521B,MY00000000,1.00",
    ],
)
def test_malformed_idn_is_rejected_without_guessing(raw_idn):
    with pytest.raises(MalformedIdnError):
        parse_idn(raw_idn)


@pytest.mark.parametrize("model", ["33521A", "33522B", "33600A", "X33521B"])
def test_unsupported_models_are_not_matched_by_family_or_substring(model):
    identity = parse_idn(f"Keysight Technologies,{model},MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError):
        resolve_supported_identity(identity)


@pytest.mark.parametrize(
    ("manufacturer", "model", "expected_id", "expected_status"),
    [
        ("Keysight Technologies", "33521B", "keysight-33521b", "product_live_model"),
        ("Agilent Technologies", "33512B", "keysight-33512b", "product_live_model"),
        ("Keysight Technologies", "33510B", "keysight-33510b", "registered_only"),
        ("Keysight Technologies", "34465A", None, "unsupported"),
        ("Other Manufacturer", "33521B", None, "unsupported"),
        ("Keysight Technologies", "X33521B", None, "unsupported"),
        (" keysight   technologies ", " 33521b ", "keysight-33521b", "product_live_model"),
    ],
)
def test_discovery_classifies_exact_registered_models_without_admitting_control(
    manufacturer, model, expected_id, expected_status
):
    identity = parse_idn(f"{manufacturer},{model},EXAMPLE-SERIAL,1.00")

    model_id, status = classify_discovered_identity(identity)

    assert (model_id, status) == (expected_id, expected_status)
