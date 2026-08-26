import json

import wavegen_tool_cli.manifest as manifest_module
from wavegen_tool_cli.cli import ExitCode, main
from wavegen_tool_cli.manifest import build_tool_manifest

EXPECTED_MANIFEST = {
    "event": "tool_manifest",
    "schema_version": 2,
    "tool_id": "wavegen",
    "tool_version": "9.9.9",
    "worker_protocol": {
        "compatibility_policy": "v2-only",
        "schema_versions": [2],
    },
}


def test_build_tool_manifest_payload_shape() -> None:
    payload = build_tool_manifest(tool_version="9.9.9")

    assert payload == EXPECTED_MANIFEST


def test_manifest_json_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(manifest_module, "_package_version", lambda: "9.9.9")

    exit_code = main(["manifest", "--json"])

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert exit_code == ExitCode.SUCCESS
    assert len(lines) == 1
    assert lines[0].strip()
    assert json.loads(lines[0]) == EXPECTED_MANIFEST
    assert captured.err == ""


def test_manifest_json_has_no_filesystem_side_effects(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(manifest_module, "_package_version", lambda: "9.9.9")
    monkeypatch.chdir(tmp_path)

    exit_code = main(["manifest", "--json"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert json.loads(captured.out)
    assert captured.err == ""
    assert list(tmp_path.iterdir()) == []
