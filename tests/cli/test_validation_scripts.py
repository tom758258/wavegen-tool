from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_SCRIPT = REPO_ROOT / "scripts" / "preflight-cli.ps1"
LIVE_SCRIPT = REPO_ROOT / "scripts" / "live-cli-check.ps1"
POWERSHELL = shutil.which("powershell")
USB_RESOURCE = "USB0::0x0957::0x0000::SYNTHETIC123::INSTR"


@pytest.fixture
def artifact_root() -> Path:
    root = REPO_ROOT / ".tmp_tests" / f"validation_scripts_{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run_script(
    script: Path,
    artifact_root: Path,
    *arguments: str,
    python: str | Path = sys.executable,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is unavailable")
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
            "-Python",
            str(python),
            "-OutputRoot",
            str(artifact_root),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=stdin,
        check=False,
    )


def _only_run(artifact_root: Path) -> Path:
    runs = sorted(path for path in artifact_root.iterdir() if path.name.startswith("run_"))
    assert len(runs) == 1
    return runs[0]


def _read_report(run_root: Path, visibility: str = "private") -> dict[str, object]:
    return json.loads(
        (run_root / visibility / "report.json").read_text(encoding="utf-8")
    )


def test_preflight_all_uses_capability_driven_channel_coverage(
    artifact_root: Path,
) -> None:
    result = _run_script(PREFLIGHT_SCRIPT, artifact_root, "-Target", "all")

    assert result.returncode == 0, result.stdout + result.stderr
    run_root = _only_run(artifact_root)
    report = _read_report(run_root)
    target_results = {
        item["expected_model"]: item for item in report["target_results"]
    }
    assert report["status"] == "PASS"
    assert report["targets"] == [
        "keysight-33510b",
        "keysight-33512b",
        "keysight-33521b",
    ]
    assert target_results["keysight-33510b"]["channel_count"] == 2
    assert target_results["keysight-33510b"]["channels_tested"] == [1, 2]
    assert target_results["keysight-33512b"]["channel_count"] == 2
    assert target_results["keysight-33512b"]["channels_tested"] == [1, 2]
    assert target_results["keysight-33521b"]["channel_count"] == 1
    assert target_results["keysight-33521b"]["channels_tested"] == [1]
    case_names = {case["name"] for case in report["cases"]}
    for model_id, channels in {
        "keysight-33510b": (1, 2),
        "keysight-33512b": (1, 2),
        "keysight-33521b": (1,),
    }.items():
        for channel in channels:
            assert f"{model_id}/ch{channel}-sine-dry-run" in case_names
            assert f"{model_id}/ch{channel}-sine-simulate" in case_names
    assert report["validation_mode"] == "no-hardware-cli-preflight"
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False
    assert "detected_model" not in report
    assert (run_root / "shareable" / "report.json").is_file()
    shareable = _read_report(run_root, "shareable")
    assert shareable["target_results"][0]["channels_tested"] == [1, 2]


def test_preflight_unknown_target_fails_closed(artifact_root: Path) -> None:
    result = _run_script(
        PREFLIGHT_SCRIPT,
        artifact_root,
        "-Target",
        "keysight-unknown",
    )

    assert result.returncode == 2
    assert "Unsupported target" in result.stderr
    assert list(artifact_root.iterdir()) == []


def test_preflight_capability_failure_marks_channel_stages_na(
    artifact_root: Path,
) -> None:
    missing_python = artifact_root / "missing-python.exe"
    result = _run_script(
        PREFLIGHT_SCRIPT,
        artifact_root,
        "-Target",
        "keysight-33521b",
        python=missing_python,
    )

    assert result.returncode == 1
    report = _read_report(_only_run(artifact_root))
    statuses = {case["name"]: case["status"] for case in report["cases"]}
    assert statuses["keysight-33521b/capabilities"] == "FAIL"
    assert statuses["keysight-33521b/channel-sine-dry-run"] == "N/A"
    assert statuses["keysight-33521b/channel-sine-simulate"] == "N/A"
    assert not any("/ch1-" in name for name in statuses)
    assert report["target_results"][0]["channel_count"] is None


@pytest.mark.parametrize(
    ("model_id", "expected_channels", "unexpected_case"),
    [
        ("keysight-33512b", [1, 2], None),
        ("keysight-33521b", [1], "ch2/sine-config"),
    ],
)
def test_live_plan_only_is_hardware_free_and_capability_driven(
    artifact_root: Path,
    model_id: str,
    expected_channels: list[int],
    unexpected_case: str | None,
) -> None:
    result = _run_script(
        LIVE_SCRIPT,
        artifact_root,
        "-Target",
        model_id,
        "-Connection",
        "usb",
        "-Resource",
        USB_RESOURCE,
        "-PlanOnly",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_root = _only_run(artifact_root)
    report = _read_report(run_root)
    assert report["status"] == "PASS"
    assert report["validation_mode"] == "live-plan-only"
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False
    assert report["channels_planned"] == expected_channels
    assert report["channels_tested"] == []
    assert "detected_model" not in report
    assert "Wavegen Tool Live CLI Validation" in result.stdout
    assert f"Target: {model_id}" in result.stdout
    assert "Connection: usb" in result.stdout
    assert "Backend: system" in result.stdout
    assert "Resource: <redacted-resource>" in result.stdout
    assert "The runner commands each exercised channel OFF after validation." in result.stdout
    assert "Best-effort OFF cleanup is attempted after failures." in result.stdout
    assert f"Channels: {', '.join(map(str, expected_channels))}" in result.stdout
    assert "Validation cases:" in result.stdout
    assert "identity" in result.stdout
    assert "baseline-error-drain" in result.stdout
    assert "Representative waveform:" in result.stdout
    assert "Frequency: 1000 Hz" in result.stdout
    assert "Amplitude: 0.1 Vpp" in result.stdout
    assert "Offset: 0 V" in result.stdout
    assert "Phase: 0 deg" in result.stdout
    assert "Load: 50 ohm" in result.stdout
    assert "Output ON will not be used" in result.stdout
    assert "No reset, preset or recall is performed" in result.stdout
    assert "Coupling or tracking is not changed automatically" in result.stdout
    assert "hardware_touched=false" in result.stdout
    assert "visa_io_performed=false" in result.stdout
    assert "Result: PLANNED" in result.stdout
    assert "Private artifacts:" in result.stdout
    assert "Shareable report:" in result.stdout
    assert "Shareable summary:" in result.stdout
    for channel in expected_channels:
        assert f"ch{channel}/sine-config" in report["planned_cases"]
        assert f"ch{channel}/sine-config" in result.stdout
        assert f"ch{channel}/sine-readback" in result.stdout
    if unexpected_case is not None:
        assert unexpected_case not in report["planned_cases"]
    shareable_root = run_root / "shareable"
    assert (shareable_root / "report.json").is_file()
    assert str(run_root / "private") in result.stdout
    assert str(shareable_root / "report.json") in result.stdout
    assert str(shareable_root / "summary.md") in result.stdout
    shareable_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in shareable_root.rglob("*")
        if path.is_file()
    )
    assert USB_RESOURCE not in shareable_text
    assert "SYNTHETIC123" not in shareable_text
    shareable = _read_report(run_root, "shareable")
    assert shareable["channels_planned"] == expected_channels
    assert shareable["candidate_evidence_only"] is True
    assert shareable["promotes_live_support"] is False


def test_live_redirected_stdin_cannot_authorize_hardware(
    artifact_root: Path,
) -> None:
    result = _run_script(
        LIVE_SCRIPT,
        artifact_root,
        "-Target",
        "keysight-33512b",
        "-Connection",
        "usb",
        "-Resource",
        USB_RESOURCE,
        stdin="YES\n",
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "redirected stdin is rejected" in result.stdout
    assert "Wavegen Tool Live CLI Validation" in result.stdout
    assert "Target: keysight-33512b" in result.stdout
    assert "Connection: usb" in result.stdout
    assert "Backend: system" in result.stdout
    assert "Channels: 1, 2" in result.stdout
    assert "Validation cases:" in result.stdout
    assert "ch1/sine-config" in result.stdout
    assert "ch2/sine-readback" in result.stdout
    assert "Frequency: 1000 Hz" in result.stdout
    assert "Amplitude: 0.1 Vpp" in result.stdout
    assert "Output ON will not be used" in result.stdout
    assert "Resource: " + USB_RESOURCE in result.stdout
    assert "Resource: <redacted-resource>" not in result.stdout
    assert "The runner commands each exercised channel OFF after validation." in result.stdout
    assert "Best-effort OFF cleanup is attempted after failures." in result.stdout
    assert "No reset, preset or recall is performed" in result.stdout
    assert "Coupling or tracking is not changed automatically" in result.stdout
    assert "Result: CANCELLED" in result.stdout
    assert "Private artifacts:" in result.stdout
    assert "Shareable report:" in result.stdout
    assert "Shareable summary:" in result.stdout
    run_root = _only_run(artifact_root)
    report = _read_report(run_root)
    assert report["status"] == "CANCELLED"
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False
    assert (run_root / "private" / "report.json").is_file()
    assert (run_root / "shareable" / "report.json").is_file()
    assert str(run_root / "private") in result.stdout
    assert str(run_root / "shareable" / "report.json") in result.stdout
    assert str(run_root / "shareable" / "summary.md") in result.stdout
    assert not (run_root / "private" / "identity").exists()

    script_text = LIVE_SCRIPT.read_text(encoding="utf-8")
    assert 'Write-Host "Resource: $resourceDisplay"' in script_text
    assert 'Write-Host "  - The runner commands each exercised channel OFF after validation."' in script_text
    assert 'Write-Host "  - Best-effort OFF cleanup is attempted after failures."' in script_text
    assert 'Read-Host "Type YES to begin Live validation, or Ctrl+C to cancel"' in script_text
    assert '$confirmation -cne "YES"' in script_text


@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    [
        (
            [
                "-Target",
                "all",
                "-Connection",
                "usb",
                "-Resource",
                USB_RESOURCE,
                "-PlanOnly",
            ],
            "single canonical target",
        ),
        (
            [
                "-Target",
                "keysight-33512b",
                "-Connection",
                "usb",
                "-Resource",
                "TCPIP0::synthetic.invalid::inst0::INSTR",
                "-PlanOnly",
            ],
            "does not match",
        ),
        (
            [
                "-Target",
                "keysight-33512b",
                "-Connection",
                "tcpip",
                "-Resource",
                USB_RESOURCE,
                "-PlanOnly",
            ],
            "does not match",
        ),
    ],
)
def test_live_usage_failures_are_rejected_before_hardware(
    artifact_root: Path,
    arguments: list[str],
    expected_error: str,
) -> None:
    result = _run_script(LIVE_SCRIPT, artifact_root, *arguments)

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert list(artifact_root.iterdir()) == []
