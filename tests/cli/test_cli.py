import json
from types import SimpleNamespace

import pytest

import wavegen_tool_cli.cli as cli_module
from wavegen_tool_cli.cli import ExitCode, main
from wavegen_tool_core import visa


USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
TCPIP_RESOURCE = "TCPIP0::192.0.2.10::inst0::INSTR"
VALID_IDN = "Keysight Technologies,33521B,MY00000000,1.00-0.00-0.00"


class FakeSession:
    def __init__(self, response=VALID_IDN, *, query_error=None, close_error=None):
        self.response = response
        self.query_error = query_error
        self.close_error = close_error
        self.timeout = None
        self.queries = []
        self.writes = []
        self.closed = False

    def query(self, command):
        self.queries.append(command)
        if self.query_error is not None:
            raise self.query_error
        return self.response

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    def write(self, command):
        self.writes.append(command)


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
    assert "--serial-baud-rate" not in output
    assert "--serial-read-termination" not in output
    assert "--serial-write-termination" not in output
    assert "{system,@py}" not in output


def test_missing_resource_is_usage_error_without_traceback(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(["identify"])

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.opened_resources == []
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


def test_agilent_identify_human_output_preserves_reported_manufacturer(
    monkeypatch, capsys
):
    response = "Agilent Technologies,33521B,MY00000000,1.00-0.00-0.00"
    manager = FakeManager(FakeSession(response))
    install_fake_manager(monkeypatch, manager)

    exit_code = main(["identify", "--resource", USB_RESOURCE])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert "Manufacturer: Agilent Technologies" in captured.out
    assert "Canonical model ID: keysight-33521b" in captured.out
    assert "Model recognized: yes" in captured.out
    assert captured.err == ""


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
    assert ExitCode.RESOURCE_DISCOVERY_ERROR == 26
    assert ExitCode.VISA_WRITE_ERROR == 27
    assert ExitCode.STATUS_QUERY_ERROR == 28


def test_configure_sine_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_sine(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Keysight Technologies",
                model="33521B",
            ),
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            load="50",
            output_state="off",
        )

    monkeypatch.setattr(cli_module, "configure_sine", fake_configure_sine)

    exit_code = main(
        [
            "configure-sine",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [(USB_RESOURCE, "1000", "0.1", "0", "50", "system")]
    assert payload == {
        "success": True,
        "action": "configure-sine",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Keysight Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_sine_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_sine(*args):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion SIN",
                "SOURce1:FREQuency 1000",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_sine(*args):
        raise AssertionError(f"live configure_sine must not be called: {args}")

    monkeypatch.setattr(cli_module, "dry_run_sine", fake_dry_run_sine)
    monkeypatch.setattr(
        cli_module,
        "configure_sine",
        fail_live_configure_sine,
    )

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert dry_run_calls == [
        ("keysight-33521b", "1000", "0.1", "0", "50")
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-sine",
        "mode": "dry-run",
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion SIN",
            "SOURce1:FREQuency 1000",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_sine_live_missing_resource_is_usage_error(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "configure-sine",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "0.1",
            ]
        )

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert "usage:" in captured.err
    assert "--resource" in captured.err
    assert "Traceback" not in captured.err


def test_configure_square_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_square(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Keysight Technologies",
                model="33521B",
            ),
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            duty_cycle_percent=50.0,
            load="50",
            output_state="off",
        )

    monkeypatch.setattr(cli_module, "configure_square", fake_configure_square)

    exit_code = main(
        [
            "configure-square",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [
        (USB_RESOURCE, "1000", "0.1", "0", "50", "50", "system")
    ]
    assert payload == {
        "success": True,
        "action": "configure-square",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Keysight Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "duty_cycle_percent": 50.0,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_square_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_square(*args):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            duty_cycle_percent=50.0,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion SQUare",
                "SOURce1:FREQuency 1000",
                "SOURce1:FUNCtion:SQUare:DCYCle 50",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_square(*args):
        raise AssertionError(f"live configure_square must not be called: {args}")

    monkeypatch.setattr(cli_module, "dry_run_square", fake_dry_run_square)
    monkeypatch.setattr(
        cli_module,
        "configure_square",
        fail_live_configure_square,
    )

    exit_code = main(
        [
            "configure-square",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert dry_run_calls == [
        ("keysight-33521b", "1000", "0.1", "0", "50", "50")
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-square",
        "mode": "dry-run",
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "duty_cycle_percent": 50.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion SQUare",
            "SOURce1:FREQuency 1000",
            "SOURce1:FUNCtion:SQUare:DCYCle 50",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_square_live_missing_resource_is_usage_error(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "configure-square",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "0.1",
            ]
        )

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert "usage:" in captured.err
    assert "--resource" in captured.err
    assert "Traceback" not in captured.err


def test_configure_ramp_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_ramp(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Agilent Technologies",
                model="33521B",
            ),
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            symmetry_percent=100.0,
            load="50",
            output_state="off",
        )

    monkeypatch.setattr(cli_module, "configure_ramp", fake_configure_ramp)

    exit_code = main(
        [
            "configure-ramp",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [
        (USB_RESOURCE, "1000", "0.1", "0", "100", "50", "system")
    ]
    assert payload == {
        "success": True,
        "action": "configure-ramp",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "symmetry_percent": 100.0,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_ramp_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_ramp(*args):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            symmetry_percent=25.0,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion RAMP",
                "SOURce1:FREQuency 1000",
                "SOURce1:FUNCtion:RAMP:SYMMetry 25",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_ramp(*args):
        raise AssertionError(f"live configure_ramp must not be called: {args}")

    monkeypatch.setattr(cli_module, "dry_run_ramp", fake_dry_run_ramp)
    monkeypatch.setattr(
        cli_module,
        "configure_ramp",
        fail_live_configure_ramp,
    )

    exit_code = main(
        [
            "configure-ramp",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--symmetry-percent",
            "25",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert dry_run_calls == [
        ("keysight-33521b", "1000", "0.1", "0", "25", "50")
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-ramp",
        "mode": "dry-run",
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "symmetry_percent": 25.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion RAMP",
            "SOURce1:FREQuency 1000",
            "SOURce1:FUNCtion:RAMP:SYMMetry 25",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_ramp_live_missing_resource_is_usage_error(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "configure-ramp",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "0.1",
            ]
        )

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert "usage:" in captured.err
    assert "--resource" in captured.err
    assert "Traceback" not in captured.err


def test_configure_pulse_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_pulse(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Agilent Technologies",
                model="33521B",
            ),
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            pulse_width_s=0.0001,
            offset_v=0.0,
            edge_time_s=1e-8,
            load="50",
            output_state="off",
        )

    monkeypatch.setattr(cli_module, "configure_pulse", fake_configure_pulse)

    exit_code = main(
        [
            "configure-pulse",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--pulse-width-s",
            "0.0001",
            "--edge-time-s",
            "0.00000001",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [
        (
            USB_RESOURCE,
            "1000",
            "0.1",
            "0.0001",
            "0",
            "0.00000001",
            "50",
            "system",
        )
    ]
    assert payload == {
        "success": True,
        "action": "configure-pulse",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "pulse_width_s": 0.0001,
        "edge_time_s": 1e-8,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_dc_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_dc(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Agilent Technologies",
                model="33521B",
            ),
            voltage_v=1.5,
            load="50",
            output_state="off",
        )

    monkeypatch.setattr(cli_module, "configure_dc", fake_configure_dc)

    exit_code = main(
        [
            "configure-dc",
            "--resource",
            USB_RESOURCE,
            "--voltage-v",
            "1.5",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [(USB_RESOURCE, "1.5", "50", "system")]
    assert payload == {
        "success": True,
        "action": "configure-dc",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "voltage_v": 1.5,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_noise_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_noise(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Agilent Technologies",
                model="33521B",
            ),
            amplitude_vpp=0.1,
            offset_v=0.0,
            bandwidth_hz=100_000.0,
            load="50",
            output_state="off",
        )

    monkeypatch.setattr(cli_module, "configure_noise", fake_configure_noise)

    exit_code = main(
        [
            "configure-noise",
            "--resource",
            USB_RESOURCE,
            "--amplitude-vpp",
            "0.1",
            "--bandwidth-hz",
            "100000",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [
        (USB_RESOURCE, "0.1", "100000", "0", "50", "system")
    ]
    assert payload == {
        "success": True,
        "action": "configure-noise",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "bandwidth_hz": 100_000.0,
        "load": "50",
        "output_state": "off",
        "error": None,
    }
    assert "frequency_hz" not in payload


def test_configure_prbs_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_prbs(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Agilent Technologies",
                model="33521B",
            ),
            bit_rate_bps=1_000_000.0,
            amplitude_vpp=0.1,
            pattern="PN15",
            offset_v=0.0,
            edge_time_s=1e-8,
            load="50",
            output_state="off",
        )

    monkeypatch.setattr(cli_module, "configure_prbs", fake_configure_prbs)

    exit_code = main(
        [
            "configure-prbs",
            "--resource",
            USB_RESOURCE,
            "--bit-rate-bps",
            "1000000",
            "--amplitude-vpp",
            "0.1",
            "--pattern",
            "pn15",
            "--edge-time-s",
            "0.00000001",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [
        (
            USB_RESOURCE,
            "1000000",
            "0.1",
            "pn15",
            "0",
            "0.00000001",
            "50",
            "system",
        )
    ]
    assert payload == {
        "success": True,
        "action": "configure-prbs",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "bit_rate_bps": 1_000_000.0,
        "amplitude_vpp": 0.1,
        "pattern": "PN15",
        "offset_v": 0.0,
        "edge_time_s": 1e-8,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_pulse_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_pulse(*args):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            pulse_width_s=0.0001,
            edge_time_s=1e-8,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion PULSe",
                "SOURce1:FREQuency 1000",
                "SOURce1:FUNCtion:PULSe:WIDTh 0.0001",
                "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 1e-08",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_pulse(*args):
        raise AssertionError(f"live configure_pulse must not be called: {args}")

    monkeypatch.setattr(cli_module, "dry_run_pulse", fake_dry_run_pulse)
    monkeypatch.setattr(
        cli_module,
        "configure_pulse",
        fail_live_configure_pulse,
    )

    exit_code = main(
        [
            "configure-pulse",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--pulse-width-s",
            "0.0001",
            "--edge-time-s",
            "0.00000001",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert dry_run_calls == [
        (
            "keysight-33521b",
            "1000",
            "0.1",
            "0.0001",
            "0",
            "0.00000001",
            "50",
        )
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-pulse",
        "mode": "dry-run",
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "pulse_width_s": 0.0001,
        "edge_time_s": 1e-8,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion PULSe",
            "SOURce1:FREQuency 1000",
            "SOURce1:FUNCtion:PULSe:WIDTh 0.0001",
            "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 1e-08",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_dc_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_dc(*args):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            voltage_v=1.5,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:FUNCtion DC",
                "SOURce1:VOLTage:OFFSet 1.5",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_dc(*args):
        raise AssertionError(f"live configure_dc must not be called: {args}")

    monkeypatch.setattr(cli_module, "dry_run_dc", fake_dry_run_dc)
    monkeypatch.setattr(cli_module, "configure_dc", fail_live_configure_dc)

    exit_code = main(
        [
            "configure-dc",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--voltage-v",
            "1.5",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert dry_run_calls == [("keysight-33521b", "1.5", "50")]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-dc",
        "mode": "dry-run",
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "voltage_v": 1.5,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "OUTPut1:LOAD 50",
            "SOURce1:FUNCtion DC",
            "SOURce1:VOLTage:OFFSet 1.5",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_noise_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_noise(*args):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            amplitude_vpp=0.1,
            offset_v=0.0,
            bandwidth_hz=1_000_000.0,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion NOISe",
                "SOURce1:FUNCtion:NOISe:BANDwidth 1000000",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_noise(*args):
        raise AssertionError(f"live configure_noise must not be called: {args}")

    monkeypatch.setattr(cli_module, "dry_run_noise", fake_dry_run_noise)
    monkeypatch.setattr(
        cli_module,
        "configure_noise",
        fail_live_configure_noise,
    )

    exit_code = main(
        [
            "configure-noise",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--amplitude-vpp",
            "0.1",
            "--bandwidth-hz",
            "1000000",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert dry_run_calls == [
        ("keysight-33521b", "0.1", "1000000", "0", "50")
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-noise",
        "mode": "dry-run",
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "bandwidth_hz": 1_000_000.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion NOISe",
            "SOURce1:FUNCtion:NOISe:BANDwidth 1000000",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_prbs_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_prbs(*args):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            bit_rate_bps=1_000_000.0,
            amplitude_vpp=0.1,
            pattern="PN9",
            offset_v=0.0,
            edge_time_s=8.4e-9,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion PRBS",
                "SOURce1:FUNCtion:PRBS:BRATe 1000000",
                "SOURce1:FUNCtion:PRBS:DATA PN9",
                "SOURce1:FUNCtion:PRBS:TRANsition:BOTH 8.4e-09",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_prbs(*args):
        raise AssertionError(f"live configure_prbs must not be called: {args}")

    monkeypatch.setattr(cli_module, "dry_run_prbs", fake_dry_run_prbs)
    monkeypatch.setattr(
        cli_module,
        "configure_prbs",
        fail_live_configure_prbs,
    )

    exit_code = main(
        [
            "configure-prbs",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--bit-rate-bps",
            "1000000",
            "--amplitude-vpp",
            "0.1",
            "--pattern",
            "pn9",
            "--edge-time-s",
            "0.0000000084",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert dry_run_calls == [
        (
            "keysight-33521b",
            "1000000",
            "0.1",
            "pn9",
            "0",
            "0.0000000084",
            "50",
        )
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-prbs",
        "mode": "dry-run",
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "bit_rate_bps": 1_000_000.0,
        "amplitude_vpp": 0.1,
        "pattern": "PN9",
        "offset_v": 0.0,
        "edge_time_s": 8.4e-9,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion PRBS",
            "SOURce1:FUNCtion:PRBS:BRATe 1000000",
            "SOURce1:FUNCtion:PRBS:DATA PN9",
            "SOURce1:FUNCtion:PRBS:TRANsition:BOTH 8.4e-09",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


@pytest.mark.parametrize(
    "argv",
    [
        [
            "configure-pulse",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--pulse-width-s",
            "0.0001",
        ],
        ["configure-dc", "--voltage-v", "1.5"],
        [
            "configure-noise",
            "--amplitude-vpp",
            "0.1",
            "--bandwidth-hz",
            "1000000",
        ],
        [
            "configure-prbs",
            "--bit-rate-bps",
            "1000000",
            "--amplitude-vpp",
            "0.1",
        ],
    ],
)
def test_remaining_live_waveforms_require_resource(
    monkeypatch, capsys, argv
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(argv)

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert "usage:" in captured.err
    assert "--resource" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("state", ["on", "off"])
def test_output_cli_parses_state_and_calls_core(monkeypatch, capsys, state):
    calls = []

    def fake_set_output(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Agilent Technologies",
                model="33521B",
            ),
            output_state=state,
        )

    monkeypatch.setattr(cli_module, "set_output", fake_set_output)

    exit_code = main(
        ["output", "--resource", USB_RESOURCE, "--state", state, "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [(USB_RESOURCE, state, "system")]
    assert payload["action"] == "output"
    assert payload["manufacturer"] == "Agilent Technologies"
    assert payload["output_state"] == state
    assert payload["error"] is None


def test_output_on_cleanup_failure_json_preserves_possible_output_state(
    monkeypatch, capsys
):
    session = FakeSession(close_error=RuntimeError("private close detail"))
    manager = FakeManager(session)
    install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "output",
            "--resource",
            USB_RESOURCE,
            "--state",
            "on",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.VISA_CLEANUP_ERROR
    assert payload["success"] is False
    assert payload["output_state"] == "on"
    assert "Channel 1 output may remain on" in payload["error"]
    assert session.writes == ["OUTPut1 ON"]


def test_status_cli_parses_arguments_calls_core_and_emits_json(monkeypatch, capsys):
    calls = []

    def fake_query_status(*args):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Agilent Technologies",
                model="33521B",
            ),
            output_state="off",
            function="SIN",
            frequency_hz=1000.0,
            amplitude=0.1,
            amplitude_unit="VPP",
            offset_v=0.0,
            load="50",
        )

    monkeypatch.setattr(cli_module, "query_status", fake_query_status)

    exit_code = main(
        ["status", "--resource", USB_RESOURCE, "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [(USB_RESOURCE, "system")]
    assert payload == {
        "success": True,
        "action": "status",
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "output_state": "off",
        "function": "SIN",
        "frequency_hz": 1000.0,
        "amplitude": 0.1,
        "amplitude_unit": "VPP",
        "offset_v": 0.0,
        "load": "50",
        "error": None,
    }
