import pytest

from wavegen_tool_core.capabilities import capabilities_for_model_id


@pytest.mark.parametrize(
    ("model_id", "maximum_frequency_hz", "expected_channel_count"),
    [
        ("keysight-33510b", 20_000_000.0, 2),
        ("keysight-33512b", 20_000_000.0, 2),
        ("keysight-33521b", 30_000_000.0, 1),
    ],
)
def test_registered_models_have_expected_frequency_capabilities(
    model_id,
    maximum_frequency_hz,
    expected_channel_count,
):
    capabilities = capabilities_for_model_id(model_id)

    assert capabilities is not None
    assert (
        capabilities.max_sine_square_pulse_noise_frequency_hz
        == maximum_frequency_hz
    )
    assert capabilities.channel_count == expected_channel_count


@pytest.mark.parametrize(
    "model_id",
    ["keysight-33512", "keysight-33500b", "33512B"],
)
def test_unknown_or_approximate_model_ids_have_no_capability_profile(model_id):
    assert capabilities_for_model_id(model_id) is None
