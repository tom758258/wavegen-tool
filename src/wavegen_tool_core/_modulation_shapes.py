"""Keysight internal modulation shape names and SCPI values."""

MODULATION_SHAPE_SCPI = {
    "sine": "SINusoid",
    "square": "SQUare",
    "up-ramp": "RAMP",
    "triangle": "TRIangle",
    "down-ramp": "NRAMp",
}
MODULATION_SHAPES = tuple(MODULATION_SHAPE_SCPI)
