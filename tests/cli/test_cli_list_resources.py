import json

import pytest

import wavegen_tool_cli.cli as cli_module
from wavegen_tool_cli.cli import ExitCode, main
from wavegen_tool_core import visa


USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
TCPIP_RESOURCE = "TCPIP0::192.0.2.10::inst0::INSTR"
GPIB_RESOURCE = "GPIB0::10::INSTR"


class FakeManager:
    def __init__(self, resources=(), *, list_error=None, close_error=None):
        self.resources = resources
        self.list_error = list_error
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
        raise AssertionError("listing must not open a resource")

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


def test_list_resources_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(["list-resources", "--help"])

    output = capsys.readouterr().out
    assert error.value.code == ExitCode.SUCCESS
    assert "--live" in output
    assert "--backend" in output
    assert "--json" in output
    assert "{system,@py}" not in output


@pytest.mark.parametrize("extra_args", [[], ["--json"]])
def test_missing_live_is_usage_error_without_creating_manager(
    monkeypatch, capsys, extra_args
):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(["list-resources", *extra_args])

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.list_calls == 0
    assert "required" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("backend_args", "library", "backend"),
    [
        ([], "@ivi", "system"),
        (["--backend", "@py"], "@py", "@py"),
    ],
)
def test_list_resources_human_preserves_order(
    monkeypatch, capsys, backend_args, library, backend
):
    resources = (USB_RESOURCE, TCPIP_RESOURCE, GPIB_RESOURCE)
    manager = FakeManager(resources)
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live", *backend_args])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert calls == [library]
    assert captured.out.splitlines() == [
        "Live VISA resources:",
        f"Backend: {backend}",
        f"- {USB_RESOURCE}",
        f"- {TCPIP_RESOURCE}",
        f"- {GPIB_RESOURCE}",
    ]
    assert captured.err == ""
    assert manager.list_calls == 1
    assert manager.opened_resources == []
    assert manager.close_calls == 1


def test_list_resources_empty_human_output(monkeypatch, capsys):
    manager = FakeManager()
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert captured.out == "No live VISA resources found.\nBackend: system\n"
    assert captured.err == ""


@pytest.mark.parametrize(
    ("backend_args", "library", "backend"),
    [
        ([], "@ivi", "system"),
        (["--backend", "@py"], "@py", "@py"),
    ],
)
def test_list_resources_json_is_exactly_one_object(
    monkeypatch, capsys, backend_args, library, backend
):
    resources = (TCPIP_RESOURCE, GPIB_RESOURCE)
    manager = FakeManager(resources)
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live", *backend_args, "--json"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert calls == [library]
    assert json.loads(captured.out) == {
        "success": True,
        "backend": backend,
        "resources": [TCPIP_RESOURCE, GPIB_RESOURCE],
        "error": None,
    }
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_list_resources_empty_json_output(monkeypatch, capsys):
    install_fake_manager(monkeypatch, FakeManager())

    exit_code = main(["list-resources", "--live", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload == {
        "success": True,
        "backend": "system",
        "resources": [],
        "error": None,
    }


def test_list_resources_invalid_backend_human_does_not_create_manager(
    monkeypatch, capsys
):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["list-resources", "--live", "--backend", "invalid-backend"]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.list_calls == 0
    assert captured.out == ""
    assert "Error [unsupported_backend]" in captured.err
    assert "Traceback" not in captured.err
    assert "usage:" not in captured.err


def test_list_resources_invalid_backend_json_is_one_object(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "list-resources",
            "--live",
            "--backend",
            "invalid-backend",
            "--json",
        ]
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


def test_list_resources_manager_creation_error_uses_exit_20(monkeypatch, capsys):
    def failing_factory(pyvisa_library):
        raise RuntimeError(f"private manager detail for {pyvisa_library}")

    monkeypatch.setattr(visa, "create_resource_manager", failing_factory)

    exit_code = main(["list-resources", "--live", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.RESOURCE_MANAGER_ERROR
    assert payload["backend"] == "system"
    assert payload["resources"] == []
    assert payload["error"].startswith("resource_manager_error:")
    assert "private manager detail" not in captured.out
    assert captured.err == ""


def test_list_resources_discovery_error_uses_exit_26(monkeypatch, capsys):
    manager = FakeManager(list_error=RuntimeError("private listing detail"))
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live", "--json"])

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


def test_list_resources_cleanup_only_error_uses_exit_25(monkeypatch, capsys):
    manager = FakeManager(close_error=RuntimeError("private close detail"))
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["list-resources", "--live"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.VISA_CLEANUP_ERROR
    assert captured.out == ""
    assert "Error [visa_cleanup_error]" in captured.err
    assert "Traceback" not in captured.err
    assert manager.list_calls == 1
    assert manager.close_calls == 1


def test_list_resources_does_not_invoke_identify(monkeypatch):
    install_fake_manager(monkeypatch, FakeManager())

    def forbidden_identify(resource, backend):
        raise AssertionError("listing must not identify instruments")

    monkeypatch.setattr(cli_module, "identify_instrument", forbidden_identify)

    assert main(["list-resources", "--live"]) == ExitCode.SUCCESS
