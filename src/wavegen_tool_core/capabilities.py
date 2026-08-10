"""Exact capability profiles for registered waveform generator models."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class WavegenCapabilities:
    """Model-specific limits needed by implemented waveform features."""

    max_sine_square_pulse_noise_frequency_hz: float
    channel_count: int = 1


_CAPABILITY_REGISTRY = MappingProxyType(
    {
        "keysight-33510b": WavegenCapabilities(20_000_000.0, channel_count=2),
        "keysight-33512b": WavegenCapabilities(20_000_000.0, channel_count=2),
        "keysight-33521b": WavegenCapabilities(30_000_000.0, channel_count=1),
    }
)


def capabilities_for_model_id(model_id: str) -> WavegenCapabilities | None:
    """Return capabilities for an exact registered model ID."""

    return _CAPABILITY_REGISTRY.get(model_id)
