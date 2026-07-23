import pytest

from wavegen_tool_core.errors import (
    IdnQueryError,
    MalformedIdnError,
    ResourceManagerError,
    ResourceOpenError,
    UnsupportedInstrumentError,
    VisaCleanupError,
)
from wavegen_tool_core.visa import DEFAULT_TIMEOUT_MS, IDN_QUERY, identify_instrument


USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
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
    def __init__(self, session=None, *, open_error=None, close_error=None):
        self.session = session or FakeSession()
        self.open_error = open_error
        self.close_error = close_error
        self.opened_resources = []
        self.close_calls = 0

    def open_resource(self, resource):
        self.opened_resources.append(resource)
        if self.open_error is not None:
            raise self.open_error
        return self.session

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


def test_system_backend_lifecycle_queries_once_and_closes():
    session = FakeSession()
    manager = FakeManager(session)
    factory = RecordingFactory(manager)

    result = identify_instrument(USB_RESOURCE, "system", resource_manager_factory=factory)

    assert factory.calls == [None]
    assert manager.opened_resources == [USB_RESOURCE]
    assert session.timeout == DEFAULT_TIMEOUT_MS
    assert session.queries == [IDN_QUERY]
    assert session.close_calls == 1
    assert manager.close_calls == 1
    assert result.backend == "system"
    assert result.transport == "usb"
    assert result.identity.canonical_model_id == "keysight-33521b"


def test_pyvisa_py_backend_is_passed_exactly_without_fallback():
    manager = FakeManager()
    factory = RecordingFactory(manager)

    result = identify_instrument(USB_RESOURCE, "@py", resource_manager_factory=factory)

    assert factory.calls == ["@py"]
    assert result.backend == "@py"


def test_resource_manager_creation_failure_is_distinct():
    calls = []

    def failing_factory(pyvisa_library):
        calls.append(pyvisa_library)
        raise RuntimeError("manager failed")

    with pytest.raises(ResourceManagerError) as error:
        identify_instrument(USB_RESOURCE, "system", resource_manager_factory=failing_factory)

    assert calls == [None]
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
