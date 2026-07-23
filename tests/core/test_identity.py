import pytest

from wavegen_tool_core.errors import MalformedIdnError, UnsupportedInstrumentError
from wavegen_tool_core.identity import (
    CANONICAL_MODEL_ID,
    parse_idn,
    resolve_supported_identity,
)


VALID_IDN = "KEYSIGHT TECHNOLOGIES,33521B,MY00000000,1.00-0.00-0.00"


def test_valid_keysight_33521b_idn_resolves_exact_support():
    identity = resolve_supported_identity(parse_idn(VALID_IDN))

    assert identity.manufacturer == "Keysight Technologies"
    assert identity.model == "33521B"
    assert identity.serial == "MY00000000"
    assert identity.firmware == "1.00-0.00-0.00"
    assert identity.canonical_model_id == CANONICAL_MODEL_ID
    assert identity.model_supported is True
    assert not hasattr(identity, "supported")
    assert identity.raw_response == VALID_IDN


def test_leading_trailing_and_field_whitespace_is_normalized():
    raw_idn = "  keysight   technologies , 33521b , MY00000000 , 1.00-0.00-0.00  \n"

    identity = resolve_supported_identity(parse_idn(raw_idn))

    assert identity.manufacturer == "Keysight Technologies"
    assert identity.model == "33521B"
    assert identity.serial == "MY00000000"
    assert identity.firmware == "1.00-0.00-0.00"
    assert identity.raw_response == raw_idn


def test_manufacturer_mismatch_fails_closed():
    identity = parse_idn("Example Instruments,33521B,MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError) as error:
        resolve_supported_identity(identity)

    assert error.value.identity == identity


def test_model_mismatch_fails_closed_even_for_keysight():
    identity = parse_idn("Keysight Technologies,33522B,MY00000000,1.00")

    with pytest.raises(UnsupportedInstrumentError):
        resolve_supported_identity(identity)


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
