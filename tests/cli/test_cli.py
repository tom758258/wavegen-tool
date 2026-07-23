import json

import pytest

import wavegen_tool_cli.cli as cli_module
from wavegen_tool_cli.cli import ExitCode, main
from wavegen_tool_core import visa


USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
TCPIP_RESOURCE = "TCPIP0::192.0.2.10::inst0::INSTR"
VALID_IDN = "KEYSIGHT TECHNOLOGIES,33521B,MY00000000,1.00-0.00-0.00"


class FakeSession:
    def __init__(self, response=VALID_IDN, *, query_error=None):
        self.response = response
        self.query_error = query_error
        self.timeout = None
        self.queries = []
        self.closed = False

    def query(self, command):
        self.queries.append(command)
        if self.query_error is not None:
            raise self.query_error
        return self.response

    def close(self):
        self.closed = True


class FakeManager:
    def __init__(self, session=None, *, open_error=None):
        self.session = session or FakeSession()
        self.open_error = open_error
        self.closed = False
        self.opened_resources = []

    def open_resource(self, resource):
        self.opened_resources.append(resource)
        if self.open_error is not None:
            raise self.open_error
        return self.session

    def close(self):
        self.closed = True


def install_fake_manager(monkeypatch, manager):
    calls = []

    def factory(pyvisa_library):
        calls.append(pyvisa_library)
        return manager

    monkeypatch.setattr(visa, "create_resource_manager", factory)
    return calls


def test_root_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == ExitCode.SUCCESS
    assert "identify" in capsys.readouterr().out


def test_identify_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(["identify", "--help"])

    assert error.value.code == ExitCode.SUCCESS
    output = capsys.readouterr().out
    assert "--resource" in output
    assert "--backend" in output
    assert "--json" in output
    assert "{system,@py}" not in output


def test_missing_resource_is_usage_error_without_traceback(capsys):
    with pytest.raises(SystemExit) as error:
        main(["identify"])

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert "required" in captured.err
    assert "Traceback" not in captured.err


def test_valid_fake_identify_human_output(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(["identify", "--resource", USB_RESOURCE])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert calls == ["@ivi"]
    assert "Instrument identified as a recognized model." in captured.out
    assert "Manufacturer: Keysight Technologies" in captured.out
    assert "Model: 33521B" in captured.out
    assert "Canonical model ID: keysight-33521b" in captured.out
    assert "Model recognized: yes" in captured.out
    assert "supported" not in captured.out.casefold()
    assert captured.err == ""
    assert manager.session.queries == ["*IDN?"]
    assert manager.session.closed is True
    assert manager.closed is True


def test_valid_fake_identify_json_stdout_is_one_object(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["identify", "--resource", TCPIP_RESOURCE, "--backend", "@py", "--json"]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == ["@py"]
    assert payload == {
        "success": True,
        "backend": "@py",
        "transport": "tcpip",
        "manufacturer": "Keysight Technologies",
        "model": "33521B",
        "serial": "MY00000000",
        "firmware": "1.00-0.00-0.00",
        "canonical_model_id": "keysight-33521b",
        "model_supported": True,
        "error": None,
    }
    assert "supported" not in payload
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_invalid_backend_human_error_does_not_create_manager(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["identify", "--resource", TCPIP_RESOURCE, "--backend", "invalid-backend"]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.opened_resources == []
    assert captured.out == ""
    assert "Error [unsupported_backend]" in captured.err
    assert "Traceback" not in captured.err
    assert "usage:" not in captured.err


def test_invalid_backend_json_is_one_object_without_fallback(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "identify",
            "--resource",
            TCPIP_RESOURCE,
            "--backend",
            "invalid-backend",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.opened_resources == []
    assert payload["model_supported"] is False
    assert "supported" not in payload
    assert payload["error"].startswith("unsupported_backend:")
    assert captured.out.count("\n") == 1
    assert "usage:" not in captured.out
    assert captured.err == ""


def test_pyvisa_py_usb_human_error_is_fail_closed(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["identify", "--resource", USB_RESOURCE, "--backend", "@py"]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.UNSUPPORTED_CONNECTION_SCOPE
    assert calls == []
    assert manager.opened_resources == []
    assert manager.session.queries == []
    assert captured.out == ""
    assert "Error [unsupported_connection_scope]" in captured.err
    assert "USB resources are supported with the 'system' backend" in captured.err
    assert "Traceback" not in captured.err


def test_pyvisa_py_usb_json_error_is_one_object(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["identify", "--resource", USB_RESOURCE, "--backend", "@py", "--json"]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.UNSUPPORTED_CONNECTION_SCOPE
    assert calls == []
    assert manager.opened_resources == []
    assert payload["backend"] == "@py"
    assert payload["transport"] == "usb"
    assert payload["model_supported"] is False
    assert "supported" not in payload
    assert payload["error"].startswith("unsupported_connection_scope:")
    assert captured.out.count("\n") == 1
    assert "usage:" not in captured.out
    assert captured.err == ""


def test_unsupported_transport_has_stable_exit_and_json(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["identify", "--resource", "GPIB0::10::INSTR", "--json"]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.UNSUPPORTED_TRANSPORT
    assert calls == []
    assert payload["success"] is False
    assert payload["transport"] == "gpib"
    assert payload["model_supported"] is False
    assert "supported" not in payload
    assert payload["error"].startswith("unsupported_transport:")
    assert captured.err == ""


def test_resource_manager_error_has_stable_exit(monkeypatch, capsys):
    def failing_factory(pyvisa_library):
        raise RuntimeError(f"private manager detail for {pyvisa_library!r}")

    monkeypatch.setattr(visa, "create_resource_manager", failing_factory)

    exit_code = main(["identify", "--resource", USB_RESOURCE, "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.RESOURCE_MANAGER_ERROR
    assert payload["error"].startswith("resource_manager_error:")
    assert "private manager detail" not in captured.out
    assert captured.err == ""


def test_malformed_idn_has_stable_exit(monkeypatch, capsys):
    install_fake_manager(monkeypatch, FakeManager(FakeSession("too,few,fields")))

    exit_code = main(["identify", "--resource", USB_RESOURCE, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.MALFORMED_IDN
    assert payload["error"].startswith("malformed_idn:")
    assert payload["manufacturer"] is None


def test_unsupported_model_has_stable_exit(monkeypatch, capsys):
    response = "Keysight Technologies,33522B,MY00000000,1.00"
    install_fake_manager(monkeypatch, FakeManager(FakeSession(response)))

    exit_code = main(["identify", "--resource", USB_RESOURCE, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.UNSUPPORTED_INSTRUMENT
    assert payload["model"] == "33522B"
    assert payload["serial"] is None
    assert payload["firmware"] is None
    assert payload["canonical_model_id"] is None
    assert payload["model_supported"] is False
    assert "supported" not in payload
    assert payload["error"].startswith("unsupported_instrument:")


def test_internal_error_json_uses_model_supported(monkeypatch, capsys):
    def unexpected_failure(resource, backend):
        raise RuntimeError("private internal detail")

    monkeypatch.setattr(cli_module, "identify_instrument", unexpected_failure)

    exit_code = main(["identify", "--resource", USB_RESOURCE, "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.INTERNAL_ERROR
    assert payload["model_supported"] is False
    assert "supported" not in payload
    assert payload["error"] == "internal_error: unexpected internal failure"
    assert "private internal detail" not in captured.out
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_visa_open_error_has_stable_exit_without_traceback(monkeypatch, capsys):
    manager = FakeManager(open_error=RuntimeError("private backend detail"))
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["identify", "--resource", USB_RESOURCE])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.RESOURCE_OPEN_ERROR
    assert "Error [resource_open_error]" in captured.err
    assert "private backend detail" not in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert manager.closed is True


def test_query_error_has_stable_exit_without_traceback(monkeypatch, capsys):
    manager = FakeManager(FakeSession(query_error=TimeoutError("private timeout detail")))
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["identify", "--resource", USB_RESOURCE])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.IDN_QUERY_ERROR
    assert "Error [idn_query_error]" in captured.err
    assert "private timeout detail" not in captured.err
    assert "Traceback" not in captured.err
    assert manager.session.closed is True
    assert manager.closed is True


def test_exit_code_contract_is_centralized():
    assert ExitCode.SUCCESS == 0
    assert ExitCode.CLI_USAGE == 2
    assert ExitCode.UNSUPPORTED_TRANSPORT == 10
    assert ExitCode.UNSUPPORTED_CONNECTION_SCOPE == 11
    assert ExitCode.RESOURCE_MANAGER_ERROR == 20
    assert ExitCode.RESOURCE_OPEN_ERROR == 21
    assert ExitCode.IDN_QUERY_ERROR == 22
    assert ExitCode.MALFORMED_IDN == 23
    assert ExitCode.UNSUPPORTED_INSTRUMENT == 24
    assert ExitCode.VISA_CLEANUP_ERROR == 25
