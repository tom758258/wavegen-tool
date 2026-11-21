"""File names from the Keysight Trueform guide's BuiltIn directory catalog."""

BUILTIN_ARBITRARY_PATHS = {
    name: rf"INT:\BUILTIN\{name}.ARB"
    for name in (
        "CARDIAC", "EXP_RISE", "EXP_FALL", "GAUSSIAN", "HAVERSINE",
        "LORENTZ", "D_LORENTZ", "NEG_RAMP", "SINC",
    )
}
