import pytest

from wavegen_tool_core.backends import normalize_backend, validate_backend_transport
from wavegen_tool_core.errors import UnsupportedBackendError, UnsupportedConnectionScopeError


@pytest.mark.parametrize("value", [None, "", "   "])
def test_default_backend_resolves_to_system_visa(value):
    backend = normalize_backend(value)

    assert backend.name == "system"
    assert backend.internal_name == "system_visa"
    assert backend.pyvisa_library == "@ivi"


def test_explicit_system_backend():
    backend = normalize_backend("system")

    assert backend.name == "system"
    assert backend.pyvisa_library == "@ivi"


def test_explicit_pyvisa_py_backend():
    backend = normalize_backend("@py")

    assert backend.name == "@py"
    assert backend.internal_name == "pyvisa_py"
    assert backend.pyvisa_library == "@py"


@pytest.mark.parametrize("value", ["py", "@ivi", "default", object()])
def test_unsupported_backend_is_rejected(value):
    with pytest.raises(UnsupportedBackendError):
        normalize_backend(value)


@pytest.mark.parametrize(
    ("backend_name", "transport"),
    [
        ("system", "usb"),
        ("system", "tcpip"),
        ("@py", "tcpip"),
    ],
)
def test_supported_backend_transport_combinations_are_accepted(backend_name, transport):
    validate_backend_transport(normalize_backend(backend_name), transport)


def test_pyvisa_py_usb_combination_is_rejected_with_clear_guidance():
    with pytest.raises(UnsupportedConnectionScopeError) as error:
        validate_backend_transport(normalize_backend("@py"), "usb")

    assert error.value.backend == "@py"
    assert error.value.transport == "usb"
    message = str(error.value)
    assert "USB resources are supported with the 'system' backend" in message
    assert "'@py' currently accepts TCPIP/LAN resources only" in message
