import json
from dataclasses import asdict

import wavegen_tool_cli.cli as cli_module
from wavegen_tool_cli.cli import ExitCode, main
from wavegen_tool_core import visa
from wavegen_tool_core.capabilities import capabilities_for_model_id


MODEL_ID = "keysight-33521b"


def _fail_if_called(*args, **kwargs):
    raise AssertionError("offline capabilities query must not perform this operation")


def test_capabilities_json_matches_core_registry(capsys) -> None:
    exit_code = main(["capabilities", "--model", MODEL_ID, "--json"])

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    core_capabilities = capabilities_for_model_id(MODEL_ID)
    assert core_capabilities is not None
    assert exit_code == ExitCode.SUCCESS
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "event": "capabilities",
        "schema_version": 2,
        "tool_id": "wavegen",
        "selection": {"requested_model": MODEL_ID},
        "model": {
            "model_id": MODEL_ID,
            "canonical_model": "33521B",
        },
        "capabilities": asdict(core_capabilities),
    }
    assert captured.err == ""


def test_capabilities_json_is_offline_and_creates_no_files(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(visa, "create_resource_manager", _fail_if_called)
    monkeypatch.setattr(cli_module, "run_worker", _fail_if_called)
    monkeypatch.chdir(tmp_path)

    exit_code = main(["capabilities", "--model", MODEL_ID, "--json"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert json.loads(captured.out)["event"] == "capabilities"
    assert captured.err == ""
    assert list(tmp_path.iterdir()) == []


def test_unknown_model_is_structured_error_without_visa(monkeypatch, capsys) -> None:
    monkeypatch.setattr(visa, "create_resource_manager", _fail_if_called)

    exit_code = main(
        ["capabilities", "--model", "definitely-unknown", "--json"]
    )

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert exit_code == ExitCode.CLI_USAGE
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "event": "error",
        "schema_version": 2,
        "tool_id": "wavegen",
        "ok": False,
        "error": "invalid_request",
        "message": (
            "Unsupported model ID 'definitely-unknown'; "
            "expected an exact registered model ID."
        ),
        "exit_code": 2,
        "selection": {"requested_model": "definitely-unknown"},
    }
    assert captured.err == ""
