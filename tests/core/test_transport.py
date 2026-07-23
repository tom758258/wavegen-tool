import pytest

from wavegen_tool_core.errors import UnsupportedTransportError
from wavegen_tool_core.transport import classify_transport, detect_resource_transport


def test_usb_resource_is_accepted():
    resource = "USB0::0x0000::0x0000::MY00000000::INSTR"

    assert classify_transport(resource) == "usb"


def test_tcpip_resource_is_accepted():
    resource = "TCPIP0::192.0.2.10::inst0::INSTR"

    assert classify_transport(resource) == "tcpip"


@pytest.mark.parametrize(
    ("resource", "transport"),
    [
        ("USB0::VALUE::INSTR", "usb"),
        ("TCPIP0::192.0.2.10::INSTR", "tcpip"),
        ("ASRL6::INSTR", "asrl"),
        ("GPIB0::10::INSTR", "gpib"),
        ("PXI0::1::INSTR", "pxi"),
        ("VXI0::1::INSTR", "vxi"),
        ("SOME0::VALUE::INSTR", "unknown"),
    ],
)
def test_transport_detection_does_not_apply_identify_admission(resource, transport):
    assert detect_resource_transport(resource) == transport


@pytest.mark.parametrize(
    ("resource", "detected"),
    [
        ("GPIB0::10::INSTR", "gpib"),
        ("ASRL1::INSTR", "asrl"),
        ("PXI0::1::INSTR", "pxi"),
        ("VXI0::1::INSTR", "vxi"),
        ("SOME0::VALUE::INSTR", "unknown"),
        ("USB-invalid", "unknown"),
        ("", None),
        ("   ", None),
    ],
)
def test_unsupported_or_empty_resource_is_rejected(resource, detected):
    with pytest.raises(UnsupportedTransportError) as error:
        classify_transport(resource)

    assert error.value.transport == detected
