from inspect import signature

import pytest

from wavegen_tool_core.errors import (
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
    configure_sine,
    configure_square,
    dry_run_sine,
    dry_run_square,
    identify_instrument,
    list_resources,
    normalize_serial_baud_rate,
    normalize_serial_termination,
    query_status,
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
    ):
        self.response = response
        self.query_error = query_error
        self.responses_by_command = responses_by_command or {}
        self.query_errors_by_command = query_errors_by_command or {}
        self.write_error = write_error
        self.close_error = close_error
        self.timeout = None
        self.baud_rate = 4800
        self.read_termination = "existing read"
        self.write_termination = "existing write"
        self.queries = []
        self.writes = []
        self.close_calls = 0

    def query(self, command):
        self.queries.append(command)
        if command in self.query_errors_by_command:
            raise self.query_errors_by_command[command]
        if self.query_error is not None:
            raise self.query_error
        return self.responses_by_command.get(command, self.response)

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error

    def write(self, command):
        self.writes.append(command)
        if self.write_error is not None:
            raise self.write_error

    def clear(self):
        raise AssertionError("clear must not be called")

    def control_ren(self, mode):
        raise AssertionError(f"control_ren must not be called: {mode}")

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
    ):
        self.session = session or FakeSession()
        self.resources = resources
        self.list_error = list_error
        self.sessions_by_resource = sessions_by_resource or {}
        self.open_errors = open_errors or {}
        self.open_error = open_error
        self.close_error = close_error
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
    assert tcpip_session.close_calls == 1
    assert result.resources == (ResourceListEntry(TCPIP_RESOURCE),)


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


def test_system_backend_accepts_tcpip():
    manager = FakeManager()
    factory = RecordingFactory(manager)

    result = identify_instrument(TCPIP_RESOURCE, "system", resource_manager_factory=factory)

    assert factory.calls == ["@ivi"]
    assert manager.opened_resources == [TCPIP_RESOURCE]
    assert result.backend == "system"
    assert result.transport == "tcpip"


def test_pyvisa_py_backend_accepts_tcpip_without_fallback():
    manager = FakeManager()
    factory = RecordingFactory(manager)

    result = identify_instrument(TCPIP_RESOURCE, "@py", resource_manager_factory=factory)

    assert factory.calls == ["@py"]
    assert manager.opened_resources == [TCPIP_RESOURCE]
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
    )
    manager = FakeManager(session, close_error=RuntimeError("manager close failed"))

    with pytest.raises(IdnQueryError) as error:
        identify_instrument(USB_RESOURCE, resource_manager_factory=RecordingFactory(manager))

    assert error.value.cleanup_errors == (
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
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SIN",
        "SOURce1:FREQuency 1000",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
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
    )
    assert result.model == "33521B"
    assert result.canonical_model_id == "keysight-33521b"
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.offset_v == 0.0
    assert result.load == "50"
    assert result.commands == (
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SIN",
        "SOURce1:FREQuency 1000",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
    )
    assert result.executed is False
    assert result.output_state == "off"


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
    ("frequency", "amplitude", "offset", "load"),
    [
        (0, 0.1, 0, 50),
        (30_000_001, 0.1, 0, 50),
        (1000, 0.0009, 0, 50),
        (1000, 10.1, 0, 50),
        (1000, 10, 0.001, 50),
        (True, 0.1, 0, 50),
        (float("nan"), 0.1, 0, 50),
        (float("inf"), 0.1, 0, 50),
        (float("-inf"), 0.1, 0, 50),
        ("not-a-number", 0.1, 0, 50),
    ],
)
def test_invalid_sine_parameters_fail_before_visa_io(
    frequency, amplitude, offset, load
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
            resource_manager_factory=factory,
        )

    assert factory.calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.session.writes == []


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
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SQUare",
        "SOURce1:FREQuency 30000000",
        "SOURce1:FUNCtion:SQUare:DCYCle 48",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
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
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion SQUare",
        "SOURce1:FREQuency 1000",
        "SOURce1:FUNCtion:SQUare:DCYCle 50",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
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
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion RAMP",
        "SOURce1:FREQuency 1000",
        "SOURce1:FUNCtion:RAMP:SYMMetry 25",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
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
    session = FakeSession()
    manager = FakeManager(session)

    result = configure_pulse(
        USB_RESOURCE,
        1000,
        0.1,
        0.0001,
        0,
        0.00000001,
        50,
        resource_manager_factory=RecordingFactory(manager),
    )

    assert session.queries == [IDN_QUERY]
    assert session.writes == [
        "OUTPut1 OFF",
        "OUTPut1:LOAD 50",
        "SOURce1:VOLTage:UNIT VPP",
        "SOURce1:FUNCtion PULSe",
        "SOURce1:FREQuency 1000",
        "SOURce1:FUNCtion:PULSe:WIDTh 0.0001",
        "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 1e-08",
        "SOURce1:VOLTage 0.1",
        "SOURce1:VOLTage:OFFSet 0",
    ]
    assert "OUTPut1 ON" not in session.writes
    assert result.frequency_hz == 1000.0
    assert result.amplitude_vpp == 0.1
    assert result.pulse_width_s == 0.0001
    assert result.offset_v == 0.0
    assert result.edge_time_s == 1e-8
    assert result.load == "50"
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


def test_configure_pulse_accepts_float_equal_width_window_boundary():
    session = FakeSession()
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

    assert session.queries == [IDN_QUERY]
    assert session.writes[0] == "OUTPut1 OFF"
    assert "OUTPut1 ON" not in session.writes
    assert result.frequency_hz == 13_333_333.333333336
    assert result.pulse_width_s == 37.5e-9
    assert result.edge_time_s == 30e-9
    assert result.output_state == "off"
    assert session.close_calls == 1
    assert manager.close_calls == 1


@pytest.mark.parametrize(
    ("frequency", "pulse_width", "edge_time"),
    [
        (30_000_001, 0.0001, 1e-8),
        (1_000_000, 15e-9, 10e-9),
        (1000, 0.0001, 8e-9),
        (1_000_000, 100e-9, 100e-9),
    ],
)
def test_invalid_pulse_parameters_fail_before_visa_io(
    frequency, pulse_width, edge_time
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


def test_status_uses_one_session_and_parses_read_only_channel_one_state():
    session = FakeSession(
        response="Agilent Technologies,33521B,SERIAL,FIRMWARE",
        responses_by_command=STATUS_RESPONSES,
    )
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    result = query_status(
        USB_RESOURCE,
        resource_manager_factory=factory,
    )

    assert factory.calls == ["@ivi"]
    assert manager.opened_resources == [USB_RESOURCE]
    assert session.queries == [
        IDN_QUERY,
        "OUTPut1?",
        "SOURce1:FUNCtion?",
        "SOURce1:FREQuency?",
        "SOURce1:VOLTage:UNIT?",
        "SOURce1:VOLTage?",
        "SOURce1:VOLTage:OFFSet?",
        "OUTPut1:LOAD?",
    ]
    assert session.writes == []
    assert result.identity.manufacturer == "Agilent Technologies"
    assert result.output_state == "off"
    assert result.function == "SIN"
    assert result.frequency_hz == 1000.0
    assert result.amplitude == 0.1
    assert result.amplitude_unit == "VPP"
    assert result.offset_v == 0.0
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
