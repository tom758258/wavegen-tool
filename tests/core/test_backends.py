import pytest

from wavegen_tool_core.backends import normalize_backend
from wavegen_tool_core.errors import UnsupportedBackendError


@pytest.mark.parametrize("value", [None, "", "   "])
def test_default_backend_resolves_to_system_visa(value):
    backend = normalize_backend(value)

    assert backend.name == "system"
    assert backend.internal_name == "system_visa"
    assert backend.pyvisa_library is None


def test_explicit_system_backend():
    backend = normalize_backend("system")

    assert backend.name == "system"
    assert backend.pyvisa_library is None


def test_explicit_pyvisa_py_backend():
    backend = normalize_backend("@py")

    assert backend.name == "@py"
    assert backend.internal_name == "pyvisa_py"
    assert backend.pyvisa_library == "@py"


@pytest.mark.parametrize("value", ["py", "@ivi", "default", object()])
def test_unsupported_backend_is_rejected(value):
    with pytest.raises(UnsupportedBackendError):
        normalize_backend(value)
