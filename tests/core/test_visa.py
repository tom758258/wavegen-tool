from inspect import signature

from pyvisa.constants import RENLineOperation
import pytest

import wavegen_tool_core.visa as visa_module
from wavegen_tool_core.capabilities import capabilities_for_model_id
from wavegen_tool_core.errors import (
    ErrorQueueQueryError,
    IdnQueryError,
    MalformedIdnError,
    ResourceDiscoveryError,
    ResourceManagerError,
    ResourceOpenError,
    StatusQueryError,
    UnsupportedConnectionScopeError,
    UnsupportedInstrumentError,
    UnsupportedTransportError,
    VisaCleanupError,
    VisaWriteError,
    WaveformParameterError,
    WaveformVerificationError,
)
from wavegen_tool_core.simulator import (
    SIMULATED_33521B_RESOURCE,
    SimulatedResourceManager,
)
from wavegen_tool_core.visa import (
    DEFAULT_TIMEOUT_MS,
    IDN_QUERY,
    LIVE_VERIFY_TIMEOUT_MS,
    ResourceListEntry,
    configure_dc,
    configure_noise,
    configure_prbs,
    configure_pulse,
    configure_ramp,
    configure_ramp_sweep,
    configure_sine,
    configure_sine_sweep,
    configure_square,
    configure_square_sweep,
    configure_triangle,
    configure_triangle_sweep,
    dry_run_dc,
    dry_run_noise,
    dry_run_prbs,
    dry_run_pulse,
    dry_run_ramp,
    dry_run_ramp_sweep,
    dry_run_sine,
    dry_run_sine_sweep,
    dry_run_square,
    dry_run_square_sweep,
    dry_run_triangle,
    dry_run_triangle_sweep,
    identify_instrument,
    list_resources,
    normalize_serial_baud_rate,
    normalize_serial_termination,
    query_status,
    read_error_queue,
    resolve_voltage_inputs,
    set_output,
)


USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
TCPIP_RESOURCE = "TCPIP0::192.0.2.10::inst0::INSTR"
ASRL_RESOURCE = "ASRL6::INSTR"
VALID_IDN = "KEYSIGHT TECHNOLOGIES,33521B,MY00000000,1.00-0.00-0.00"
STATUS_RESPONSES = {
    "OUTPut1?": " 0 ",
    "SOURce1:FUNCtion?": " sin ",
    "SOURce1:FREQuency?": "1.000000000000000E+03",
    "SOURce1:VOLTage:UNIT?": " vpp ",
    "SOURce1:VOLTage?": "1.000000000000000E-01",
    "SOURce1:VOLTage:OFFSet?": "0.000000000000000E+00",
    "OUTPut1:LOAD?": "9.900000000000000E+37",
}
PULSE_RESPONSES = {
    "SOURce1:FUNCtion:PULSe:TRANsition? MAXimum": "1.000000000000000E-06",
    "OUTPut1?": "0",
    "SOURce1:FUNCtion?": "PULS",
    "SOURce1:FREQuency?": "10000000.0000005",
    "SOURce1:FUNCtion:PULSe:WIDTh?": "5.005e-08",
    "SOURce1:FUNCtion:PULSe:TRANsition?": "2.005e-08",
    "SOURce1:PHASe?": "0",
}


class FakeSession:
    def __init__(
        self,
        response=VALID_IDN,
        *,
        query_error=None,
        responses_by_command=None,
        query_errors_by_command=None,
        write_error=None,
        close_error=None,
        control_ren_error=None,
        events=None,
    ):
        self.response = response
        self.query_error = query_error
        self.responses_by_command = responses_by_command or {}
        self.query_errors_by_command = query_errors_by_command or {}
        self.write_error = write_error
        self.close_error = close_error
        self.control_ren_error = control_ren_error
        self.timeout = None
        self.baud_rate = 4800
        self.read_termination = "existing read"
        self.write_termination = "existing write"
        self.queries = []
        self.writes = []
        self.events = events if events is not None else []
        self.control_ren_calls = []
        self.close_calls = 0

    def query(self, command):
        self.queries.append(command)
        self.events.append(("query", command))
        if command in self.query_errors_by_command:
            raise self.query_errors_by_command[command]
        if self.query_error is not None:
            raise self.query_error
        return self.responses_by_command.get(command, self.response)

    def close(self):
        self.close_calls += 1
        self.events.append(("close",))
        if self.close_error is not None:
            raise self.close_error

    def write(self, command):
        self.writes.append(command)
        self.events.append(("write", command))
        if self.write_error is not None:
            raise self.write_error

    def clear(self):
        raise AssertionError("clear must not be called")

    def control_ren(self, mode):
        self.control_ren_calls.append(mode)
        self.events.append(("control_ren", mode))
        if self.control_ren_error is not None:
            raise self.control_ren_error

    def read_stb(self):
        raise AssertionError("read_stb must not be called")


class FakeErrorQueueSession:
    """FakeSession with a FIFO response queue for SYSTem:ERRor?."""

    def __init__(
        self,
        error_queue_responses,
        response=VALID_IDN,
        *,
        close_error=None,
        events=None,
    ):
        self.response = response
        self.error_queue_responses = list(error_queue_responses)
        self.close_error = close_error
        self.timeout = None
        self.baud_rate = 4800
        self.read_termination = "existing read"
        self.write_termination = "existing write"
        self.queries = []
        self.writes = []
        self.events = events if events is not None else []
        self.control_ren_calls = []
        self.close_calls = 0

    def query(self, command):
        self.queries.append(command)
        self.events.append(("query", command))
        if command == "SYSTem:ERRor?":
            if self.error_queue_responses:
                return self.error_queue_responses.pop(0)
            return '+0,"No error"'
        return self.response

    def close(self):
        self.close_calls += 1
        self.events.append(("close",))
        if self.close_error is not None:
            raise self.close_error

    def write(self, command):
        self.writes.append(command)
        self.events.append(("write", command))

    def clear(self):
        raise AssertionError("clear must not be called")

    def control_ren(self, mode):
        self.control_ren_calls.append(mode)
        self.events.append(("control_ren", mode))

    def read_stb(self):
        raise AssertionError("read_stb must not be called")


class FakeManager:
    def __init__(
        self,
        session=None,
        *,
        resources=(),
        list_error=None,
        sessions_by_resource=None,
        open_errors=None,
        open_error=None,
        close_error=None,
        events=None,
    ):
        self.session = session or FakeSession()
        self.resources = resources
        self.list_error = list_error
        self.sessions_by_resource = sessions_by_resource or {}
        self.open_errors = open_errors or {}
        self.open_error = open_error
        self.close_error = close_error
        self.events = events if events is not None else []
        self.list_calls = 0
        self.opened_resources = []
        self.open_calls = []
        self.close_calls = 0

    def list_resources(self):
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return self.resources

    def open_resource(self, resource, **kwargs):
        self.opened_resources.append(resource)
        self.open_calls.append((resource, kwargs))
        if resource in self.open_errors:
            raise self.open_errors[resource]
        if self.open_error is not None:
            raise self.open_error
        return self.sessions_by_resource.get(resource, self.session)

    def close(self):
        self.close_calls += 1
        self.events.append(("manager_close",))
        if self.close_error is not None:
            raise self.close_error


class RecordingFactory:
    def __init__(self, manager):
        self.manager = manager
        self.calls = []

    def __call__(self, pyvisa_library):
        self.calls.append(pyvisa_library)
        return self.manager


@pytest.mark.parametrize(
    ("backend", "library"),
    [
        ("system", "@ivi"),
        ("@py", "@py"),
    ],
)
def test_raw_resource_listing_uses_selected_backend_once_and_closes(backend, library):
    resources = (ASRL_RESOURCE, TCPIP_RESOURCE, "GPIB0::10::INSTR", USB_RESOURCE)
    manager = FakeManager(resources=resources)
    factory = RecordingFactory(manager)

    result = list_resources(backend, resource_manager_factory=factory)

    assert factory.calls == [library]
    assert manager.list_calls == 1
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.control_ren_calls == []
    assert manager.close_calls == 1
    assert result.backend == backend
    assert result.resources == tuple(ResourceListEntry(resource=item) for item in resources)


def test_raw_resource_listing_empty_result_is_successful_and_closes():
    manager = FakeManager(resources=())

    result = list_resources(resource_manager_factory=RecordingFactory(manager))

    assert result.resources == ()
    assert manager.list_calls == 1
    assert manager.opened_resources == []
    assert manager.close_calls == 1


def test_resource_listing_manager_creation_failure_is_distinct():
    calls = []

    def failing_factory(pyvisa_library):
        calls.append(pyvisa_library)
        raise RuntimeError("private manager detail")

    with pytest.raises(ResourceManagerError) as error:
        list_resources("system", resource_manager_factory=failing_factory)

    assert calls == ["@ivi"]
    assert error.value.backend == "system"


def test_raw_resource_listing_failure_closes_manager_without_retry():
    manager = FakeManager(list_error=RuntimeError("private listing detail"))

    with pytest.raises(ResourceDiscoveryError) as error:
        list_resources("@py", resource_manager_factory=RecordingFactory(manager))

    assert error.value.backend == "@py"
    assert str(error.value) == "Could not list VISA resources."
    assert manager.list_calls == 1
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.close_calls == 1


def test_resource_listing_failure_remains_primary_when_cleanup_fails():
    manager = FakeManager(
        list_error=RuntimeError("listing failed"),
        close_error=RuntimeError("close failed"),
    )

    with pytest.raises(ResourceDiscoveryError) as error:
        list_resources(resource_manager_factory=RecordingFactory(manager))

    assert error.value.cleanup_errors == ("ResourceManager close failed",)
    assert manager.list_calls == 1
    assert manager.close_calls == 1


def test_resource_listing_cleanup_only_failure_is_reported():
    manager = FakeManager(
        resources=(TCPIP_RESOURCE,),
        close_error=RuntimeError("close failed"),
    )

    with pytest.raises(VisaCleanupError) as error:
        list_resources(resource_manager_factory=RecordingFactory(manager))

    assert error.value.backend == "system"
    assert "ResourceManager close failed" in str(error.value)
    assert manager.list_calls == 1
    assert manager.close_calls == 1


def test_system_live_only_verifies_usb_tcpip_and_asrl_and_skips_other_transports():
    gpib = "GPIB0::10::INSTR"
    pxi = "PXI0::0::INSTR"
    vxi = "VXI0::1::INSTR"
    unknown = "SOME0::VALUE::INSTR"
    resources = (ASRL_RESOURCE, TCPIP_RESOURCE, gpib, USB_RESOURCE, pxi, vxi, unknown)
    asrl_session = FakeSession(response="Agilent Technologies,33521B,SERIAL,FIRMWARE")
    tcpip_session = FakeSession(response="Vendor,Model,Serial,Firmware")
    usb_session = FakeSession(response="not,a,required,identity")
    manager = FakeManager(
        resources=resources,
        sessions_by_resource={
            ASRL_RESOURCE: asrl_session,
            TCPIP_RESOURCE: tcpip_session,
            USB_RESOURCE: usb_session,
        },
    )

    result = list_resources(
        "system",
        live_only=True,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert manager.list_calls == 1
    assert manager.opened_resources == [ASRL_RESOURCE, TCPIP_RESOURCE, USB_RESOURCE]
    assert manager.open_calls[0] == (
        ASRL_RESOURCE,
        {"open_timeout": LIVE_VERIFY_TIMEOUT_MS},
    )
    assert manager.open_calls[1:] == [(TCPIP_RESOURCE, {}), (USB_RESOURCE, {})]
    assert asrl_session.timeout == LIVE_VERIFY_TIMEOUT_MS
    assert tcpip_session.timeout == LIVE_VERIFY_TIMEOUT_MS
    assert usb_session.timeout == LIVE_VERIFY_TIMEOUT_MS
    assert asrl_session.queries == [IDN_QUERY]
    assert tcpip_session.queries == [IDN_QUERY]
    assert usb_session.queries == [IDN_QUERY]
    assert asrl_session.close_calls == 1
    assert tcpip_session.close_calls == 1
    assert usb_session.close_calls == 1
    assert asrl_session.control_ren_calls == []
    assert tcpip_session.control_ren_calls == []
    assert usb_session.control_ren_calls == [RENLineOperation.address_gtl]
    assert manager.close_calls == 1
    assert result.resources == (
        ResourceListEntry(ASRL_RESOURCE, "Agilent Technologies", "33521B"),
        ResourceListEntry(TCPIP_RESOURCE, "Vendor", "Model"),
        ResourceListEntry(USB_RESOURCE, "not", "a"),
    )


def test_pyvisa_py_live_only_verifies_tcpip_and_skips_usb():
    tcpip_session = FakeSession(response="any non-empty response")
    manager = FakeManager(
        resources=(USB_RESOURCE, ASRL_RESOURCE, TCPIP_RESOURCE),
        sessions_by_resource={TCPIP_RESOURCE: tcpip_session},
    )
    factory = RecordingFactory(manager)

    result = list_resources(
        "@py",
        live_only=True,
        resource_manager_factory=factory,
    )

    assert factory.calls == ["@py"]
    assert manager.opened_resources == [TCPIP_RESOURCE]
    assert tcpip_session.queries == [IDN_QUERY]
    assert tcpip_session.control_ren_calls == []
    assert tcpip_session.close_calls == 1
    assert result.resources == (ResourceListEntry(TCPIP_RESOURCE),)


def test_system_usb_live_only_cleanup_orders_gtl_before_session_and_manager_close():
    events = []
    session = FakeSession(events=events)
    manager = FakeManager(
        resources=(USB_RESOURCE,),
        sessions_by_resource={USB_RESOURCE: session},
        events=events,
    )

    list_resources(
        "system",
        live_only=True,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.control_ren_calls == [RENLineOperation.address_gtl]
    assert events == [
        ("query", IDN_QUERY),
        ("control_ren", RENLineOperation.address_gtl),
        ("close",),
        ("manager_close",),
    ]


def test_system_usb_live_only_gtl_failure_closes_session_and_manager():
    session = FakeSession(control_ren_error=RuntimeError("GTL failed"))
    manager = FakeManager(
        resources=(USB_RESOURCE,),
        sessions_by_resource={USB_RESOURCE: session},
    )

    with pytest.raises(VisaCleanupError) as error:
        list_resources(
            "system",
            live_only=True,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert "return to local failed" in str(error.value)
    assert session.control_ren_calls == [RENLineOperation.address_gtl]
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_multiple_usb_live_only_cleanup_errors_preserve_candidate_order():
    first_resource = "USB0::0x0000::0x0000::MY00000001::INSTR"
    second_resource = "USB0::0x0000::0x0000::MY00000002::INSTR"
    events = []
    first_session = FakeSession(
        control_ren_error=RuntimeError("first GTL failed"),
        events=events,
    )
    second_session = FakeSession(
        close_error=RuntimeError("second close failed"),
        events=events,
    )
    manager = FakeManager(
        resources=(first_resource, second_resource),
        sessions_by_resource={
            first_resource: first_session,
            second_resource: second_session,
        },
        events=events,
    )

    with pytest.raises(VisaCleanupError) as error:
        list_resources(
            "system",
            live_only=True,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert str(error.value) == (
        "VISA cleanup failed: return to local failed; session close failed."
    )
    assert first_session.control_ren_calls == [RENLineOperation.address_gtl]
    assert second_session.control_ren_calls == [RENLineOperation.address_gtl]
    assert first_session.close_calls == 1
    assert second_session.close_calls == 1
    assert manager.close_calls == 1
    assert events == [
        ("query", IDN_QUERY),
        ("control_ren", RENLineOperation.address_gtl),
        ("close",),
        ("query", IDN_QUERY),
        ("control_ren", RENLineOperation.address_gtl),
        ("close",),
        ("manager_close",),
    ]


def test_simulator_live_only_does_not_attempt_gtl():
    manager = SimulatedResourceManager()

    result = list_resources(
        "system",
        live_only=True,
        resource_manager_factory=lambda _library: manager,
    )

    assert result.resources == (
        ResourceListEntry(
            SIMULATED_33521B_RESOURCE,
            "KEYSIGHT TECHNOLOGIES",
            "33521B",
        ),
    )
    assert manager.closed


def test_live_only_keeps_parsed_identity_for_any_instrument_and_unknown_for_malformed():
    unsupported = "USB0::0x0000::0x0000::MY00000001::INSTR"
    malformed = "TCPIP0::192.0.2.11::inst0::INSTR"
    unsupported_session = FakeSession(
        response="Keysight Technologies,34465A,SERIAL,FIRMWARE"
    )
    malformed_session = FakeSession(response="non-standard response")
    manager = FakeManager(
        resources=(unsupported, malformed),
        sessions_by_resource={
            unsupported: unsupported_session,
            malformed: malformed_session,
        },
    )

    result = list_resources(
        live_only=True,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert result.resources == (
        ResourceListEntry(unsupported, "Keysight Technologies", "34465A"),
        ResourceListEntry(malformed),
    )
    assert unsupported_session.queries == [IDN_QUERY]
    assert malformed_session.queries == [IDN_QUERY]


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("CR", "\r"),
        ("LF", "\n"),
        ("CRLF", "\r\n"),
        ("NONE", None),
        (" cr ", "\r"),
    ],
)
def test_serial_termination_normalization(token, expected):
    assert normalize_serial_termination(token) == expected


@pytest.mark.parametrize("value", [9600, "9600"])
def test_positive_serial_baud_rate_normalization(value):
    assert normalize_serial_baud_rate(value) == 9600


@pytest.mark.parametrize("value", [0, -1, "not-an-integer", 9600.5, True])
def test_invalid_serial_baud_rate_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_serial_baud_rate(value)


def test_explicit_serial_settings_apply_only_to_asrl():
    asrl_session = FakeSession()
    usb_session = FakeSession()
    manager = FakeManager(
        resources=(ASRL_RESOURCE, USB_RESOURCE),
        sessions_by_resource={
            ASRL_RESOURCE: asrl_session,
            USB_RESOURCE: usb_session,
        },
    )

    list_resources(
        live_only=True,
        serial_baud_rate=9600,
        serial_read_termination="LF",
        serial_write_termination="NONE",
        resource_manager_factory=RecordingFactory(manager),
    )

    assert asrl_session.baud_rate == 9600
    assert asrl_session.read_termination == "\n"
    assert asrl_session.write_termination is None
    assert usb_session.baud_rate == 4800
    assert usb_session.read_termination == "existing read"
    assert usb_session.write_termination == "existing write"


def test_omitted_serial_settings_do_not_overwrite_asrl_session():
    session = FakeSession()
    manager = FakeManager(
        resources=(ASRL_RESOURCE,),
        sessions_by_resource={ASRL_RESOURCE: session},
    )

    list_resources(
        live_only=True,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.baud_rate == 4800
    assert session.read_termination == "existing read"
    assert session.write_termination == "existing write"


def test_asrl_query_timeout_is_filtered_closed_and_next_candidate_continues():
    timeout_session = FakeSession(query_error=TimeoutError("private timeout"))
    live_session = FakeSession(response="Vendor,Model,Serial,Firmware")
    manager = FakeManager(
        resources=(ASRL_RESOURCE, TCPIP_RESOURCE),
        sessions_by_resource={
            ASRL_RESOURCE: timeout_session,
            TCPIP_RESOURCE: live_session,
        },
    )

    result = list_resources(
        live_only=True,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert manager.opened_resources == [ASRL_RESOURCE, TCPIP_RESOURCE]
    assert timeout_session.queries == [IDN_QUERY]
    assert timeout_session.close_calls == 1
    assert live_session.queries == [IDN_QUERY]
    assert result.resources == (
        ResourceListEntry(TCPIP_RESOURCE, "Vendor", "Model"),
    )


def test_asrl_open_failure_is_filtered_without_retry_and_next_candidate_continues():
    live_asrl = "ASRL7::INSTR"
    live_session = FakeSession(response="Vendor,Model,Serial,Firmware")
    manager = FakeManager(
        resources=(ASRL_RESOURCE, live_asrl),
        open_errors={ASRL_RESOURCE: TimeoutError("private open timeout")},
        sessions_by_resource={live_asrl: live_session},
    )

    result = list_resources(
        live_only=True,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert manager.open_calls == [
        (ASRL_RESOURCE, {"open_timeout": LIVE_VERIFY_TIMEOUT_MS}),
        (live_asrl, {"open_timeout": LIVE_VERIFY_TIMEOUT_MS}),
    ]
    assert live_session.queries == [IDN_QUERY]
    assert live_session.close_calls == 1
    assert result.resources == (ResourceListEntry(live_asrl, "Vendor", "Model"),)


def test_live_only_filters_failures_and_empty_responses_without_retry():
    open_failure = "TCPIP0::192.0.2.11::inst0::INSTR"
    query_failure = "TCPIP0::192.0.2.12::inst0::INSTR"
    timeout = "TCPIP0::192.0.2.13::inst0::INSTR"
    empty = "TCPIP0::192.0.2.14::inst0::INSTR"
    live = "TCPIP0::192.0.2.15::inst0::INSTR"
    query_failure_session = FakeSession(query_error=RuntimeError("query failed"))
    timeout_session = FakeSession(query_error=TimeoutError("timed out"))
    empty_session = FakeSession(response="  \r\n")
    live_session = FakeSession(response="response")
    manager = FakeManager(
        resources=(open_failure, query_failure, timeout, empty, live),
        open_errors={open_failure: RuntimeError("open failed")},
        sessions_by_resource={
            query_failure: query_failure_session,
            timeout: timeout_session,
            empty: empty_session,
            live: live_session,
        },
    )

    result = list_resources(
        live_only=True,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert manager.opened_resources == [
        open_failure,
        query_failure,
        timeout,
        empty,
        live,
    ]
    assert query_failure_session.queries == [IDN_QUERY]
    assert timeout_session.queries == [IDN_QUERY]
    assert empty_session.queries == [IDN_QUERY]
    assert live_session.queries == [IDN_QUERY]
    assert query_failure_session.close_calls == 1
    assert timeout_session.close_calls == 1
    assert empty_session.close_calls == 1
    assert live_session.close_calls == 1
    assert manager.close_calls == 1
    assert result.resources == (ResourceListEntry(live),)


def test_live_only_session_cleanup_failure_raises_after_manager_cleanup():
    session = FakeSession(response="response", close_error=RuntimeError("close failed"))
    manager = FakeManager(
        resources=(TCPIP_RESOURCE,),
        sessions_by_resource={TCPIP_RESOURCE: session},
    )

    with pytest.raises(VisaCleanupError) as error:
        list_resources(
            live_only=True,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert "session close failed" in str(error.value)
    assert session.queries == [IDN_QUERY]
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_system_backend_lifecycle_queries_once_and_closes():
    session = FakeSession()
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    result = identify_instrument(USB_RESOURCE, "system", resource_manager_factory=factory)

    assert factory.calls == ["@ivi"]
    assert manager.opened_resources == [USB_RESOURCE]
    assert session.timeout == DEFAULT_TIMEOUT_MS
    assert session.queries == [IDN_QUERY]
    assert session.close_calls == 1
    assert manager.close_calls == 1
    assert result.backend == "system"
    assert result.transport == "usb"
    assert result.identity.canonical_model_id == "keysight-33521b"


def test_system_usb_cleanup_orders_gtl_before_session_and_manager_close():
    events = []
    session = FakeSession(events=events)
    manager = FakeManager(session, events=events)

    identify_instrument(
        USB_RESOURCE,
        "system",
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.control_ren_calls == [RENLineOperation.address_gtl]
    assert events == [
        ("query", IDN_QUERY),
        ("control_ren", RENLineOperation.address_gtl),
        ("close",),
        ("manager_close",),
    ]


def test_gtl_failure_closes_resources_and_reports_cleanup_failure():
    session = FakeSession(control_ren_error=RuntimeError("GTL failed"))
    manager = FakeManager(session)

    with pytest.raises(VisaCleanupError) as error:
        identify_instrument(
            USB_RESOURCE,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert "return to local failed" in str(error.value)
    assert session.control_ren_calls == [RENLineOperation.address_gtl]
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_gtl_failure_does_not_hide_primary_operation_error():
    session = FakeSession(
        query_error=TimeoutError("query timed out"),
        control_ren_error=RuntimeError("GTL failed"),
    )
    manager = FakeManager(session)

    with pytest.raises(IdnQueryError) as error:
        identify_instrument(
            USB_RESOURCE,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert error.value.cleanup_errors == ("return to local failed",)
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_system_backend_accepts_tcpip():
    manager = FakeManager()
    factory = RecordingFactory(manager)

    result = identify_instrument(TCPIP_RESOURCE, "system", resource_manager_factory=factory)

    assert factory.calls == ["@ivi"]
    assert manager.opened_resources == [TCPIP_RESOURCE]
    assert manager.session.control_ren_calls == []
    assert manager.session.close_calls == 1
    assert manager.close_calls == 1
    assert result.backend == "system"
    assert result.transport == "tcpip"


def test_pyvisa_py_backend_accepts_tcpip_without_fallback():
    manager = FakeManager()
    factory = RecordingFactory(manager)

    result = identify_instrument(TCPIP_RESOURCE, "@py", resource_manager_factory=factory)

    assert factory.calls == ["@py"]
    assert manager.opened_resources == [TCPIP_RESOURCE]
    assert manager.session.control_ren_calls == []
    assert manager.session.close_calls == 1
    assert manager.close_calls == 1
    assert result.backend == "@py"
    assert result.transport == "tcpip"


def test_pyvisa_py_usb_is_rejected_before_resource_manager_creation():
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(UnsupportedConnectionScopeError) as error:
        identify_instrument(USB_RESOURCE, "@py", resource_manager_factory=factory)

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert error.value.backend == "@py"
    assert error.value.transport == "usb"


@pytest.mark.parametrize(
    "resource",
    [
        "GPIB0::10::INSTR",
        "ASRL1::INSTR",
        "SOME0::VALUE::INSTR",
    ],
)
def test_unsupported_transport_is_rejected_before_resource_manager_creation(resource):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(UnsupportedTransportError):
        identify_instrument(resource, "system", resource_manager_factory=factory)

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []


def test_resource_manager_creation_failure_is_distinct():
    calls = []

    def failing_factory(pyvisa_library):
        calls.append(pyvisa_library)
        raise RuntimeError("manager failed")

    with pytest.raises(ResourceManagerError) as error:
        identify_instrument(USB_RESOURCE, "system", resource_manager_factory=failing_factory)

    assert calls == ["@ivi"]
    assert error.value.backend == "system"


def test_resource_open_failure_closes_manager():
    manager = FakeManager(open_error=RuntimeError("open failed"))

    with pytest.raises(ResourceOpenError):
        identify_instrument(USB_RESOURCE, resource_manager_factory=RecordingFactory(manager))

    assert manager.opened_resources == [USB_RESOURCE]
    assert manager.session.close_calls == 0
    assert manager.close_calls == 1


def test_query_failure_closes_session_and_manager():
    session = FakeSession(query_error=TimeoutError("query timed out"))
    manager = FakeManager(session)

    with pytest.raises(IdnQueryError):
        identify_instrument(USB_RESOURCE, resource_manager_factory=RecordingFactory(manager))

    assert session.queries == [IDN_QUERY]
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_malformed_idn_closes_session_and_manager():
    session = FakeSession(response="too,few,fields")
    manager = FakeManager(session)

    with pytest.raises(MalformedIdnError):
        identify_instrument(USB_RESOURCE, resource_manager_factory=RecordingFactory(manager))

    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_unsupported_model_closes_session_and_manager():
    session = FakeSession(response="Keysight Technologies,33522B,MY00000000,1.00")
    manager = FakeManager(session)

    with pytest.raises(UnsupportedInstrumentError):
        identify_instrument(USB_RESOURCE, resource_manager_factory=RecordingFactory(manager))

    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_cleanup_error_does_not_hide_query_error():
    session = FakeSession(
        query_error=TimeoutError("query timed out"),
        close_error=RuntimeError("session close failed"),
        control_ren_error=RuntimeError("GTL failed"),
    )
    manager = FakeManager(session, close_error=RuntimeError("manager close failed"))

    with pytest.raises(IdnQueryError) as error:
        identify_instrument(USB_RESOURCE, resource_manager_factory=RecordingFactory(manager))

    assert error.value.cleanup_errors == (
        "return to local failed",
        "session close failed",
        "ResourceManager close failed",
    )
    assert session.close_calls == 1
    assert manager.close_calls == 1


@pytest.mark.parametrize(
    ("session_close_error", "manager_close_error", "expected"),
    [
        (RuntimeError("session"), None, ("session close failed",)),
        (None, RuntimeError("manager"), ("ResourceManager close failed",)),
    ],
)
def test_cleanup_only_failure_is_reported(session_close_error, manager_close_error, expected):
    session = FakeSession(close_error=session_close_error)
    manager = FakeManager(session, close_error=manager_close_error)

    with pytest.raises(VisaCleanupError) as error:
        identify_instrument(USB_RESOURCE, resource_manager_factory=RecordingFactory(manager))

    assert all(item in str(error.value) for item in expected)


def test_configure_sine_identifies_then_writes_safe_channel_one_sequence():
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_sine(
        USB_RESOURCE,
        1000,
        0.1,
        0,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SIN",
        "SOURce1:FREQuency 1000",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    ]
    assert "OUTPut1 ON" not in session.writes
    assert result.output_state == "off"
    assert result.load == "50"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_dry_run_sine_returns_normalized_hardware_free_command_preview():
    result = dry_run_sine(
        "  KEYSIGHT-33521B  ",
        1000,
        0.1,
        0,
        50,
    )

    assert tuple(signature(dry_run_sine).parameters) == (
        "model",
        "frequency_hz",
        "amplitude_vpp",
        "offset_v",
        "load",
        "phase_deg",
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SIN",
        "SOURce1:FREQuency 1000",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    )
    assert result.executed is False
    assert result.output_state == "off"


@pytest.mark.parametrize(
    ("spacing", "sweep_time", "spacing_command"),
    [
        ("linear", 1, "LINear"),
        ("LOGARITHMIC", 2, "LOGarithmic"),
    ],
)
def test_sine_sweep_core_and_dry_run_share_ordered_write_plan(
    spacing, sweep_time, spacing_command
):
    session = FakeSession()
    manager = FakeManager(session)
    result = configure_sine_sweep(
        USB_RESOURCE,
        1000,
        10000,
        spacing,
        sweep_time,
        0.2,
        0.1,
        3,
        4,
        "high-z",
        phase_deg=90,
        resource_manager_factory=RecordingFactory(manager),
    )
    expected_commands = (
        "OUTPut1 OFF",
        "OUTPut1:LOAD INF",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SIN",
        "SOURce1:FREQuency 1000",
        "SOURce1:VOLTage 0.2",
        "SOURce1:VOLTage:OFFSet 0.1",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 90",
        "SOURce1:FREQuency:STARt 1000",
        "SOURce1:FREQuency:STOP 10000",
        f"SOURce1:SWEep:SPACing {spacing_command}",
        f"SOURce1:SWEep:TIME {sweep_time}",
        "SOURce1:SWEep:HTIMe 3",
        "SOURce1:SWEep:RTIMe 4",
        "TRIGger1:SOURce IMMediate",
        "SOURce1:FREQuency:MODE SWEep",
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == list(expected_commands)
    assert result.start_frequency_hz == 1000.0
    assert result.stop_frequency_hz == 10000.0
    assert result.spacing == spacing.casefold()
    assert result.sweep_time_s == float(sweep_time)
    assert result.hold_time_s == 3.0
    assert result.return_time_s == 4.0
    assert result.trigger_source == "immediate"
    assert result.amplitude_vpp == 0.2
    assert result.offset_v == 0.1
    assert result.phase_deg == 90.0
    assert result.load == "high-z"
    assert result.output_state == "off"

    preview = dry_run_sine_sweep(
        "keysight-33521b",
        1000,
        10000,
        spacing,
        sweep_time,
        0.2,
        0.1,
        3,
        4,
        "high-z",
        90,
    )
    assert preview.commands == expected_commands
    assert preview.executed is False
    assert preview.output_state == "off"


@pytest.mark.parametrize(
    ("waveform", "specific_value", "expected_commands"),
    [
        (
            "square",
            50,
            (
                "OUTPut1 OFF",
                "OUTPut1:LOAD INF",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion SQUare",
                "SOURce1:FREQuency 1000",
                "SOURce1:FUNCtion:SQUare:DCYCle 50",
                "SOURce1:VOLTage 0.2",
                "SOURce1:VOLTage:OFFSet 0.1",
                "UNIT:ANGLe DEGree",
                "SOURce1:PHASe 45",
                "SOURce1:FREQuency:STARt 1000",
                "SOURce1:FREQuency:STOP 30000",
                "SOURce1:SWEep:SPACing LINear",
                "SOURce1:SWEep:TIME 1",
                "SOURce1:SWEep:HTIMe 2",
                "SOURce1:SWEep:RTIMe 3",
                "TRIGger1:SOURce IMMediate",
                "SOURce1:FREQuency:MODE SWEep",
            ),
        ),
        (
            "ramp",
            25,
            (
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FREQuency MINimum",
                "SOURce1:FUNCtion RAMP",
                "SOURce1:FREQuency 10000",
                "SOURce1:FUNCtion:RAMP:SYMMetry 25",
                "SOURce1:VOLTage 0.2",
                "SOURce1:VOLTage:OFFSet -0.1",
                "UNIT:ANGLe DEGree",
                "SOURce1:PHASe -45",
                "SOURce1:FREQuency:STARt 10000",
                "SOURce1:FREQuency:STOP 1000",
                "SOURce1:SWEep:SPACing LOGarithmic",
                "SOURce1:SWEep:TIME 2",
                "SOURce1:SWEep:HTIMe 1",
                "SOURce1:SWEep:RTIMe 2",
                "TRIGger1:SOURce IMMediate",
                "SOURce1:FREQuency:MODE SWEep",
            ),
        ),
        (
            "triangle",
            None,
            (
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FREQuency MINimum",
                "SOURce1:FUNCtion TRIangle",
                "SOURce1:FREQuency 200000",
                "SOURce1:VOLTage 0.2",
                "SOURce1:VOLTage:OFFSet 0",
                "UNIT:ANGLe DEGree",
                "SOURce1:PHASe 0",
                "SOURce1:FREQuency:STARt 200000",
                "SOURce1:FREQuency:STOP 1000",
                "SOURce1:SWEep:SPACing LOGarithmic",
                "SOURce1:SWEep:TIME 2",
                "SOURce1:SWEep:HTIMe 0",
                "SOURce1:SWEep:RTIMe 1",
                "TRIGger1:SOURce IMMediate",
                "SOURce1:FREQuency:MODE SWEep",
            ),
        ),
    ],
)
def test_square_ramp_triangle_sweep_core_and_dry_run_share_ordered_write_plan(
    waveform, specific_value, expected_commands
):
    session = FakeSession()
    manager = FakeManager(session)
    live_common = (
        USB_RESOURCE,
        1000 if waveform == "square" else 10000 if waveform == "ramp" else 200000,
        30000 if waveform == "square" else 1000,
        "linear" if waveform == "square" else "logarithmic",
        1 if waveform == "square" else 2,
        0.2,
        0.1 if waveform == "square" else -0.1 if waveform == "ramp" else 0,
        2 if waveform == "square" else 1 if waveform == "ramp" else 0,
        3 if waveform == "square" else 2 if waveform == "ramp" else 1,
        "high-z" if waveform == "square" else "50",
        "system",
        45 if waveform == "square" else -45 if waveform == "ramp" else 0,
    )
    dry_common = live_common[1:10] + (live_common[11],)

    if waveform == "square":
        result = configure_square_sweep(
            *live_common,
            specific_value,
            resource_manager_factory=RecordingFactory(manager),
        )
        preview = dry_run_square_sweep(
            "keysight-33521b",
            *dry_common,
            specific_value,
        )
        assert result.duty_cycle_percent == 50.0
        assert preview.duty_cycle_percent == 50.0
    elif waveform == "ramp":
        result = configure_ramp_sweep(
            *live_common,
            specific_value,
            resource_manager_factory=RecordingFactory(manager),
        )
        preview = dry_run_ramp_sweep(
            "keysight-33521b",
            *dry_common,
            specific_value,
        )
        assert result.symmetry_percent == 25.0
        assert preview.symmetry_percent == 25.0
    else:
        result = configure_triangle_sweep(
            *live_common,
            resource_manager_factory=RecordingFactory(manager),
        )
        preview = dry_run_triangle_sweep("keysight-33521b", *dry_common)

    assert session.queries == [IDN_QUERY]
    assert session.writes == list(expected_commands)
    assert preview.commands == expected_commands
    assert result.start_frequency_hz == float(live_common[1])
    assert result.stop_frequency_hz == float(live_common[2])
    assert result.spacing == live_common[3]
    assert result.sweep_time_s == float(live_common[4])
    assert result.hold_time_s == float(live_common[7])
    assert result.return_time_s == float(live_common[8])
    assert result.trigger_source == "immediate"
    assert result.amplitude_vpp == 0.2
    assert result.offset_v == float(live_common[6])
    assert result.phase_deg == float(live_common[11])
    assert result.load == live_common[9]
    assert result.output_state == "off"
    assert "OUTPut1 ON" not in session.writes
    assert "SOURce1:FREQuency:MODE CW" not in session.writes
    assert session.writes[-1] == "SOURce1:FREQuency:MODE SWEep"
    assert preview.executed is False
    assert preview.output_state == "off"


@pytest.mark.parametrize(
    ("field", "value", "spacing", "sweep_time", "hold_time", "return_time"),
    [
        ("stop_frequency_hz", 1000, "linear", 1, 0, 0),
        ("stop_frequency_hz", 30_000_001, "linear", 1, 0, 0),
        ("sweep_time_s", 8000.1, "linear", 8000.1, 0, 0),
        ("sweep_time_s", 500.1, "logarithmic", 500.1, 0, 0),
        ("hold_time_s", -1, "linear", 1, -1, 0),
        ("return_time_s", 3600.1, "linear", 1, 0, 3600.1),
        ("total_time_s", 7999, "linear", 7999, 2, 0),
        ("total_time_s", 499, "logarithmic", 499, 2, 0),
    ],
)
def test_invalid_sine_sweep_parameters_fail_before_visa_io(
    field, value, spacing, sweep_time, hold_time, return_time
):
    manager = FakeManager()
    factory = RecordingFactory(manager)
    arguments = {
        "start_frequency_hz": 1000,
        "stop_frequency_hz": 10000,
        "spacing": spacing,
        "sweep_time_s": sweep_time,
        "amplitude_vpp": 0.1,
        "offset_v": 0,
        "hold_time_s": hold_time,
        "return_time_s": return_time,
        "load": 50,
        "phase_deg": 0,
    }
    if field == "total_time_s":
        arguments["sweep_time_s"] = value
    else:
        arguments[field] = value

    with pytest.raises(WaveformParameterError):
        configure_sine_sweep(
            USB_RESOURCE,
            resource_manager_factory=factory,
            **arguments,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


@pytest.mark.parametrize(
    ("waveform", "start", "stop", "spacing", "sweep", "hold", "return_time", "duty"),
    [
        ("square", 1000, 1000, "linear", 1, 0, 0, 50),
        ("square", 1000, 30_000_001, "linear", 1, 0, 0, 50),
        ("ramp", 1000, 200_001, "linear", 1, 0, 0, 50),
        ("triangle", 1000, 200_001, "linear", 1, 0, 0, 50),
        ("square", 1_000_000, 30_000_000, "linear", 1, 0, 0, 47),
        ("square", 1000, 10000, "linear", 7999, 2, 0, 50),
        ("ramp", 1000, 10000, "logarithmic", 499, 2, 0, 50),
    ],
)
def test_invalid_square_ramp_triangle_sweep_parameters_fail_before_visa_io(
    waveform, start, stop, spacing, sweep, hold, return_time, duty
):
    manager = FakeManager()
    factory = RecordingFactory(manager)
    common = {
        "resource": USB_RESOURCE,
        "start_frequency_hz": start,
        "stop_frequency_hz": stop,
        "spacing": spacing,
        "sweep_time_s": sweep,
        "amplitude_vpp": 0.1,
        "offset_v": 0,
        "hold_time_s": hold,
        "return_time_s": return_time,
        "load": 50,
        "backend": "system",
        "phase_deg": 0,
        "resource_manager_factory": factory,
    }

    with pytest.raises(WaveformParameterError):
        if waveform == "square":
            configure_square_sweep(
                **common,
                duty_cycle_percent=duty,
            )
        elif waveform == "ramp":
            configure_ramp_sweep(
                **common,
                symmetry_percent=50,
            )
        else:
            configure_triangle_sweep(**common)

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


@pytest.mark.parametrize(
    ("prepare", "arguments"),
    [
        pytest.param(
            visa_module._prepare_sine_sweep,
            (20_000_001, 1000, "linear", 1, 0, 0, 0.1, 0, 50, 0),
            id="sine-start",
        ),
        pytest.param(
            visa_module._prepare_sine_sweep,
            (1000, 20_000_001, "linear", 1, 0, 0, 0.1, 0, 50, 0),
            id="sine-stop",
        ),
        pytest.param(
            visa_module._prepare_square_sweep,
            (20_000_001, 1000, "linear", 1, 0, 0, 0.1, 0, 50, 50, 0),
            id="square-start",
        ),
        pytest.param(
            visa_module._prepare_square_sweep,
            (1000, 20_000_001, "linear", 1, 0, 0, 0.1, 0, 50, 50, 0),
            id="square-stop",
        ),
    ],
)
def test_20_mhz_capability_limits_sine_and_square_sweep_endpoints(
    prepare,
    arguments,
):
    capabilities = capabilities_for_model_id("keysight-33510b")

    assert capabilities is not None
    with pytest.raises(WaveformParameterError, match="20000000 Hz"):
        prepare(*arguments, capabilities=capabilities)


@pytest.mark.parametrize(
    ("model", "frequency", "error_type"),
    [
        ("keysight-33522b", 1000, UnsupportedInstrumentError),
        ("keysight-33521b", 30_000_001, WaveformParameterError),
    ],
)
def test_invalid_sine_dry_run_input_raises_domain_error(
    model, frequency, error_type
):
    with pytest.raises(error_type):
        dry_run_sine(model, frequency, 0.1)


@pytest.mark.parametrize(
    ("frequency", "amplitude", "offset", "load", "phase"),
    [
        (0, 0.1, 0, 50, 0.0),
        (30_000_001, 0.1, 0, 50, 0.0),
        (1000, 0.0009, 0, 50, 0.0),
        (1000, 10.1, 0, 50, 0.0),
        (1000, 10, 0.001, 50, 0.0),
        (True, 0.1, 0, 50, 0.0),
        (float("nan"), 0.1, 0, 50, 0.0),
        (float("inf"), 0.1, 0, 50, 0.0),
        (float("-inf"), 0.1, 0, 50, 0.0),
        ("not-a-number", 0.1, 0, 50, 0.0),
        (1000, 0.1, 0, 50, -361.0),
        (1000, 0.1, 0, 50, 361.0),
        (1000, 0.1, 0, 50, True),
        (1000, 0.1, 0, 50, float("nan")),
        (1000, 0.1, 0, 50, float("inf")),
        (1000, 0.1, 0, 50, float("-inf")),
    ],
)
def test_invalid_sine_parameters_fail_before_visa_io(
    frequency, amplitude, offset, load, phase
):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError):
        configure_sine(
            USB_RESOURCE,
            frequency,
            amplitude,
            offset,
            load,
            phase_deg=phase,
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


@pytest.mark.parametrize(
    ("prepare", "arguments"),
    [
        pytest.param(
            visa_module._prepare_sine,
            (20_000_001, 0.1, 0, 50, 0),
            id="sine",
        ),
        pytest.param(
            visa_module._prepare_square,
            (20_000_001, 0.1, 0, 50, 50, 0),
            id="square",
        ),
        pytest.param(
            visa_module._prepare_pulse,
            (20_000_001, 0.1, 0.0001, 0, 1e-8, 50, 0, None, None),
            id="pulse",
        ),
        pytest.param(
            visa_module._prepare_noise,
            (0.1, 20_000_001, 0, 50),
            id="noise",
        ),
    ],
)
def test_20_mhz_capability_limits_normal_waveform_frequency(prepare, arguments):
    capabilities = capabilities_for_model_id("keysight-33510b")

    assert capabilities is not None
    with pytest.raises(WaveformParameterError, match="20000000 Hz"):
        prepare(*arguments, capabilities=capabilities)


@pytest.mark.parametrize(
    ("amplitude", "offset", "high", "low", "expected", "error_match"),
    [
        (None, None, 3.3, 0.0, (3.3, 1.65), None),
        (None, None, 2.0, -3.0, (5.0, -0.5), None),
        (None, None, 0.0, 0.0, None, "greater than low"),
        (None, None, 1.0, None, None, "provided together"),
        (0.1, None, 1.0, 0.0, None, "cannot be combined"),
    ],
)
def test_resolve_voltage_inputs_canonicalizes_and_rejects_invalid_modes(
    amplitude,
    offset,
    high,
    low,
    expected,
    error_match,
):
    if error_match is not None:
        with pytest.raises(WaveformParameterError, match=error_match):
            resolve_voltage_inputs(
                amplitude,
                offset,
                high,
                low,
                50,
                "Sine",
            )
        return

    assert resolve_voltage_inputs(
        amplitude,
        offset,
        high,
        low,
        50,
        "Sine",
    ) == expected


def test_configure_square_identifies_then_writes_safe_channel_one_sequence():
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_square(
        USB_RESOURCE,
        30_000_000,
        0.1,
        0,
        48,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SQUare",
        "SOURce1:FREQuency 30000000",
        "SOURce1:FUNCtion:SQUare:DCYCle 48",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    ]
    assert "OUTPut1 ON" not in session.writes
    assert result.frequency_hz == 30_000_000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.duty_cycle_percent == 48.0
    assert result.load == "50"
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_dry_run_square_returns_normalized_hardware_free_command_preview():
    result = dry_run_square(
        "  KEYSIGHT-33521B  ",
        1000,
        0.1,
        0,
        50,
        50,
    )

    assert tuple(signature(dry_run_square).parameters) == (
        "model",
        "frequency_hz",
        "amplitude_vpp",
        "offset_v",
        "duty_cycle_percent",
        "load",
        "phase_deg",
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.duty_cycle_percent == 50.0
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SQUare",
        "SOURce1:FREQuency 1000",
        "SOURce1:FUNCtion:SQUare:DCYCle 50",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    )
    assert result.executed is False
    assert result.output_state == "off"


def test_dry_run_square_rejects_frequency_dependent_duty_cycle():
    with pytest.raises(WaveformParameterError):
        dry_run_square("keysight-33521b", 30_000_000, 0.1, 0, 47)


@pytest.mark.parametrize(
    ("frequency", "amplitude", "offset", "duty_cycle"),
    [
        (30_000_001, 0.1, 0, 50),
        (30_000_000, 0.1, 0, 47),
        (1000, 10, 0.1, 50),
    ],
)
def test_invalid_square_parameters_fail_before_visa_io(
    frequency, amplitude, offset, duty_cycle
):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError):
        configure_square(
            USB_RESOURCE,
            frequency,
            amplitude,
            offset,
            duty_cycle,
            50,
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


def test_configure_ramp_identifies_then_writes_safe_channel_one_sequence():
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_ramp(
        USB_RESOURCE,
        1000,
        0.1,
        0,
        25,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FREQuency MINimum",
        "SOURce1:FUNCtion RAMP",
        "SOURce1:FREQuency 1000",
        "SOURce1:FUNCtion:RAMP:SYMMetry 25",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    ]
    assert "OUTPut1 ON" not in session.writes
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.symmetry_percent == 25.0
    assert result.load == "50"
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_dry_run_ramp_returns_normalized_hardware_free_command_preview():
    result = dry_run_ramp(
        "  KEYSIGHT-33521B  ",
        1000,
        0.1,
        0,
        25,
        50,
    )

    assert tuple(signature(dry_run_ramp).parameters) == (
        "model",
        "frequency_hz",
        "amplitude_vpp",
        "offset_v",
        "symmetry_percent",
        "load",
        "phase_deg",
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.symmetry_percent == 25.0
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FREQuency MINimum",
        "SOURce1:FUNCtion RAMP",
        "SOURce1:FREQuency 1000",
        "SOURce1:FUNCtion:RAMP:SYMMetry 25",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    )
    assert result.executed is False
    assert result.output_state == "off"


def test_triangle_configuration_and_dry_run_use_safe_direct_function_plan(
    monkeypatch,
):
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_triangle(
        USB_RESOURCE,
        "1000",
        "0.1",
        "0.2",
        "high-z",
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD INF",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FREQuency MINimum",
        "SOURce1:FUNCtion TRIangle",
        "SOURce1:FREQuency 1000",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0.2",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    ]
    assert "OUTPut1 ON" not in session.writes
    assert not any("RAMP" in command for command in session.writes)
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.2
    assert result.load == "high-z"
    assert result.output_state == "off"

    def fail_resource_manager(_library):
        raise AssertionError("dry-run must not create a ResourceManager")

    monkeypatch.setattr(visa_module, "create_resource_manager", fail_resource_manager)
    dry_run = dry_run_triangle("  KEYSIGHT-33521B  ", 1000, 0.1, 0.2, "high-z")

    assert dry_run.model == "33521B"
    assert dry_run.canonical_model_id == "keysight-33521b"
    assert dry_run.frequency_hz == 1000.0
    assert dry_run.amplitude_vpp == 0.1
    assert dry_run.offset_v == 0.2
    assert dry_run.load == "high-z"
    assert dry_run.commands == tuple(session.writes)
    assert dry_run.executed is False
    assert dry_run.output_state == "off"

    with pytest.raises(WaveformParameterError, match="Triangle frequency"):
        configure_triangle(
            USB_RESOURCE,
            200_001,
            0.1,
            resource_manager_factory=RecordingFactory(FakeManager()),
        )


def test_dry_run_ramp_rejects_invalid_symmetry():
    with pytest.raises(WaveformParameterError):
        dry_run_ramp("keysight-33521b", 1000, 0.1, 0, 101)


@pytest.mark.parametrize(
    ("frequency", "amplitude", "offset", "symmetry"),
    [
        (200_001, 0.1, 0, 25),
        (1000, 0.1, 0, -0.1),
        (1000, 0.1, 0, 100.1),
        (1000, 10, 0.1, 25),
    ],
)
def test_invalid_ramp_parameters_fail_before_visa_io(
    frequency, amplitude, offset, symmetry
):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError):
        configure_ramp(
            USB_RESOURCE,
            frequency,
            amplitude,
            offset,
            symmetry,
            50,
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


def test_configure_pulse_identifies_then_writes_safe_channel_one_sequence():
    session = FakeSession(
        responses_by_command={
            **PULSE_RESPONSES,
            "SOURce1:PHASe?": "90",
        }
    )
    manager = FakeManager(session)

    result = configure_pulse(
        USB_RESOURCE,
        10_000_000,
        0.1,
        50e-9,
        0,
        20e-9,
        50,
        phase_deg=90,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [
        IDN_QUERY,
        "SOURce1:FUNCtion:PULSe:TRANsition? MAXimum",
        "OUTPut1?",
        "SOURce1:FUNCtion?",
        "SOURce1:FREQuency?",
        "SOURce1:FUNCtion:PULSe:WIDTh?",
        "SOURce1:FUNCtion:PULSe:TRANsition?",
        "SOURce1:PHASe?",
    ]
    assert session.writes == [
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion:PULSe:HOLD WIDTh",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH MINimum",
        "SOURce1:FUNCtion:PULSe:WIDTh MINimum",
        "SOURce1:FUNCtion PULSe",
        "SOURce1:FREQuency 10000000",
        "SOURce1:FUNCtion:PULSe:WIDTh 5e-08",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 2e-08",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 90",
    ]
    assert session.events == [
        ("query", IDN_QUERY),
        ("write", "OUTPut1 OFF"),
        ("write", "SOURce1:FREQuency:MODE CW"),
        ("write", "OUTPut1:LOAD 50"),
        ("write", "SOURce1:VOLTage:UNIT VPP"),
        ("write", "SOURce1:FUNCtion:PULSe:HOLD WIDTh"),
        ("write", "SOURce1:FUNCtion:PULSe:TRANsition:BOTH MINimum"),
        ("write", "SOURce1:FUNCtion:PULSe:WIDTh MINimum"),
        ("write", "SOURce1:FUNCtion PULSe"),
        ("write", "SOURce1:FREQuency 10000000"),
        ("write", "SOURce1:FUNCtion:PULSe:WIDTh 5e-08"),
        ("query", "SOURce1:FUNCtion:PULSe:TRANsition? MAXimum"),
        ("write", "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 2e-08"),
        ("write", "SOURce1:VOLTage 0.1"),
        ("write", "SOURce1:VOLTage:OFFSet 0"),
        ("write", "UNIT:ANGLe DEGree"),
        ("write", "SOURce1:PHASe 90"),
        ("query", "OUTPut1?"),
        ("query", "SOURce1:FUNCtion?"),
        ("query", "SOURce1:FREQuency?"),
        ("query", "SOURce1:FUNCtion:PULSe:WIDTh?"),
        ("query", "SOURce1:FUNCtion:PULSe:TRANsition?"),
        ("query", "SOURce1:PHASe?"),
        ("control_ren", RENLineOperation.address_gtl),
        ("close",),
    ]
    assert "OUTPut1 ON" not in session.writes
    assert result.frequency_hz == 10_000_000.0000005
    assert result.amplitude_vpp == 0.1
    assert result.pulse_width_s == 5.005e-08
    assert result.offset_v == 0.0
    assert result.edge_time_s == 2.005e-08
    assert result.leading_edge_s == 2.005e-08
    assert result.trailing_edge_s == 2.005e-08
    assert result.phase_deg == 90.0
    assert result.load == "50"
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_configure_pulse_supports_independent_edges_and_hardware_free_preview():
    session = FakeSession(
        responses_by_command={
            "SOURce1:FUNCtion:PULSe:TRANsition:LEADing? MAXimum": "1e-6",
            "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling? MAXimum": "1e-6",
            "OUTPut1?": "0",
            "SOURce1:FUNCtion?": "PULS",
            "SOURce1:FREQuency?": "1000000",
            "SOURce1:FUNCtion:PULSe:WIDTh?": "4e-7",
            "SOURce1:FUNCtion:PULSe:TRANsition:LEADing?": "1e-7",
            "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling?": "5e-7",
            "SOURce1:PHASe?": "0",
        }
    )
    manager = FakeManager(session)

    result = configure_pulse(
        USB_RESOURCE,
        1_000_000,
        0.1,
        400e-9,
        leading_edge_s=100e-9,
        trailing_edge_s=500e-9,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [
        IDN_QUERY,
        "SOURce1:FUNCtion:PULSe:TRANsition:LEADing? MAXimum",
        "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling? MAXimum",
        "OUTPut1?",
        "SOURce1:FUNCtion?",
        "SOURce1:FREQuency?",
        "SOURce1:FUNCtion:PULSe:WIDTh?",
        "SOURce1:FUNCtion:PULSe:TRANsition:LEADing?",
        "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling?",
        "SOURce1:PHASe?",
    ]
    assert session.writes == [
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion:PULSe:HOLD WIDTh",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH MINimum",
        "SOURce1:FUNCtion:PULSe:WIDTh MINimum",
        "SOURce1:FUNCtion PULSe",
        "SOURce1:FREQuency 1000000",
        "SOURce1:FUNCtion:PULSe:WIDTh 4e-07",
        "SOURce1:FUNCtion:PULSe:TRANsition:LEADing 1e-07",
        "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling 5e-07",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    ]
    assert "OUTPut1 ON" not in session.writes
    assert result.edge_time_s is None
    assert result.leading_edge_s == 1e-7
    assert result.trailing_edge_s == 5e-7
    assert result.output_state == "off"

    preview = dry_run_pulse(
        "keysight-33521b",
        1_000_000,
        0.1,
        400e-9,
        leading_edge_s=100e-9,
        trailing_edge_s=500e-9,
    )
    assert preview.edge_time_s is None
    assert preview.leading_edge_s == 100e-9
    assert preview.trailing_edge_s == 500e-9
    assert "SOURce1:FUNCtion:PULSe:TRANsition:LEADing 1e-07" in preview.commands
    assert "SOURce1:FUNCtion:PULSe:TRANsition:TRAiling 5e-07" in preview.commands
    assert preview.executed is False


@pytest.mark.parametrize(
    (
        "responses",
        "frequency",
        "pulse_width",
        "edge_time",
        "expected_writes",
        "message",
        "expected_output_state",
        "target_edge",
    ),
    [
        (
            {
                **PULSE_RESPONSES,
                "SOURce1:FUNCtion:PULSe:TRANsition? MAXimum": "2.9e-08",
            },
            10_000_000,
            50e-9,
            30e-9,
            False,
            "exceeds instrument maximum",
            "off",
            "3e-08",
        ),
        (
            {
                **PULSE_RESPONSES,
                "SOURce1:FREQuency?": "10000000",
                "SOURce1:FUNCtion:PULSe:WIDTh?": "5e-08",
                "SOURce1:FUNCtion:PULSe:TRANsition?": "2.9e-08",
            },
            10_000_000,
            50e-9,
            30e-9,
            True,
            "readback mismatch",
            "off",
            "3e-08",
        ),
        (
            {
                **PULSE_RESPONSES,
                "OUTPut1?": "1",
            },
            10_000_000,
            50e-9,
            30e-9,
            True,
            "reported output state 'on'; expected 'off'",
            "on",
            "3e-08",
        ),
        (
            {
                **PULSE_RESPONSES,
                "SOURce1:FREQuency?": "1000",
                "SOURce1:FUNCtion:PULSe:WIDTh?": "9.995e-05",
            },
            1000,
            100e-6,
            20e-9,
            True,
            "readback mismatch",
            "off",
            "2e-08",
        ),
    ],
)
def test_configure_pulse_verification_failures(
    responses,
    frequency,
    pulse_width,
    edge_time,
    expected_writes,
    message,
    expected_output_state,
    target_edge,
):
    session = FakeSession(responses_by_command=responses)
    manager = FakeManager(session)

    with pytest.raises(WaveformVerificationError, match=message) as error:
        configure_pulse(
            USB_RESOURCE,
            frequency,
            0.1,
            pulse_width,
            0,
            edge_time,
            50,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert error.value.output_state == expected_output_state
    assert "SYSTem:ERRor?" not in session.queries
    assert (
        f"SOURce1:FUNCtion:PULSe:TRANsition:BOTH {target_edge}"
        in session.writes
    ) is expected_writes
    assert "OUTPut1 ON" not in session.writes
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_dry_run_pulse_returns_normalized_hardware_free_command_preview():
    result = dry_run_pulse(
        "  KEYSIGHT-33521B  ",
        1000,
        0.1,
        0.0001,
        0,
        1e-8,
        50,
    )

    assert tuple(signature(dry_run_pulse).parameters) == (
        "model",
        "frequency_hz",
        "amplitude_vpp",
        "pulse_width_s",
        "offset_v",
        "edge_time_s",
        "load",
        "phase_deg",
        "leading_edge_s",
        "trailing_edge_s",
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.pulse_width_s == 0.0001
    assert result.edge_time_s == 1e-8
    assert result.leading_edge_s == 1e-8
    assert result.trailing_edge_s == 1e-8
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "SOURce1:FREQuency:MODE CW",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion:PULSe:HOLD WIDTh",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH MINimum",
        "SOURce1:FUNCtion:PULSe:WIDTh MINimum",
        "SOURce1:FUNCtion PULSe",
        "SOURce1:FREQuency 1000",
        "SOURce1:FUNCtion:PULSe:WIDTh 0.0001",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 1e-08",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
        "UNIT:ANGLe DEGree",
        "SOURce1:PHASe 0",
    )
    assert result.executed is False
    assert result.output_state == "off"


def test_dry_run_pulse_rejects_invalid_timing_relationship():
    with pytest.raises(WaveformParameterError):
        dry_run_pulse(
            "keysight-33521b",
            30_000_000,
            0.1,
            100e-9,
            0,
            1e-6,
        )


def test_configure_pulse_accepts_float_equal_width_window_boundary():
    session = FakeSession(
        responses_by_command={
            **PULSE_RESPONSES,
            "SOURce1:FREQuency?": "13333333.333333336",
            "SOURce1:FUNCtion:PULSe:WIDTh?": "3.75e-08",
            "SOURce1:FUNCtion:PULSe:TRANsition?": "3e-08",
        }
    )
    manager = FakeManager(session)

    result = configure_pulse(
        USB_RESOURCE,
        13_333_333.333333336,
        0.1,
        37.5e-9,
        0,
        30e-9,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [
        IDN_QUERY,
        "SOURce1:FUNCtion:PULSe:TRANsition? MAXimum",
        "OUTPut1?",
        "SOURce1:FUNCtion?",
        "SOURce1:FREQuency?",
        "SOURce1:FUNCtion:PULSe:WIDTh?",
        "SOURce1:FUNCtion:PULSe:TRANsition?",
        "SOURce1:PHASe?",
    ]
    assert session.writes[0] == "OUTPut1 OFF"
    assert "OUTPut1 ON" not in session.writes
    assert result.frequency_hz == 13_333_333.333333336
    assert result.pulse_width_s == 37.5e-9
    assert result.edge_time_s == 30e-9
    assert result.phase_deg == 0.0
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


@pytest.mark.parametrize(
    ("frequency", "pulse_width", "edge_time", "leading_edge", "trailing_edge"),
    [
        (30_000_001, 0.0001, 1e-8, None, None),
        (1_000_000, 15e-9, 10e-9, None, None),
        (1000, 0.0001, 8e-9, None, None),
        (1_000_000, 100e-9, 100e-9, None, None),
        (1000, 0.0001, None, 20e-9, None),
        (1000, 0.0001, None, None, 20e-9),
        (1000, 0.0001, 10e-9, 20e-9, 20e-9),
    ],
)
def test_invalid_pulse_parameters_fail_before_visa_io(
    frequency, pulse_width, edge_time, leading_edge, trailing_edge
):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError):
        configure_pulse(
            USB_RESOURCE,
            frequency,
            0.1,
            pulse_width,
            0,
            edge_time,
            50,
            leading_edge_s=leading_edge,
            trailing_edge_s=trailing_edge,
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


def test_configure_dc_identifies_then_writes_safe_channel_one_sequence():
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_dc(
        USB_RESOURCE,
        1.5,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:FUNCtion DC",
        "SOURce1:VOLTage:OFFSet 1.5",
    ]
    assert not any(
        command.startswith(
            (
                "SOURce1:FREQuency",
                "SOURce1:VOLTage ",
                "SOURce1:VOLTage:UNIT",
            )
        )
        for command in session.writes
    )
    assert "OUTPut1 ON" not in session.writes
    assert result.voltage_v == 1.5
    assert result.load == "50"
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_dry_run_dc_returns_normalized_hardware_free_command_preview():
    result = dry_run_dc("  KEYSIGHT-33521B  ", 1.5, 50)

    assert tuple(signature(dry_run_dc).parameters) == (
        "model",
        "voltage_v",
        "load",
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.voltage_v == 1.5
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:FUNCtion DC",
        "SOURce1:VOLTage:OFFSet 1.5",
    )
    assert result.executed is False
    assert result.output_state == "off"


@pytest.mark.parametrize(
    ("voltage", "load"),
    [
        (5.1, 50),
        (-10.1, "high-z"),
    ],
)
def test_invalid_dc_parameters_fail_before_visa_io(voltage, load):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError):
        configure_dc(
            USB_RESOURCE,
            voltage,
            load,
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


def test_configure_noise_identifies_then_writes_safe_channel_one_sequence():
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_noise(
        USB_RESOURCE,
        0.1,
        100_000,
        0,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion NOISe",
        "SOURce1:FUNCtion:NOISe:BANDwidth 100000",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
    ]
    assert not any(
        command.startswith("SOURce1:FREQuency") for command in session.writes
    )
    assert "OUTPut1 ON" not in session.writes
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.bandwidth_hz == 100_000.0
    assert result.load == "50"
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_dry_run_noise_returns_normalized_hardware_free_command_preview():
    result = dry_run_noise(
        "  KEYSIGHT-33521B  ",
        0.1,
        1_000_000,
        0,
        50,
    )

    assert tuple(signature(dry_run_noise).parameters) == (
        "model",
        "amplitude_vpp",
        "bandwidth_hz",
        "offset_v",
        "load",
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.bandwidth_hz == 1_000_000.0
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion NOISe",
        "SOURce1:FUNCtion:NOISe:BANDwidth 1000000",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
    )
    assert result.executed is False
    assert result.output_state == "off"


@pytest.mark.parametrize("bandwidth", [0.0009, 30_000_001])
def test_invalid_noise_bandwidth_fails_before_visa_io(bandwidth):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError):
        configure_noise(
            USB_RESOURCE,
            0.1,
            bandwidth,
            0,
            50,
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


def test_configure_prbs_identifies_then_writes_safe_channel_one_sequence():
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_prbs(
        USB_RESOURCE,
        1_000_000,
        0.1,
        " pn15 ",
        0,
        1e-8,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion PRBS",
        "SOURce1:FUNCtion:PRBS:BRATe 1000000",
        "SOURce1:FUNCtion:PRBS:DATA PN15",
        "SOURce1:FUNCtion:PRBS:TRANsition:BOTH 1e-08",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
    ]
    assert not any(
        command.startswith("SOURce1:FREQuency") for command in session.writes
    )
    assert "OUTPut1 ON" not in session.writes
    assert result.bit_rate_bps == 1_000_000.0
    assert result.amplitude_vpp == 0.1
    assert result.pattern == "PN15"
    assert result.offset_v == 0.0
    assert result.edge_time_s == 1e-8
    assert result.load == "50"
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_dry_run_prbs_returns_normalized_hardware_free_command_preview():
    result = dry_run_prbs(
        "  KEYSIGHT-33521B  ",
        1_000_000,
        0.1,
        "pn9",
        0,
        8.4e-9,
        50,
    )

    assert tuple(signature(dry_run_prbs).parameters) == (
        "model",
        "bit_rate_bps",
        "amplitude_vpp",
        "pattern",
        "offset_v",
        "edge_time_s",
        "load",
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.bit_rate_bps == 1_000_000.0
    assert result.amplitude_vpp == 0.1
    assert result.pattern == "PN9"
    assert result.offset_v == 0.0
    assert result.edge_time_s == 8.4e-9
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion PRBS",
        "SOURce1:FUNCtion:PRBS:BRATe 1000000",
        "SOURce1:FUNCtion:PRBS:DATA PN9",
        "SOURce1:FUNCtion:PRBS:TRANsition:BOTH 8.4e-09",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
    )
    assert result.executed is False
    assert result.output_state == "off"


def test_dry_run_prbs_rejects_edge_time_longer_than_bit_period():
    with pytest.raises(WaveformParameterError):
        dry_run_prbs(
            "keysight-33521b",
            50_000_000,
            0.1,
            "PN7",
            0,
            21e-9,
        )


@pytest.mark.parametrize(
    ("bit_rate", "pattern", "edge_time"),
    [
        (50_000_001, "PN7", 8.4e-9),
        (1_000_000, "PN13", 8.4e-9),
        (1_000_000, "PN7", 8e-9),
        (50_000_000, "PN7", 21e-9),
    ],
)
def test_invalid_prbs_parameters_fail_before_visa_io(
    bit_rate, pattern, edge_time
):
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError):
        configure_prbs(
            USB_RESOURCE,
            bit_rate,
            0.1,
            pattern,
            0,
            edge_time,
            50,
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


@pytest.mark.parametrize("state", ["on", "off"])
def test_output_identifies_then_writes_only_requested_state(state):
    session = FakeSession()
    manager = FakeManager(session)

    result = set_output(
        USB_RESOURCE,
        state,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [f"OUTPut1 {state.upper()}"]
    assert result.output_state == state
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_control_rejects_unsupported_model_before_any_write():
    session = FakeSession(response="Keysight Technologies,33522B,SERIAL,FIRMWARE")
    manager = FakeManager(session)

    with pytest.raises(UnsupportedInstrumentError):
        set_output(
            USB_RESOURCE,
            "on",
            resource_manager_factory=RecordingFactory(manager),
        )

    assert session.queries == [IDN_QUERY]
    assert session.writes == []
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_control_write_failure_is_domain_error_and_resources_are_closed():
    session = FakeSession(write_error=RuntimeError("private write detail"))
    manager = FakeManager(session)

    with pytest.raises(VisaWriteError):
        set_output(
            USB_RESOURCE,
            "off",
            resource_manager_factory=RecordingFactory(manager),
        )

    assert session.writes == ["OUTPut1 OFF"]
    assert session.close_calls == 1
    assert manager.close_calls == 1

    pulse_session = FakeSession(write_error=RuntimeError("private pulse write detail"))
    pulse_manager = FakeManager(pulse_session)

    with pytest.raises(VisaWriteError) as pulse_error:
        configure_pulse(
            USB_RESOURCE,
            1000,
            0.1,
            100e-9,
            resource_manager_factory=RecordingFactory(pulse_manager),
        )

    assert pulse_error.value.output_state is None
    assert isinstance(pulse_error.value.__cause__, RuntimeError)
    assert pulse_session.writes == ["OUTPut1 OFF"]
    assert pulse_session.close_calls == 1
    assert pulse_manager.close_calls == 1


def test_output_on_cleanup_failure_preserves_possible_output_state():
    session = FakeSession(close_error=RuntimeError("session close failed"))
    manager = FakeManager(session)

    with pytest.raises(VisaCleanupError) as error:
        set_output(
            USB_RESOURCE,
            "on",
            resource_manager_factory=RecordingFactory(manager),
        )

    assert error.value.output_state == "on"
    assert "Channel 1 output may remain on" in str(error.value)
    assert session.writes == ["OUTPut1 ON"]
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_invalid_output_state_is_domain_error_before_visa_io():
    manager = FakeManager()
    factory = RecordingFactory(manager)

    with pytest.raises(WaveformParameterError, match="on or off"):
        set_output(
            USB_RESOURCE,
            "enabled",
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


@pytest.mark.parametrize(
    (
        "function_response",
        "extra_responses",
        "expected_queries",
        "expected_frequency",
        "expected_amplitude",
        "expected_unit",
        "expected_bandwidth",
        "expected_offset",
    ),
    [
        (
            " sin ",
            {},
            [
                IDN_QUERY,
                "OUTPut1?",
                "SOURce1:FUNCtion?",
                "SOURce1:VOLTage:OFFSet?",
                "OUTPut1:LOAD?",
                "SOURce1:FREQuency?",
                "SOURce1:VOLTage:UNIT?",
                "SOURce1:VOLTage?",
            ],
            1000.0,
            0.1,
            "VPP",
            None,
            0.0,
        ),
        (
            " DC ",
            {"SOURce1:VOLTage:OFFSet?": "1.5"},
            [
                IDN_QUERY,
                "OUTPut1?",
                "SOURce1:FUNCtion?",
                "SOURce1:VOLTage:OFFSet?",
                "OUTPut1:LOAD?",
            ],
            None,
            None,
            None,
            None,
            1.5,
        ),
        (
            " NOIS ",
            {"SOURce1:FUNCtion:NOISe:BANDwidth?": "200000"},
            [
                IDN_QUERY,
                "OUTPut1?",
                "SOURce1:FUNCtion?",
                "SOURce1:VOLTage:OFFSet?",
                "OUTPut1:LOAD?",
                "SOURce1:VOLTage:UNIT?",
                "SOURce1:VOLTage?",
                "SOURce1:FUNCtion:NOISe:BANDwidth?",
            ],
            None,
            0.1,
            "VPP",
            200000.0,
            0.0,
        ),
    ],
)
def test_status_uses_one_session_and_parses_mode_aware_channel_one_state(
    function_response,
    extra_responses,
    expected_queries,
    expected_frequency,
    expected_amplitude,
    expected_unit,
    expected_bandwidth,
    expected_offset,
):
    responses = {
        **STATUS_RESPONSES,
        "SOURce1:FUNCtion?": function_response,
        **extra_responses,
    }
    session = FakeSession(
        response="Agilent Technologies,33521B,SERIAL,FIRMWARE",
        responses_by_command=responses,
    )
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    result = query_status(
        USB_RESOURCE,
        resource_manager_factory=factory,
    )

    assert factory.calls == ["@ivi"]
    assert manager.opened_resources == [USB_RESOURCE]
    assert session.queries == expected_queries
    assert session.writes == []
    assert result.identity.manufacturer == "Agilent Technologies"
    assert result.output_state == "off"
    assert result.function == function_response.strip().upper()
    assert result.frequency_hz == expected_frequency
    assert result.amplitude == expected_amplitude
    assert result.amplitude_unit == expected_unit
    assert result.bandwidth_hz == expected_bandwidth
    assert result.offset_v == expected_offset
    assert result.load == "high-z"
    assert session.close_calls == 1
    assert manager.close_calls == 1


@pytest.mark.parametrize(
    ("frequency_response", "query_error"),
    [
        ("not-a-number", None),
        ("1000", TimeoutError("private status timeout")),
    ],
)
def test_status_query_or_numeric_parse_failure_is_domain_error_and_closes(
    frequency_response, query_error
):
    responses = dict(STATUS_RESPONSES)
    responses["SOURce1:FREQuency?"] = frequency_response
    query_errors = (
        {"SOURce1:FREQuency?": query_error}
        if query_error is not None
        else {}
    )
    session = FakeSession(
        responses_by_command=responses,
        query_errors_by_command=query_errors,
    )
    manager = FakeManager(session)

    with pytest.raises(StatusQueryError):
        query_status(
            USB_RESOURCE,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert session.writes == []
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_status_rejects_unsupported_model_before_status_queries():
    session = FakeSession(
        response="Keysight Technologies,33522B,SERIAL,FIRMWARE",
        responses_by_command=STATUS_RESPONSES,
    )
    manager = FakeManager(session)

    with pytest.raises(UnsupportedInstrumentError):
        query_status(
            USB_RESOURCE,
            resource_manager_factory=RecordingFactory(manager),
        )

    assert session.queries == [IDN_QUERY]
    assert session.writes == []
    assert session.close_calls == 1
    assert manager.close_calls == 1

# ------------

def test_read_error_queue_live_drains_fifo():
    """Drain the error queue FIFO with comma-in-message, sentinel, read_count."""
    error_responses = [
        '-100,"Data out of range"',
        '-222,"Data out of range, value clipped to upper limit"',
        '+0,"No error"',
    ]
    session = FakeErrorQueueSession(error_responses)
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    result = read_error_queue(
        USB_RESOURCE,
        resource_manager_factory=factory,
    )

    assert result.read_count == 3
    assert result.max_reads == 20
    assert result.empty_confirmed is True
    assert result.limit_reached is False
    assert len(result.errors) == 2
    assert result.errors[0].code == -100
    assert result.errors[0].message == "Data out of range"
    assert result.errors[1].code == -222
    assert result.errors[1].message == "Data out of range, value clipped to upper limit"
    assert result.errors[1].raw_response == '-222,"Data out of range, value clipped to upper limit"'
    assert session.queries == [IDN_QUERY, "SYSTem:ERRor?", "SYSTem:ERRor?", "SYSTem:ERRor?"]
    assert session.writes == []
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_read_error_queue_max_reads_cap():
    """Stops at max_reads without confirming that the queue is empty."""
    # Queue longer than max_reads, no sentinel
    session = FakeErrorQueueSession(
        ['-100,"A"', '-200,"B"', '-300,"C"', '-400,"D"', '-500,"E"'],
    )
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    result = read_error_queue(
        USB_RESOURCE,
        resource_manager_factory=factory,
        max_reads=3,
    )

    assert result.read_count == 3
    assert result.max_reads == 3
    assert result.empty_confirmed is False
    assert result.limit_reached is True
    assert len(result.errors) == 3
    assert result.errors[0].code == -100
    assert result.errors[1].code == -200
    assert result.errors[2].code == -300
    assert session.close_calls == 1
    assert manager.close_calls == 1


@pytest.mark.parametrize("bad_max_reads", [0, 101, True])
def test_read_error_queue_rejects_invalid_max_reads_before_manager(bad_max_reads):
    """Invalid max_reads must be rejected before any ResourceManager is created."""
    session = FakeSession()
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    with pytest.raises(ValueError, match="must be an integer between 1 and 100"):
        read_error_queue(
            USB_RESOURCE,
            resource_manager_factory=factory,
            max_reads=bad_max_reads,
        )

    # RecordingFactory was never called; manager must not have been opened
    assert factory.calls == []
    assert manager.open_calls == []
    assert manager.close_calls == 0
    assert session.queries == []
    assert session.close_calls == 0


def test_read_error_queue_rejects_unsupported_idn_before_queue():
    """Unsupported IDN must stop before SYSTem:ERRor? is sent."""
    session = FakeSession(response="Keysight Technologies,33522B,SERIAL,FIRMWARE")
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    with pytest.raises(UnsupportedInstrumentError):
        read_error_queue(
            USB_RESOURCE,
            resource_manager_factory=factory,
        )

    assert session.queries == [IDN_QUERY]
    assert session.writes == []
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_read_error_queue_malformed_response_raises_and_closes():
    """Malformed SYSTem:ERRor? raises ErrorQueueQueryError and cleans up."""
    session = FakeErrorQueueSession(["not a valid response"])
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    with pytest.raises(ErrorQueueQueryError, match="Malformed SYSTem:ERRor?"):
        read_error_queue(
            USB_RESOURCE,
            resource_manager_factory=factory,
        )

    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_read_error_queue_query_failure_chains_and_closes():
    """Query failure on SYSTem:ERRor? raises ErrorQueueQueryError, chains cause, cleans up."""
    session = FakeSession(
        query_errors_by_command={"SYSTem:ERRor?": TimeoutError("query timed out")},
        close_error=RuntimeError("session close failed"),
    )
    manager = FakeManager(session, close_error=RuntimeError("manager close failed"))
    factory = RecordingFactory(manager)

    with pytest.raises(ErrorQueueQueryError, match="failed or timed out") as caught:
        read_error_queue(
            USB_RESOURCE,
            resource_manager_factory=factory,
        )

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert caught.value.cleanup_errors == (
        "session close failed",
        "ResourceManager close failed",
    )
    assert session.queries == [IDN_QUERY, "SYSTem:ERRor?"]
    assert session.close_calls == 1
    assert manager.close_calls == 1
