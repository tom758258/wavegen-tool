import pytest

from wavegen_tool_core.errors import (
    IdnQueryError,
    MalformedIdnError,
    ResourceDiscoveryError,
    ResourceManagerError,
    ResourceOpenError,
    UnsupportedConnectionScopeError,
    UnsupportedInstrumentError,
    UnsupportedTransportError,
    VisaCleanupError,
)
from wavegen_tool_core.visa import (
    DEFAULT_TIMEOUT_MS,
    IDN_QUERY,
    LIVE_VERIFY_TIMEOUT_MS,
    identify_instrument,
    list_resources,
)


USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
TCPIP_RESOURCE = "TCPIP0::192.0.2.10::inst0::INSTR"
VALID_IDN = "KEYSIGHT TECHNOLOGIES,33521B,MY00000000,1.00-0.00-0.00"


class FakeSession:
    def __init__(self, response=VALID_IDN, *, query_error=None, close_error=None):
        self.response = response
        self.query_error = query_error
        self.close_error = close_error
        self.timeout = None
        self.queries = []
        self.close_calls = 0

    def query(self, command):
        self.queries.append(command)
        if self.query_error is not None:
            raise self.query_error
        return self.response

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


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
        self.close_calls = 0

    def list_resources(self):
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return self.resources

    def open_resource(self, resource):
        self.opened_resources.append(resource)
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
    resources = (TCPIP_RESOURCE, "GPIB0::10::INSTR", USB_RESOURCE)
    manager = FakeManager(resources=resources)
    factory = RecordingFactory(manager)

    result = list_resources(backend, resource_manager_factory=factory)

    assert factory.calls == [library]
    assert manager.list_calls == 1
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert manager.close_calls == 1
    assert result.backend == backend
    assert result.resources == resources


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


def test_system_live_only_verifies_usb_and_tcpip_and_skips_other_transports():
    asrl = "ASRL6::INSTR"
    gpib = "GPIB0::10::INSTR"
    pxi = "PXI0::0::INSTR"
    vxi = "VXI0::1::INSTR"
    unknown = "SOME0::VALUE::INSTR"
    resources = (asrl, TCPIP_RESOURCE, gpib, USB_RESOURCE, pxi, vxi, unknown)
    tcpip_session = FakeSession(response="Vendor,Model,Serial,Firmware")
    usb_session = FakeSession(response="not,a,required,identity")
    manager = FakeManager(
        resources=resources,
        sessions_by_resource={
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
    assert manager.opened_resources == [TCPIP_RESOURCE, USB_RESOURCE]
    assert tcpip_session.timeout == LIVE_VERIFY_TIMEOUT_MS
    assert usb_session.timeout == LIVE_VERIFY_TIMEOUT_MS
    assert tcpip_session.queries == [IDN_QUERY]
    assert usb_session.queries == [IDN_QUERY]
    assert tcpip_session.close_calls == 1
    assert usb_session.close_calls == 1
    assert manager.close_calls == 1
    assert result.resources == (TCPIP_RESOURCE, USB_RESOURCE)


def test_pyvisa_py_live_only_verifies_tcpip_and_skips_usb():
    tcpip_session = FakeSession(response="any non-empty response")
    manager = FakeManager(
        resources=(USB_RESOURCE, TCPIP_RESOURCE),
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
    assert result.resources == (TCPIP_RESOURCE,)


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
    assert result.resources == (live,)


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
