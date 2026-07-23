import json

import pytest

import wavegen_tool_cli.cli as cli_module
from wavegen_tool_cli.cli import ExitCode, main
from wavegen_tool_core import visa


ASRL_RESOURCE = "ASRL6::INSTR"
USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
TCPIP_RESOURCE = "TCPIP0::192.0.2.10::inst0::INSTR"


class FakeSession:
    def __init__(self, response="response", *, query_error=None, close_error=None):
        self.response = response
        self.query_error = query_error
        self.close_error = close_error
        self.timeout = None
        self.baud_rate = 4800
        self.read_termination = "existing read"
        self.write_termination = "existing write"
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

    def write(self, command):
        raise AssertionError(f"write must not be called: {command}")

    def clear(self):
        raise AssertionError("clear must not be called")

    def control_ren(self, mode):
        raise AssertionError(f"control_ren must not be called: {mode}")

    def read_stb(self):
        raise AssertionError("read_stb must not be called")


class FakeManager:
    def __init__(
        self,
        resources=(),
        *,
        sessions_by_resource=None,
        list_error=None,
        close_error=None,
    ):
        self.resources = resources
        self.sessions_by_resource = sessions_by_resource or {}
        self.list_error = list_error
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
        return self.sessions_by_resource[resource]

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def install_fake_manager(monkeypatch, manager):
    calls = []

    def factory(pyvisa_library):
        calls.append(pyvisa_library)
        return manager

    monkeypatch.setattr(visa, "create_resource_manager", factory)
    return calls


def test_root_help_includes_list_resources(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == ExitCode.SUCCESS
    assert "list-resources" in capsys.readouterr().out


def test_list_resources_help_uses_live_only(capsys):
    with pytest.raises(SystemExit) as error:
        main(["list-resources", "--help"])

    output = capsys.readouterr().out
    assert error.value.code == ExitCode.SUCCESS
    assert "--live-only" in output
    assert "--live " not in output
    assert "--backend" in output
    assert "--json" in output
    assert "--serial-baud-rate" in output
    assert "--serial-read-termination" in output
    assert "--serial-write-termination" in output
    assert "{system,@py}" not in output


def test_old_live_option_is_unknown_argument_without_creating_manager(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(["list-resources", "--live"])

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.list_calls == 0
    assert "unrecognized arguments: --live" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--serial-baud-rate", "0"],
        ["--serial-baud-rate", "invalid"],
        ["--serial-read-termination", "TAB"],
        ["--serial-write-termination", "newline"],
    ],
)
def test_invalid_serial_option_is_usage_error_without_creating_manager(
    monkeypatch, capsys, arguments
):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(["list-resources", *arguments])

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.list_calls == 0
    assert manager.opened_resources == []
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("backend_args", "library", "backend"),
    [
        ([], "@ivi", "system"),
        (["--backend", "@py"], "@py", "@py"),
    ],
)
def test_raw_listing_human_preserves_all_resources(
    monkeypatch, capsys, backend_args, library, backend
):
    resources = (ASRL_RESOURCE, TCPIP_RESOURCE, USB_RESOURCE)
    manager = FakeManager(resources)
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", *backend_args])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert calls == [library]
    assert captured.out.splitlines() == [
        "VISA resources:",
        f"Backend: {backend}",
        f"- {ASRL_RESOURCE}",
        f"- {TCPIP_RESOURCE}",
        f"- {USB_RESOURCE}",
    ]
    assert captured.err == ""
    assert manager.list_calls == 1
    assert manager.opened_resources == []
    assert manager.close_calls == 1


def test_raw_listing_empty_human_output(monkeypatch, capsys):
    install_fake_manager(monkeypatch, FakeManager())

    exit_code = main(["list-resources"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert captured.out == "No VISA resources found.\nBackend: system\n"
    assert captured.err == ""


def test_raw_listing_json_is_one_object(monkeypatch, capsys):
    resources = (ASRL_RESOURCE, TCPIP_RESOURCE)
    manager = FakeManager(resources)
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--backend", "@py", "--json"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert calls == ["@py"]
    assert json.loads(captured.out) == {
        "success": True,
        "backend": "@py",
        "resources": [
            {
                "resource": ASRL_RESOURCE,
                "manufacturer": None,
                "model": None,
            },
            {
                "resource": TCPIP_RESOURCE,
                "manufacturer": None,
                "model": None,
            },
        ],
        "error": None,
    }
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert manager.opened_resources == []


def test_live_only_human_shows_parsed_identity_and_resource(monkeypatch, capsys):
    tcpip_session = FakeSession(response="Vendor,Anything")
    manager = FakeManager(
        (ASRL_RESOURCE, TCPIP_RESOURCE, USB_RESOURCE),
        sessions_by_resource={TCPIP_RESOURCE: tcpip_session},
    )
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live-only", "--backend", "@py"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert calls == ["@py"]
    assert captured.out == (
        "Live VISA resources:\n"
        "Backend: @py\n"
        "- Unknown instrument\n"
        f"  Resource: {TCPIP_RESOURCE}\n"
    )
    assert captured.err == ""
    assert manager.list_calls == 1
    assert manager.opened_resources == [TCPIP_RESOURCE]
    assert tcpip_session.queries == ["*IDN?"]
    assert tcpip_session.close_calls == 1
    assert manager.close_calls == 1


def test_live_only_human_shows_any_parsed_model_without_serial_firmware_or_raw_idn(
    monkeypatch, capsys
):
    response = "Keysight Technologies,34465A,SECRET-SERIAL,SECRET-FIRMWARE"
    session = FakeSession(response=response)
    manager = FakeManager(
        (USB_RESOURCE,),
        sessions_by_resource={USB_RESOURCE: session},
    )
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live-only"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert captured.out == (
        "Live VISA resources:\n"
        "Backend: system\n"
        "- Keysight Technologies 34465A\n"
        f"  Resource: {USB_RESOURCE}\n"
    )
    assert "SECRET-SERIAL" not in captured.out
    assert "SECRET-FIRMWARE" not in captured.out
    assert response not in captured.out
    assert captured.err == ""


def test_serial_cli_options_apply_to_system_asrl_only(monkeypatch, capsys):
    asrl_session = FakeSession(response="Agilent Technologies,33521B,SERIAL,FIRMWARE")
    tcpip_session = FakeSession(response="Vendor,Model,Serial,Firmware")
    manager = FakeManager(
        (ASRL_RESOURCE, TCPIP_RESOURCE),
        sessions_by_resource={
            ASRL_RESOURCE: asrl_session,
            TCPIP_RESOURCE: tcpip_session,
        },
    )
    install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "list-resources",
            "--live-only",
            "--serial-baud-rate",
            "9600",
            "--serial-read-termination",
            "CRLF",
            "--serial-write-termination",
            "NONE",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert manager.open_calls == [
        (ASRL_RESOURCE, {"open_timeout": 1000}),
        (TCPIP_RESOURCE, {}),
    ]
    assert asrl_session.baud_rate == 9600
    assert asrl_session.read_termination == "\r\n"
    assert asrl_session.write_termination is None
    assert tcpip_session.baud_rate == 4800
    assert tcpip_session.read_termination == "existing read"
    assert tcpip_session.write_termination == "existing write"
    assert captured.err == ""


def test_live_only_json_filters_empty_response(monkeypatch, capsys):
    tcpip_session = FakeSession(
        response="Agilent Technologies,33521B,SERIAL,FIRMWARE"
    )
    usb_session = FakeSession(response="  ")
    manager = FakeManager(
        (TCPIP_RESOURCE, USB_RESOURCE),
        sessions_by_resource={
            TCPIP_RESOURCE: tcpip_session,
            USB_RESOURCE: usb_session,
        },
    )
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live-only", "--json"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert json.loads(captured.out) == {
        "success": True,
        "backend": "system",
        "resources": [
            {
                "resource": TCPIP_RESOURCE,
                "manufacturer": "Agilent Technologies",
                "model": "33521B",
            }
        ],
        "error": None,
    }
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert "SERIAL" not in captured.out
    assert "FIRMWARE" not in captured.out
    assert "Agilent Technologies,33521B,SERIAL,FIRMWARE" not in captured.out


def test_live_only_empty_human_and_json_output(monkeypatch, capsys):
    install_fake_manager(monkeypatch, FakeManager())

    human_exit = main(["list-resources", "--live-only"])
    human = capsys.readouterr()
    json_exit = main(["list-resources", "--live-only", "--json"])
    json_result = capsys.readouterr()

    assert human_exit == ExitCode.SUCCESS
    assert human.out == "No live VISA resources found.\nBackend: system\n"
    assert human.err == ""
    assert json_exit == ExitCode.SUCCESS
    assert json.loads(json_result.out) == {
        "success": True,
        "backend": "system",
        "resources": [],
        "error": None,
    }
    assert json_result.err == ""


def test_listing_invalid_backend_json_does_not_create_manager(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["list-resources", "--backend", "invalid-backend", "--json"]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CLI_USAGE
    assert calls == []
    assert json.loads(captured.out) == {
        "success": False,
        "backend": None,
        "resources": [],
        "error": (
            "unsupported_backend: Unsupported VISA backend "
            "'invalid-backend'; choose 'system' or '@py'."
        ),
    }
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_listing_manager_creation_error_uses_exit_20(monkeypatch, capsys):
    def failing_factory(pyvisa_library):
        raise RuntimeError(f"private manager detail for {pyvisa_library}")

    monkeypatch.setattr(visa, "create_resource_manager", failing_factory)

    exit_code = main(["list-resources", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.RESOURCE_MANAGER_ERROR
    assert payload["error"].startswith("resource_manager_error:")
    assert "private manager detail" not in captured.out
    assert captured.err == ""


def test_listing_discovery_error_uses_exit_26(monkeypatch, capsys):
    manager = FakeManager(list_error=RuntimeError("private listing detail"))
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--json"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.RESOURCE_DISCOVERY_ERROR
    assert json.loads(captured.out) == {
        "success": False,
        "backend": "system",
        "resources": [],
        "error": "resource_discovery_error: Could not list VISA resources.",
    }
    assert "private listing detail" not in captured.out
    assert captured.err == ""
    assert manager.list_calls == 1
    assert manager.close_calls == 1


def test_live_only_session_cleanup_error_uses_exit_25(monkeypatch, capsys):
    session = FakeSession(close_error=RuntimeError("private close detail"))
    manager = FakeManager(
        (TCPIP_RESOURCE,),
        sessions_by_resource={TCPIP_RESOURCE: session},
    )
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live-only"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.VISA_CLEANUP_ERROR
    assert captured.out == ""
    assert "Error [visa_cleanup_error]" in captured.err
    assert "private close detail" not in captured.err
    assert "Traceback" not in captured.err
    assert manager.close_calls == 1


def test_listing_manager_cleanup_error_uses_exit_25(monkeypatch, capsys):
    manager = FakeManager(close_error=RuntimeError("private manager close detail"))
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.VISA_CLEANUP_ERROR
    assert payload["resources"] == []
    assert payload["error"].startswith("visa_cleanup_error:")
    assert "private manager close detail" not in captured.out
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_listing_does_not_invoke_identify(monkeypatch):
    install_fake_manager(monkeypatch, FakeManager())

    def forbidden_identify(resource, backend):
        raise AssertionError("listing must not identify instruments")

    monkeypatch.setattr(cli_module, "identify_instrument", forbidden_identify)

    assert main(["list-resources"]) == ExitCode.SUCCESS
