import json
from types import SimpleNamespace

import pytest

import wavegen_tool_cli.cli as cli_module
from wavegen_tool_cli.cli import ExitCode, main
from wavegen_tool_core import ErrorQueueQueryError, visa


USB_RESOURCE = "USB0::0x0000::0x0000::MY00000000::INSTR"
TCPIP_RESOURCE = "TCPIP0::192.0.2.10::inst0::INSTR"
VALID_IDN = "Keysight Technologies,33521B,MY00000000,1.00-0.00-0.00"


def test_configure_sine_counted_burst_dry_run_json(capsys) -> None:
    exit_code = main(
        [
            "configure-sine",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--burst-count",
            "2",
            "--burst-period-s",
            "0.01",
            "--dry-run",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["burst_enabled"] is True
    assert payload["burst_count"] == 2
    assert payload["burst_period_s"] == 0.01
    assert payload["commands"][0] == "OUTPut1 OFF"
    assert payload["commands"][-1] == "SOURce1:BURSt:STATe ON"
    assert "OUTPut1 ON" not in payload["commands"]


class FakeSession:
    def __init__(self, response=VALID_IDN, *, query_error=None, close_error=None):
        self.response = response
        self.query_error = query_error
        self.close_error = close_error
        self.timeout = None
        self.queries = []
        self.writes = []
        self.control_ren_calls = []
        self.closed = False

    def query(self, command):
        self.queries.append(command)
        if self.query_error is not None:
            raise self.query_error
        cmd_upper = command.strip().upper()
        if "COUPLE" in cmd_upper:
            return "0"
        if "TRACK?" in cmd_upper:
            return "OFF"
        return self.response

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    def write(self, command):
        self.writes.append(command)

    def control_ren(self, mode):
        self.control_ren_calls.append(mode)


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


def test_configure_sine_help_hides_validation_live_switch(capsys):
    with pytest.raises(SystemExit) as error:
        main(["configure-sine", "--help"])

    assert error.value.code == ExitCode.SUCCESS
    assert (
        "--validation-allow-pending-live-support"
        not in capsys.readouterr().out
    )


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
    assert "--validation-allow-pending-live-support" not in output
    assert "--model" not in output


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


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "--resource",
            USB_RESOURCE,
            "--validation-allow-pending-live-support",
        ],
        [
            "--resource",
            USB_RESOURCE,
            "--model",
            "keysight-33512b",
        ],
        [
            "--simulate",
            "--validation-allow-pending-live-support",
            "--model",
            "keysight-33512b",
        ],
    ],
)
def test_identify_validation_only_argument_guards_fail_before_visa_io(
    monkeypatch,
    capsys,
    arguments,
):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(["identify", *arguments])

    assert error.value.code == ExitCode.CLI_USAGE
    assert calls == []
    assert manager.opened_resources == []
    assert "usage:" in capsys.readouterr().err


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


def test_identify_validation_live_accepts_matching_33512b(monkeypatch, capsys):
    manager = FakeManager(
        FakeSession("Keysight Technologies,33512B,MY00000000,1.00")
    )
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "identify",
            "--resource",
            USB_RESOURCE,
            "--validation-allow-pending-live-support",
            "--model",
            "keysight-33512b",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == ["@ivi"]
    assert manager.session.queries == ["*IDN?"]
    assert manager.session.writes == []
    assert payload["model"] == "33512B"
    assert payload["canonical_model_id"] == "keysight-33512b"
    assert payload["model_supported"] is True


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
    assert ExitCode.WAVEFORM_VERIFICATION_ERROR == 29


def test_configure_sine_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_sine(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Keysight Technologies",
                model="33521B",
            ),
            frequency_hz=1000.0,
            amplitude_vpp=3.3,
            offset_v=1.65,
            load="50",
            phase_deg=45.0,
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
            "--high-level-v",
            "3.3",
            "--low-level-v",
            "0",
            "--phase-deg",
            "45",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [
        (USB_RESOURCE, "1000", 3.3, 1.65, "50", "system", 45.0)
    ]
    assert payload == {
        "success": True,
        "action": "configure-sine",
        "channel": 1,
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Keysight Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 3.3,
        "offset_v": 1.65,
        "phase_deg": 45.0,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_sine_dry_run_cli_forwards_registered_model(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_sine(*args, **kwargs):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33510B",
            canonical_model_id="keysight-33510b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            load="50",
            phase_deg=0.0,
            commands=(
                "OUTPut1 OFF",
                "SOURce1:AM:STATe OFF",
                "SOURce1:FM:STATe OFF",
                "SOURce1:PM:STATe OFF",
                "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        "SOURce1:FREQuency:MODE CW",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion SIN",
                "SOURce1:FREQuency 1000",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
                "UNIT:ANGLe DEGree",
                "SOURce1:PHASe 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_sine(*args, **kwargs):
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
            "keysight-33510b",
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
        ("keysight-33510b", "1000", 0.1, 0.0, "50", 0.0)
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-sine",
        "mode": "dry-run",
        "channel": 1,
        "model": "33510B",
        "canonical_model_id": "keysight-33510b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        "SOURce1:FREQuency:MODE CW",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion SIN",
            "SOURce1:FREQuency 1000",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
            "UNIT:ANGLe DEGree",
            "SOURce1:PHASe 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_sine_internal_am_dry_run_emits_ordered_json_without_visa(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--model",
            "keysight-33512b",
            "--channel",
            "2",
            "--frequency-hz",
            "1000000",
            "--amplitude-vpp",
            "0.1",
            "--am-frequency",
            "1000",
            "--am-depth",
            "50",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["am_enabled"] is True
    assert payload["am_frequency_hz"] == 1000.0
    assert payload["am_depth_percent"] == 50.0
    assert payload["am_type"] == "normal"
    assert payload["output_state"] == "off"
    assert payload["commands"][:9] == [
        "OUTPut2 OFF",
        "SOURce2:AM:STATe OFF",
        "SOURce2:FM:STATe OFF",
        "SOURce2:PM:STATe OFF",
        "SOURce2:FSKey:STATe OFF",
        "SOURce2:BPSK:STATe OFF",
        "SOURce2:PWM:STATe OFF",
        "SOURce2:BURSt:STATe OFF",
        "SOURce2:FREQuency:MODE CW",
    ]
    assert payload["commands"][-6:] == [
        "SOURce2:AM:SOURce INTernal",
        "SOURce2:AM:DSSC OFF",
        "SOURce2:AM:INTernal:FUNCtion SINusoid",
        "SOURce2:AM:INTernal:FREQuency 1000",
        "SOURce2:AM:DEPTh 50",
        "SOURce2:AM:STATe ON",
    ]
    assert "OUTPut2 ON" not in payload["commands"]


@pytest.mark.parametrize(
    "am_arguments",
    [
        ["--am-frequency", "100"],
        ["--am-depth", "50"],
        ["--am-type", "dssc"],
    ],
)
def test_incomplete_am_cli_group_fails_closed_without_visa(
    monkeypatch,
    capsys,
    am_arguments,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            *am_arguments,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["success"] is False
    assert payload["error"].startswith("waveform_parameter_error:")


def test_configure_sine_internal_fm_dry_run_emits_ordered_json_without_visa(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--model",
            "keysight-33512b",
            "--channel",
            "2",
            "--frequency-hz",
            "1000000",
            "--amplitude-vpp",
            "0.1",
            "--fm-frequency",
            "1000",
            "--fm-deviation",
            "100000",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["fm_enabled"] is True
    assert payload["fm_frequency_hz"] == 1000.0
    assert payload["fm_deviation_hz"] == 100000.0
    assert payload["output_state"] == "off"
    assert payload["commands"][:9] == [
        "OUTPut2 OFF",
        "SOURce2:AM:STATe OFF",
        "SOURce2:FM:STATe OFF",
        "SOURce2:PM:STATe OFF",
        "SOURce2:FSKey:STATe OFF",
        "SOURce2:BPSK:STATe OFF",
        "SOURce2:PWM:STATe OFF",
        "SOURce2:BURSt:STATe OFF",
        "SOURce2:FREQuency:MODE CW",
    ]
    assert payload["commands"][-5:] == [
        "SOURce2:FM:SOURce INTernal",
        "SOURce2:FM:INTernal:FUNCtion SINusoid",
        "SOURce2:FM:INTernal:FREQuency 1000",
        "SOURce2:FM:DEViation 100000",
        "SOURce2:FM:STATe ON",
    ]
    assert "OUTPut2 ON" not in payload["commands"]


def test_incomplete_fm_cli_group_fails_closed_without_visa(monkeypatch, capsys):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--fm-frequency",
            "100",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["success"] is False
    assert payload["error"].startswith("waveform_parameter_error:")


def test_configure_sine_internal_pm_dry_run_emits_ordered_json_without_visa(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--model",
            "keysight-33512b",
            "--channel",
            "2",
            "--frequency-hz",
            "100000",
            "--amplitude-vpp",
            "0.1",
            "--pm-frequency",
            "1000",
            "--pm-deviation-deg",
            "90",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["pm_enabled"] is True
    assert payload["pm_frequency_hz"] == 1000.0
    assert payload["pm_deviation_deg"] == 90.0
    assert payload["output_state"] == "off"
    assert payload["commands"][:9] == [
        "OUTPut2 OFF",
        "SOURce2:AM:STATe OFF",
        "SOURce2:FM:STATe OFF",
        "SOURce2:PM:STATe OFF",
        "SOURce2:FSKey:STATe OFF",
        "SOURce2:BPSK:STATe OFF",
        "SOURce2:PWM:STATe OFF",
        "SOURce2:BURSt:STATe OFF",
        "SOURce2:FREQuency:MODE CW",
    ]
    assert payload["commands"][-5:] == [
        "SOURce2:PM:SOURce INTernal",
        "SOURce2:PM:INTernal:FUNCtion SINusoid",
        "SOURce2:PM:INTernal:FREQuency 1000",
        "SOURce2:PM:DEViation 90",
        "SOURce2:PM:STATe ON",
    ]
    assert "OUTPut2 ON" not in payload["commands"]


def test_incomplete_pm_cli_group_fails_closed_without_visa(monkeypatch, capsys):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--frequency-hz",
            "100000",
            "--amplitude-vpp",
            "0.1",
            "--pm-frequency",
            "1000",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["success"] is False
    assert payload["error"].startswith("waveform_parameter_error:")


def test_configure_sine_internal_fsk_dry_run_emits_channelized_output_without_visa(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    arguments = [
        "configure-sine",
        "--dry-run",
        "--model",
        "keysight-33512b",
        "--channel",
        "2",
        "--frequency-hz",
        "1000000",
        "--amplitude-vpp",
        "0.1",
        "--fsk-hop-frequency",
        "500000",
        "--fsk-rate",
        "80000",
    ]

    exit_code = main([*arguments, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["fsk_enabled"] is True
    assert payload["fsk_hop_frequency_hz"] == 500000.0
    assert payload["fsk_rate_hz"] == 80000.0
    assert payload["output_state"] == "off"
    assert payload["commands"][:9] == [
        "OUTPut2 OFF",
        "SOURce2:AM:STATe OFF",
        "SOURce2:FM:STATe OFF",
        "SOURce2:PM:STATe OFF",
        "SOURce2:FSKey:STATe OFF",
        "SOURce2:BPSK:STATe OFF",
        "SOURce2:PWM:STATe OFF",
        "SOURce2:BURSt:STATe OFF",
        "SOURce2:FREQuency:MODE CW",
    ]
    assert payload["commands"][-4:] == [
        "SOURce2:FSKey:SOURce INTernal",
        "SOURce2:FSKey:FREQuency 500000",
        "SOURce2:FSKey:INTernal:RATE 80000",
        "SOURce2:FSKey:STATe ON",
    ]
    assert "OUTPut2 ON" not in payload["commands"]

    assert main(arguments) == ExitCode.SUCCESS
    human_output = capsys.readouterr().out
    assert "FSK hop frequency (Hz): 500000.0" in human_output
    assert "FSK rate (Hz): 80000.0" in human_output
    assert manager_calls == []


@pytest.mark.parametrize(
    "fsk_arguments",
    [
        ["--fsk-hop-frequency", "500000"],
        ["--fsk-rate", "80000"],
    ],
)
def test_incomplete_fsk_cli_group_fails_closed_without_visa(
    monkeypatch,
    capsys,
    fsk_arguments,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "1000000",
            "--amplitude-vpp",
            "0.1",
            *fsk_arguments,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["success"] is False
    assert payload["error"].startswith("waveform_parameter_error:")


def test_configure_sine_internal_bpsk_dry_run_emits_channelized_output_without_visa(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    arguments = [
        "configure-sine",
        "--dry-run",
        "--model",
        "keysight-33512b",
        "--channel",
        "2",
        "--frequency-hz",
        "1000000",
        "--amplitude-vpp",
        "0.1",
        "--bpsk-phase-shift-deg",
        "180",
        "--bpsk-rate",
        "1000",
    ]

    exit_code = main([*arguments, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["bpsk_enabled"] is True
    assert payload["bpsk_phase_shift_deg"] == 180.0
    assert payload["bpsk_rate_hz"] == 1000.0
    assert payload["output_state"] == "off"
    assert payload["commands"][:9] == [
        "OUTPut2 OFF",
        "SOURce2:AM:STATe OFF",
        "SOURce2:FM:STATe OFF",
        "SOURce2:PM:STATe OFF",
        "SOURce2:FSKey:STATe OFF",
        "SOURce2:BPSK:STATe OFF",
        "SOURce2:PWM:STATe OFF",
        "SOURce2:BURSt:STATe OFF",
        "SOURce2:FREQuency:MODE CW",
    ]
    assert payload["commands"][-4:] == [
        "SOURce2:BPSK:SOURce INTernal",
        "SOURce2:BPSK:PHASe 180",
        "SOURce2:BPSK:INTernal:RATE 1000",
        "SOURce2:BPSK:STATe ON",
    ]
    assert "OUTPut2 ON" not in payload["commands"]

    assert main(arguments) == ExitCode.SUCCESS
    human_output = capsys.readouterr().out
    assert "BPSK phase shift (degrees): 180.0" in human_output
    assert "BPSK rate (Hz): 1000.0" in human_output
    assert manager_calls == []


@pytest.mark.parametrize(
    "bpsk_arguments",
    [
        ["--bpsk-phase-shift-deg", "180"],
        ["--bpsk-rate", "1000"],
    ],
)
def test_incomplete_bpsk_cli_group_fails_closed_without_visa(
    monkeypatch,
    capsys,
    bpsk_arguments,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "1000000",
            "--amplitude-vpp",
            "0.1",
            *bpsk_arguments,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["success"] is False
    assert payload["error"].startswith("waveform_parameter_error:")


def test_internal_fm_dry_run_human_output_reports_frequency_and_deviation(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--frequency-hz",
            "1000000",
            "--amplitude-vpp",
            "0.1",
            "--fm-frequency",
            "1000",
            "--fm-deviation",
            "100000",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert "FM modulation frequency (Hz): 1000.0" in output
    assert "FM deviation (Hz): 100000.0" in output


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

    def fake_configure_square(*args, **kwargs):
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
            phase_deg=0.0,
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
        (USB_RESOURCE, "1000", 0.1, 0.0, "50", "50", "system", 0.0)
    ]
    assert payload == {
        "success": True,
        "action": "configure-square",
        "channel": 1,
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Keysight Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "phase_deg": 0.0,
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

    def fake_dry_run_square(*args, **kwargs):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            duty_cycle_percent=50.0,
            load="50",
            phase_deg=0.0,
            commands=(
                "OUTPut1 OFF",
                "SOURce1:AM:STATe OFF",
                "SOURce1:FM:STATe OFF",
                "SOURce1:PM:STATe OFF",
                "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_square(*args, **kwargs):
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
        ("keysight-33521b", "1000", 0.1, 0.0, "50", "50", 0.0)
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-square",
        "mode": "dry-run",
        "channel": 1,
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "duty_cycle_percent": 50.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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

    def fake_configure_ramp(*args, **kwargs):
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
            phase_deg=0.0,
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
        (USB_RESOURCE, "1000", 0.1, 0.0, "100", "50", "system", 0.0)
    ]
    assert payload == {
        "success": True,
        "action": "configure-ramp",
        "channel": 1,
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "phase_deg": 0.0,
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

    def fake_dry_run_ramp(*args, **kwargs):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            symmetry_percent=25.0,
            load="50",
            phase_deg=0.0,
            commands=(
                "OUTPut1 OFF",
                "SOURce1:AM:STATe OFF",
                "SOURce1:FM:STATe OFF",
                "SOURce1:PM:STATe OFF",
                "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_ramp(*args, **kwargs):
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
        ("keysight-33521b", "1000", 0.1, 0.0, "25", "50", 0.0)
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-ramp",
        "mode": "dry-run",
        "channel": 1,
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "symmetry_percent": 25.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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


def test_configure_triangle_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-triangle",
            "--dry-run",
            "--model",
            "keysight-33521b",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--offset-v",
            "0.2",
            "--load",
            "high-z",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-triangle",
        "mode": "dry-run",
        "channel": 1,
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.2,
        "phase_deg": 0.0,
        "load": "high-z",
        "commands": [
            "OUTPut1 OFF",
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
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
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_pulse_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_pulse(*args, **kwargs):
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
            edge_time_s=None,
            leading_edge_s=1e-8,
            trailing_edge_s=2e-8,
            load="50",
            phase_deg=0.0,
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
            "--leading-edge-s",
            "0.00000001",
            "--trailing-edge-s",
            "0.00000002",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [
        (
            USB_RESOURCE,
            "1000",
            0.1,
            "0.0001",
            0.0,
            None,
            "50",
            "system",
            0.0,
            "0.00000001",
            "0.00000002",
        )
    ]
    assert payload == {
        "success": True,
        "action": "configure-pulse",
        "channel": 1,
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "pulse_width_s": 0.0001,
        "edge_time_s": None,
        "leading_edge_s": 1e-8,
        "trailing_edge_s": 2e-8,
        "load": "50",
        "output_state": "off",
        "error": None,
    }


def test_configure_dc_cli_parses_arguments_calls_core_and_emits_json(
    monkeypatch, capsys
):
    calls = []

    def fake_configure_dc(*args, **kwargs):
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
        "channel": 1,
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

    def fake_configure_noise(*args, **kwargs):
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
        (USB_RESOURCE, 0.1, "100000", 0.0, "50", "system")
    ]
    assert payload == {
        "success": True,
        "action": "configure-noise",
        "channel": 1,
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

    def fake_configure_prbs(*args, **kwargs):
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
            pattern="PN9",
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
            "PN9",
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
            0.1,
            "PN9",
            0.0,
            "0.00000001",
            "50",
            "system",
        )
    ]
    assert payload == {
        "success": True,
        "action": "configure-prbs",
        "channel": 1,
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "bit_rate_bps": 1_000_000.0,
        "amplitude_vpp": 0.1,
        "pattern": "PN9",
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

    def fake_dry_run_pulse(*args, **kwargs):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            frequency_hz=1000.0,
            amplitude_vpp=0.1,
            offset_v=0.0,
            pulse_width_s=0.0001,
            edge_time_s=1e-8,
            leading_edge_s=1e-8,
            trailing_edge_s=1e-8,
            load="50",
            phase_deg=0.0,
            commands=(
                "OUTPut1 OFF",
                "SOURce1:AM:STATe OFF",
                "SOURce1:FM:STATe OFF",
                "SOURce1:PM:STATe OFF",
                "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        "SOURce1:FREQuency:MODE CW",
                "OUTPut1:LOAD 50",
                "SOURce1:VOLTage:UNIT VPP",
                "SOURce1:FUNCtion PULSe",
                "SOURce1:FREQuency 1000",
                "SOURce1:FUNCtion:PULSe:WIDTh 0.0001",
                "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 1e-08",
                "SOURce1:VOLTage 0.1",
                "SOURce1:VOLTage:OFFSet 0",
                "UNIT:ANGLe DEGree",
                "SOURce1:PHASe 0",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_pulse(*args, **kwargs):
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
            0.1,
            "0.0001",
            0.0,
            "0.00000001",
            "50",
            0.0,
            None,
            None,
        )
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-pulse",
        "mode": "dry-run",
        "channel": 1,
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "frequency_hz": 1000.0,
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "pulse_width_s": 0.0001,
        "edge_time_s": 1e-8,
        "leading_edge_s": 1e-8,
        "trailing_edge_s": 1e-8,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "SOURce1:BURSt:STATe OFF",
        "SOURce1:FREQuency:MODE CW",
            "OUTPut1:LOAD 50",
            "SOURce1:VOLTage:UNIT VPP",
            "SOURce1:FUNCtion PULSe",
            "SOURce1:FREQuency 1000",
            "SOURce1:FUNCtion:PULSe:WIDTh 0.0001",
            "SOURce1:FUNCtion:PULSe:TRANsition:BOTH 1e-08",
            "SOURce1:VOLTage 0.1",
            "SOURce1:VOLTage:OFFSet 0",
            "UNIT:ANGLe DEGree",
            "SOURce1:PHASe 0",
        ],
        "executed": False,
        "output_state": "off",
        "error": None,
    }


def test_configure_pulse_internal_pwm_dry_run_emits_json_and_human_output(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    arguments = [
        "configure-pulse",
        "--dry-run",
        "--model",
        "keysight-33521b",
        "--frequency-hz",
        "1000",
        "--pulse-width-s",
        "0.0001",
        "--amplitude-vpp",
        "1",
        "--edge-time-s",
        "0.00000005",
        "--pwm-frequency",
        "5",
        "--pwm-deviation-s",
        "0.00002",
    ]

    assert main([*arguments, "--json"]) == ExitCode.SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["pwm_enabled"] is True
    assert payload["pwm_frequency_hz"] == 5.0
    assert payload["pwm_deviation_s"] == 0.00002
    assert payload["commands"][-5:] == [
        "SOURce1:PWM:SOURce INTernal",
        "SOURce1:PWM:INTernal:FUNCtion SINusoid",
        "SOURce1:PWM:INTernal:FREQuency 5",
        "SOURce1:PWM:DEViation 2e-05",
        "SOURce1:PWM:STATe ON",
    ]
    assert payload["output_state"] == "off"
    assert "OUTPut1 ON" not in payload["commands"]

    assert main(arguments) == ExitCode.SUCCESS
    human_output = capsys.readouterr().out
    assert "PWM modulation frequency (Hz): 5.0" in human_output
    assert "PWM width deviation (seconds): 2e-05" in human_output
    assert manager_calls == []
    assert manager.opened_resources == []


def test_incomplete_pwm_cli_group_fails_closed_without_visa(monkeypatch, capsys):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-pulse",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "1000",
            "--pulse-width-s",
            "0.0001",
            "--amplitude-vpp",
            "1",
            "--edge-time-s",
            "0.00000005",
            "--pwm-frequency",
            "5",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert payload["success"] is False
    assert payload["error"].startswith("waveform_parameter_error:")
    assert manager_calls == []
    assert manager.opened_resources == []


def test_configure_dc_dry_run_cli_emits_hardware_free_json(
    monkeypatch, capsys
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    dry_run_calls = []

    def fake_dry_run_dc(*args, **kwargs):
        dry_run_calls.append(args)
        return SimpleNamespace(
            model="33521B",
            canonical_model_id="keysight-33521b",
            voltage_v=1.5,
            load="50",
            commands=(
                "OUTPut1 OFF",
                "SOURce1:AM:STATe OFF",
                "SOURce1:FM:STATe OFF",
                "SOURce1:PM:STATe OFF",
                "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
        "OUTPut1:LOAD 50",
                "SOURce1:FUNCtion DC",
                "SOURce1:VOLTage:OFFSet 1.5",
            ),
            executed=False,
            output_state="off",
        )

    def fail_live_configure_dc(*args, **kwargs):
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
        "channel": 1,
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "voltage_v": 1.5,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
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

    def fake_dry_run_noise(*args, **kwargs):
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
                "SOURce1:AM:STATe OFF",
                "SOURce1:FM:STATe OFF",
                "SOURce1:PM:STATe OFF",
                "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
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

    def fail_live_configure_noise(*args, **kwargs):
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
        ("keysight-33521b", 0.1, "1000000", 0.0, "50")
    ]
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload == {
        "success": True,
        "action": "configure-noise",
        "mode": "dry-run",
        "channel": 1,
        "model": "33521B",
        "canonical_model_id": "keysight-33521b",
        "amplitude_vpp": 0.1,
        "offset_v": 0.0,
        "bandwidth_hz": 1_000_000.0,
        "load": "50",
        "commands": [
            "OUTPut1 OFF",
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
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

    def fake_dry_run_prbs(*args, **kwargs):
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
                "SOURce1:AM:STATe OFF",
                "SOURce1:FM:STATe OFF",
                "SOURce1:PM:STATe OFF",
                "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
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

    def fail_live_configure_prbs(*args, **kwargs):
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
            0.1,
            "PN9",
            0.0,
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
        "channel": 1,
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
            "SOURce1:AM:STATe OFF",
            "SOURce1:FM:STATe OFF",
            "SOURce1:PM:STATe OFF",
            "SOURce1:FSKey:STATe OFF",
        "SOURce1:BPSK:STATe OFF",
        "SOURce1:PWM:STATe OFF",
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

    def fake_set_output(*args, **kwargs):
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
    assert "selected output may remain on" in payload["error"]
    assert session.writes == ["OUTPut1 ON"]


def test_status_cli_parses_arguments_calls_core_and_emits_json(monkeypatch, capsys):
    calls = []

    def fake_query_status(*args, **kwargs):
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
            bit_rate_bps=None,
            amplitude=0.1,
            amplitude_unit="VPP",
            bandwidth_hz=None,
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
        "channel": 1,
        "backend": "system",
        "transport": "usb",
        "manufacturer": "Agilent Technologies",
        "model": "33521B",
        "output_state": "off",
        "function": "SIN",
        "frequency_hz": 1000.0,
        "bit_rate_bps": None,
        "amplitude": 0.1,
        "amplitude_unit": "VPP",
        "bandwidth_hz": None,
        "offset_v": 0.0,
        "load": "50",
        "error": None,
    }


def test_prbs_status_cli_reports_bit_rate(monkeypatch, capsys):
    calls = []
    result = SimpleNamespace(
        backend="system",
        transport="usb",
        identity=SimpleNamespace(
            manufacturer="Keysight Technologies",
            model="33512B",
        ),
        channel=2,
        output_state="off",
        function="PRBS",
        frequency_hz=None,
        bit_rate_bps=1_000_000.0,
        amplitude=0.1,
        amplitude_unit="VPP",
        bandwidth_hz=None,
        offset_v=0.0,
        load="50",
    )

    def fake_query_status(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(cli_module, "query_status", fake_query_status)
    argv = [
        "status",
        "--resource",
        USB_RESOURCE,
        "--channel",
        "2",
    ]

    json_exit_code = main([*argv, "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert json_exit_code == ExitCode.SUCCESS
    assert payload["channel"] == 2
    assert payload["function"] == "PRBS"
    assert payload["frequency_hz"] is None
    assert payload["bit_rate_bps"] == 1_000_000.0

    human_exit_code = main(argv)
    human_output = capsys.readouterr().out

    assert human_exit_code == ExitCode.SUCCESS
    assert "Bit rate: 1e+06 bit/s" in human_output
    assert "Frequency:" not in human_output
    assert len(calls) == 2
    assert all(call[1]["channel"] == 2 for call in calls)


def test_read_errors_cli_json_success_with_instrument_error(monkeypatch, capsys):
    calls = []

    def fake_read_error_queue(resource, backend, *, max_reads, **kwargs):
        calls.append((resource, backend, max_reads, kwargs))
        return SimpleNamespace(
            backend=backend,
            transport="tcpip",
            identity=SimpleNamespace(
                manufacturer="Keysight Technologies",
                model="33521B",
            ),
            errors=(
                SimpleNamespace(
                    code=-113,
                    message="Undefined header",
                    raw_response='-113,"Undefined header"',
                ),
            ),
            read_count=2,
            max_reads=max_reads,
            empty_confirmed=True,
            limit_reached=False,
        )

    monkeypatch.setattr(cli_module, "read_error_queue", fake_read_error_queue)

    exit_code = main(
        [
            "read-errors",
            "--resource",
            TCPIP_RESOURCE,
            "--backend",
            "@py",
            "--max-reads",
            "7",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.SUCCESS
    assert calls == [(TCPIP_RESOURCE, "@py", 7, {})]
    assert payload == {
        "success": True,
        "action": "read-errors",
        "backend": "@py",
        "transport": "tcpip",
        "manufacturer": "Keysight Technologies",
        "model": "33521B",
        "errors": [
            {
                "code": -113,
                "message": "Undefined header",
                "raw_response": '-113,"Undefined header"',
            }
        ],
        "read_count": 2,
        "max_reads": 7,
        "has_errors": True,
        "empty_confirmed": True,
        "limit_reached": False,
        "error": None,
    }
    assert captured.out.count("\n") == 1
    assert captured.err == ""


@pytest.mark.parametrize(
    ("command", "core_function", "extra_args"),
    [
        ("identify", "identify_instrument", []),
        ("status", "query_status", []),
        ("read-errors", "read_error_queue", []),
        ("output", "set_output", ["--state", "off"]),
    ],
)
def test_validation_direct_routes_forward_policy_and_expected_model(
    monkeypatch,
    capsys,
    command,
    core_function,
    extra_args,
):
    identity = SimpleNamespace(
        manufacturer="Keysight Technologies",
        model="33512B",
        serial="MY00000000",
        firmware="1.00",
        canonical_model_id="keysight-33512b",
        model_supported=True,
    )
    values = {
        "backend": "system",
        "transport": "usb",
        "identity": identity,
    }
    if command == "status":
        values.update(
            output_state="off",
            function="SIN",
            frequency_hz=1000.0,
            bit_rate_bps=None,
            amplitude=0.1,
            amplitude_unit="VPP",
            bandwidth_hz=None,
            offset_v=0.0,
            load="50",
        )
    elif command == "read-errors":
        values.update(
            errors=(),
            read_count=1,
            max_reads=20,
            empty_confirmed=True,
            limit_reached=False,
        )
    elif command == "output":
        values["output_state"] = "off"
    result = SimpleNamespace(**values)
    calls = []

    def fake_core(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(cli_module, core_function, fake_core)

    exit_code = main(
        [
            command,
            "--resource",
            USB_RESOURCE,
            "--validation-allow-pending-live-support",
            "--model",
            "keysight-33512b",
            *extra_args,
            "--json",
        ]
    )

    assert exit_code == ExitCode.SUCCESS
    assert len(calls) == 1
    assert calls[0][1]["support_policy_mode"] == "validation"
    assert calls[0][1]["expected_model_id"] == "keysight-33512b"
    assert json.loads(capsys.readouterr().out)["success"] is True


def test_read_errors_cli_human_empty_queue_output(monkeypatch, capsys):
    def fake_read_error_queue(*args, **kwargs):
        return SimpleNamespace(
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Keysight Technologies",
                model="33521B",
            ),
            errors=(),
            read_count=1,
            max_reads=20,
            empty_confirmed=True,
            limit_reached=False,
        )

    monkeypatch.setattr(cli_module, "read_error_queue", fake_read_error_queue)

    exit_code = main(["read-errors", "--resource", USB_RESOURCE])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert "Instrument: Keysight Technologies 33521B" in captured.out
    assert "System error queue: no errors" in captured.out
    assert "Reads: 1/20" in captured.out
    assert "Queue empty confirmed: yes" in captured.out
    assert "Read limit reached: no" in captured.out
    assert captured.err == ""


def test_read_errors_cli_query_error_returns_exit_code_30(monkeypatch, capsys):
    def fake_read_error_queue(*args, **kwargs):
        raise ErrorQueueQueryError(
            "SYSTem:ERRor? query failed",
            backend="system",
            transport="usb",
            identity=SimpleNamespace(
                manufacturer="Keysight Technologies",
                model="33521B",
            ),
        )

    monkeypatch.setattr(cli_module, "read_error_queue", fake_read_error_queue)

    exit_code = main(
        [
            "read-errors",
            "--resource",
            USB_RESOURCE,
            "--max-reads",
            "4",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.ERROR_QUEUE_QUERY_ERROR
    assert payload["success"] is False
    assert payload["action"] == "read-errors"
    assert payload["backend"] == "system"
    assert payload["transport"] == "usb"
    assert payload["manufacturer"] == "Keysight Technologies"
    assert payload["model"] == "33521B"
    assert payload["errors"] == []
    assert payload["read_count"] == 0
    assert payload["max_reads"] == 4
    assert payload["has_errors"] is False
    assert payload["empty_confirmed"] is False
    assert payload["limit_reached"] is False
    assert payload["error"] == (
        "error_queue_query_error: SYSTem:ERRor? query failed"
    )
    assert captured.err == ""


@pytest.mark.parametrize("max_reads", [0, 101])
def test_read_errors_rejects_invalid_max_reads_before_core(
    monkeypatch,
    capsys,
    max_reads,
):
    calls = []

    def fail_read_error_queue(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(cli_module, "read_error_queue", fail_read_error_queue)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "read-errors",
                "--resource",
                USB_RESOURCE,
                "--max-reads",
                str(max_reads),
            ]
        )

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert calls == []
    assert "max_reads must be an integer between 1 and 100" in captured.err
    assert captured.out == ""


def test_read_errors_cli_simulation_json_smoke(capsys):
    exit_code = main(["read-errors", "--simulate", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["success"] is True
    assert payload["action"] == "read-errors"
    assert payload["errors"] == []
    assert payload["read_count"] == 1
    assert payload["empty_confirmed"] is True
    assert payload["mode"] == "simulate"
    assert payload["simulated"] is True
    assert captured.err == ""


@pytest.mark.parametrize(
    ("argv", "expected_action", "specific_field", "specific_value"),
    [
        (
            [
                "configure-square-sweep",
                "--dry-run",
                "--model",
                "keysight-33521b",
                "--start-frequency-hz",
                "1000",
                "--stop-frequency-hz",
                "30000",
                "--spacing",
                "linear",
                "--sweep-time-s",
                "1",
                "--amplitude-vpp",
                "0.1",
                "--duty-cycle-percent",
                "25",
                "--json",
            ],
            "configure-square-sweep",
            "duty_cycle_percent",
            25.0,
        ),
        (
            [
                "configure-ramp-sweep",
                "--dry-run",
                "--model",
                "keysight-33521b",
                "--start-frequency-hz",
                "10000",
                "--stop-frequency-hz",
                "1000",
                "--spacing",
                "logarithmic",
                "--sweep-time-s",
                "2",
                "--amplitude-vpp",
                "0.1",
                "--symmetry-percent",
                "25",
                "--json",
            ],
            "configure-ramp-sweep",
            "symmetry_percent",
            25.0,
        ),
        (
            [
                "configure-triangle-sweep",
                "--dry-run",
                "--model",
                "keysight-33521b",
                "--start-frequency-hz",
                "200000",
                "--stop-frequency-hz",
                "1000",
                "--spacing",
                "logarithmic",
                "--sweep-time-s",
                "2",
                "--amplitude-vpp",
                "0.1",
                "--json",
            ],
            "configure-triangle-sweep",
            "spacing",
            "logarithmic",
        ),
    ],
)
def test_frequency_sweep_dry_run_cli_json_matrix(
    monkeypatch,
    capsys,
    argv,
    expected_action,
    specific_field,
    specific_value,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(argv)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["success"] is True
    assert payload["action"] == expected_action
    assert payload["mode"] == "dry-run"
    assert payload["model"] == "33521B"
    assert payload["canonical_model_id"] == "keysight-33521b"
    assert payload["channel"] == 1
    assert payload["trigger_source"] == "immediate"
    assert payload["output_state"] == "off"
    assert payload[specific_field] == specific_value
    assert payload["commands"][0] == "OUTPut1 OFF"
    assert payload["commands"][-1] == "SOURce1:FREQuency:MODE SWEep"
    assert "SOURce1:FREQuency:MODE CW" not in payload["commands"]
    assert payload["executed"] is False
    assert captured.err == ""


def test_configure_sine_sweep_cli_channel_two_json(capsys):
    exit_code = main(
        [
            "configure-sine-sweep",
            "--simulate",
            "--model",
            "keysight-33512b",
            "--channel",
            "2",
            "--start-frequency-hz",
            "1000",
            "--stop-frequency-hz",
            "10000",
            "--spacing",
            "linear",
            "--sweep-time-s",
            "1",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["success"] is True
    assert payload["mode"] == "simulate"
    assert payload["model"] == "33512B"
    assert payload["channel"] == 2
    assert payload["start_frequency_hz"] == 1000.0
    assert payload["stop_frequency_hz"] == 10000.0
    assert payload["output_state"] == "off"


@pytest.mark.parametrize(
    ("argv", "expected_action", "field", "expected"),
    [
        (
            ["list-resources", "--simulate", "--json"],
            None,
            "resources",
            [
                {
                    "resource": "USB0::SIM::33521B::INSTR",
                    "manufacturer": None,
                    "model": None,
                }
            ],
        ),
        (
            ["identify", "--simulate", "--json"],
            None,
            "canonical_model_id",
            "keysight-33521b",
        ),
        (
            ["status", "--simulate", "--json"],
            "status",
            "function",
            "SIN",
        ),
        (
            [
                "configure-sine",
                "--simulate",
                "--frequency-hz",
                "1000",
                "--high-level-v",
                "3.3",
                "--low-level-v",
                "0",
                "--json",
            ],
            "configure-sine",
            "amplitude_vpp",
            3.3,
        ),
        (
            [
                "configure-sine-sweep",
                "--simulate",
                "--start-frequency-hz",
                "1000",
                "--stop-frequency-hz",
                "10000",
                "--spacing",
                "linear",
                "--sweep-time-s",
                "1",
                "--amplitude-vpp",
                "0.1",
                "--json",
            ],
            "configure-sine-sweep",
            "spacing",
            "linear",
        ),
        (
            [
                "configure-square-sweep",
                "--simulate",
                "--start-frequency-hz",
                "1000",
                "--stop-frequency-hz",
                "30000",
                "--spacing",
                "linear",
                "--sweep-time-s",
                "1",
                "--amplitude-vpp",
                "0.1",
                "--duty-cycle-percent",
                "25",
                "--json",
            ],
            "configure-square-sweep",
            "duty_cycle_percent",
            25.0,
        ),
        (
            [
                "configure-ramp-sweep",
                "--simulate",
                "--start-frequency-hz",
                "10000",
                "--stop-frequency-hz",
                "1000",
                "--spacing",
                "logarithmic",
                "--sweep-time-s",
                "2",
                "--amplitude-vpp",
                "0.1",
                "--symmetry-percent",
                "25",
                "--json",
            ],
            "configure-ramp-sweep",
            "symmetry_percent",
            25.0,
        ),
        (
            [
                "configure-triangle-sweep",
                "--simulate",
                "--start-frequency-hz",
                "200000",
                "--stop-frequency-hz",
                "1000",
                "--spacing",
                "logarithmic",
                "--sweep-time-s",
                "2",
                "--amplitude-vpp",
                "0.1",
                "--json",
            ],
            "configure-triangle-sweep",
            "spacing",
            "logarithmic",
        ),
        (
            [
                "configure-square",
                "--simulate",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "0.1",
                "--duty-cycle-percent",
                "25",
                "--json",
            ],
            "configure-square",
            "duty_cycle_percent",
            25.0,
        ),
        (
            [
                "configure-ramp",
                "--simulate",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "0.1",
                "--symmetry-percent",
                "25",
                "--json",
            ],
            "configure-ramp",
            "symmetry_percent",
            25.0,
        ),
        (
            [
                "configure-triangle",
                "--simulate",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "0.1",
                "--offset-v",
                "0.2",
                "--json",
            ],
            "configure-triangle",
            "frequency_hz",
            1000.0,
        ),
        (
            [
                "configure-pulse",
                "--simulate",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "0.1",
                "--pulse-width-s",
                "0.0001",
                "--json",
            ],
            "configure-pulse",
            "pulse_width_s",
            0.0001,
        ),
        (
            [
                "configure-dc",
                "--simulate",
                "--voltage-v",
                "1.5",
                "--json",
            ],
            "configure-dc",
            "voltage_v",
            1.5,
        ),
        (
            [
                "configure-noise",
                "--simulate",
                "--amplitude-vpp",
                "0.1",
                "--bandwidth-hz",
                "100000",
                "--json",
            ],
            "configure-noise",
            "bandwidth_hz",
            100000.0,
        ),
        (
            [
                "configure-prbs",
                "--simulate",
                "--bit-rate-bps",
                "1000000",
                "--amplitude-vpp",
                "0.1",
                "--pattern",
                "pn9",
                "--json",
            ],
            "configure-prbs",
            "pattern",
            "PN9",
        ),
        (
            ["output", "--simulate", "--state", "on", "--json"],
            "output",
            "output_state",
            "on",
        ),
    ],
)
def test_all_public_cli_routes_support_simulation_without_real_visa(
    monkeypatch,
    capsys,
    argv,
    expected_action,
    field,
    expected,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(argv)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == []
    assert manager.opened_resources == []
    assert payload["mode"] == "simulate"
    assert payload["simulated"] is True
    assert payload.get("action") == expected_action
    assert payload[field] == expected
    assert captured.err == ""


def test_output_simulator_dispatches_33512b_channel_two(capsys):
    exit_code = main(
        [
            "output",
            "--simulate",
            "--model",
            "keysight-33512b",
            "--channel",
            "2",
            "--state",
            "on",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["model"] == "33512B"
    assert payload["channel"] == 2
    assert payload["output_state"] == "on"
    assert payload["mode"] == "simulate"


def test_status_simulator_dispatches_33512b_channel_two(capsys):
    exit_code = main(
        [
            "status",
            "--simulate",
            "--model",
            "keysight-33512b",
            "--channel",
            "2",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["model"] == "33512B"
    assert payload["channel"] == 2
    assert payload["output_state"] == "off"
    assert payload["mode"] == "simulate"


def test_configure_sine_simulator_dispatches_33510b_identity_and_capability(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)
    common_args = [
        "configure-sine",
        "--simulate",
        "--model",
        "keysight-33510b",
        "--amplitude-vpp",
        "0.1",
        "--json",
    ]

    exit_code = main([*common_args, "--frequency-hz", "20000000"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["model"] == "33510B"
    assert payload["frequency_hz"] == 20_000_000.0
    assert payload["mode"] == "simulate"

    exit_code = main([*common_args, "--frequency-hz", "25000000"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert payload["success"] is False
    assert "20000000 Hz" in payload["error"]
    assert manager_calls == []
    assert manager.opened_resources == []


def test_configure_sine_product_live_accepts_matching_33512b(
    monkeypatch,
    capsys,
):
    session = FakeSession(
        response="Keysight Technologies,33512B,MY00000000,1.00"
    )
    manager = FakeManager(session)
    manager_calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--resource",
            USB_RESOURCE,
            "--frequency-hz",
            "20000000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert manager_calls == ["@ivi"]
    assert session.queries == [
        "*IDN?",
        "SOURce1:FREQuency:COUPle:STATe?",
        "SOURce1:VOLTage:COUPle:STATe?",
        "SOURce1:TRACk?",
        "SOURce2:TRACk?",
    ]
    assert session.writes[0] == "OUTPut1 OFF"
    assert "OUTPut1 ON" not in session.writes
    assert payload["model"] == "33512B"
    assert payload["frequency_hz"] == 20_000_000.0


def test_configure_sine_validation_live_rejects_expected_model_mismatch(
    monkeypatch,
    capsys,
):
    session = FakeSession()
    manager = FakeManager(session)
    install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--resource",
            USB_RESOURCE,
            "--model",
            "keysight-33512b",
            "--validation-allow-pending-live-support",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.UNSUPPORTED_INSTRUMENT
    assert "expected exact model ID" in payload["error"]
    assert session.queries == ["*IDN?"]
    assert session.writes == []


def test_configure_sine_validation_live_uses_detected_33512b_capability(
    monkeypatch,
    capsys,
):
    session = FakeSession(
        response="Keysight Technologies,33512B,MY00000000,1.00"
    )
    manager = FakeManager(session)
    install_fake_manager(monkeypatch, manager)

    exit_code = main(
        [
            "configure-sine",
            "--resource",
            USB_RESOURCE,
            "--model",
            "keysight-33512b",
            "--validation-allow-pending-live-support",
            "--frequency-hz",
            "25000000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert "20000000 Hz" in payload["error"]
    assert session.queries == [
        "*IDN?",
        "SOURce1:FREQuency:COUPle:STATe?",
        "SOURce1:VOLTage:COUPle:STATe?",
        "SOURce1:TRACk?",
        "SOURce2:TRACk?",
    ]
    assert session.writes == []


def test_configure_sine_cli_channel_two_dry_run_emits_channel_two_scpi(capsys):
    exit_code = main(
        [
            "configure-sine",
            "--dry-run",
            "--model",
            "keysight-33512b",
            "--channel",
            "2",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["channel"] == 2
    assert "OUTPut2 OFF" in payload["commands"]
    assert all("SOURce1" not in command for command in payload["commands"])


def test_configure_sine_cli_rejects_33521b_channel_two_in_simulator(capsys):
    exit_code = main(
        [
            "configure-sine",
            "--simulate",
            "--model",
            "keysight-33521b",
            "--channel",
            "2",
            "--frequency-hz",
            "1000",
            "--amplitude-vpp",
            "0.1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.CLI_USAGE
    assert payload["channel"] == 2
    assert "Channel 2" in payload["error"]


@pytest.mark.parametrize("model_id", ["keysight-33510b", "keysight-33512b"])
def test_non_default_model_is_rejected_for_live_configure_before_visa_io(
    monkeypatch,
    capsys,
    model_id,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "configure-sine",
                "--resource",
                USB_RESOURCE,
                "--model",
                model_id,
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
    assert "requires --dry-run or --simulate" in captured.err


def test_dry_run_and_simulate_conflict_is_usage_error_without_visa(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "configure-sine",
                "--dry-run",
                "--simulate",
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
    assert "--dry-run and --simulate cannot be used together" in captured.err
    assert "Traceback" not in captured.err


def test_simulate_and_explicit_resource_conflict_is_usage_error_without_visa(
    monkeypatch,
    capsys,
):
    manager = FakeManager()
    manager_calls = install_fake_manager(monkeypatch, manager)

    with pytest.raises(SystemExit) as error:
        main(["identify", "--simulate", "--resource", USB_RESOURCE])

    captured = capsys.readouterr()
    assert error.value.code == ExitCode.CLI_USAGE
    assert manager_calls == []
    assert manager.opened_resources == []
    assert "--resource cannot be used with --simulate" in captured.err
    assert "Traceback" not in captured.err


def test_pyvisa_bt_human_error_is_fail_closed(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(["identify", "--resource", TCPIP_RESOURCE, "--backend", "@bt"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.UNSUPPORTED_CONNECTION_SCOPE
    assert calls == []
    assert manager.opened_resources == []
    assert captured.out == ""
    assert "Error [unsupported_connection_scope]" in captured.err
    assert "The '@bt' backend is recognized as 'pyvisa_bt'" in captured.err
    assert "no Product-open live connection scope" in captured.err


def test_pyvisa_bt_json_error_is_one_object(monkeypatch, capsys):
    manager = FakeManager()
    calls = install_fake_manager(monkeypatch, manager)

    exit_code = main(
        ["identify", "--resource", TCPIP_RESOURCE, "--backend", "@bt", "--json"]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == ExitCode.UNSUPPORTED_CONNECTION_SCOPE
    assert calls == []
    assert manager.opened_resources == []
    assert payload["backend"] == "@bt"
    assert payload["transport"] == "tcpip"
    assert payload["model_supported"] is False
    assert payload["error"].startswith("unsupported_connection_scope:")
    assert captured.out.count("\n") == 1
    assert captured.err == ""
