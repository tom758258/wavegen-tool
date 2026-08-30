from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from wavegen_tool_core.identity import model_info_for_model_id, registered_model_ids


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS_PATH = REPO_ROOT / "scripts" / "_validation_helpers.ps1"
PRIVACY_PATH = REPO_ROOT / "scripts" / "_artifact_privacy.ps1"
POWERSHELL = shutil.which("powershell")


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is unavailable")
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"PowerShell failed with exit code {result.returncode}:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _dot_source(*paths: Path) -> str:
    statements = ["$ErrorActionPreference = 'Stop'"]
    statements.extend(f". {_ps_quote(path)}" for path in paths)
    return "; ".join(statements) + "; "


@pytest.fixture
def artifact_root() -> Path:
    root = REPO_ROOT / ".tmp_tests" / f"validation_helpers_{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_validation_helpers_dot_source() -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH, PRIVACY_PATH) + "Write-Output 'loaded'"
    )

    assert result.stdout.strip() == "loaded"


def test_target_profile_mirror_matches_core_registry() -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "$script:ValidationTargetProfiles | ConvertTo-Json -Compress"
    )
    profiles = json.loads(result.stdout)
    expected_ids = registered_model_ids()

    assert tuple(profile["model_id"] for profile in profiles) == expected_ids
    assert {
        profile["model_id"]: profile["model"] for profile in profiles
    } == {
        model_id: model_info_for_model_id(model_id).canonical_model
        for model_id in expected_ids
    }


def test_target_resolution_known_and_all() -> None:
    known = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "Resolve-ValidationTarget -Target 'keysight-33521b' | ConvertTo-Json -Compress"
    )
    all_targets = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "Resolve-ValidationTargets -Target 'all' | ConvertTo-Json -Compress"
    )

    assert json.loads(known.stdout) == {
        "model_id": "keysight-33521b",
        "model": "33521B",
    }
    assert [item["model_id"] for item in json.loads(all_targets.stdout)] == list(
        registered_model_ids()
    )


def test_target_resolution_unknown_fails_closed() -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "Resolve-ValidationTarget -Target 'keysight-unknown' | Out-Null",
        check=False,
    )

    assert result.returncode != 0
    assert "Unsupported target" in result.stderr


def test_path_safety_allows_tmp_tests_and_rejects_outside(artifact_root: Path) -> None:
    allowed = artifact_root / "inside"
    outside = REPO_ROOT / "outside-validation-artifacts"
    script = (
        _dot_source(HELPERS_PATH)
        + f"Assert-PathUnderRoot -RootPath {_ps_quote(REPO_ROOT / '.tmp_tests')} "
        + f"-Path {_ps_quote(allowed)}; Write-Output 'allowed'"
    )
    assert _run_powershell(script).stdout.strip() == "allowed"

    rejected = _run_powershell(
        _dot_source(HELPERS_PATH)
        + f"New-ValidationRunDirectory -RepoRoot {_ps_quote(REPO_ROOT)} "
        + f"-BaseRoot {_ps_quote(outside)} | Out-Null",
        check=False,
    )
    assert rejected.returncode != 0
    assert not outside.exists()


def test_case_status_formats_pass_fail_and_na() -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "Write-CaseStatus -Status PASS -Context '[preflight][cli]' "
        + "-Name 'keysight-33521b/sine-dry-run'; "
        + "Write-CaseStatus -Status FAIL -Context '[preflight][cli]' "
        + "-Name 'keysight-33510b/example' -FailureReasons 'synthetic failure'; "
        + "Write-CaseStatus -Status 'N/A' -Context '[live][cli]' -Name 'example'"
    )

    assert "PASS  [preflight][cli] keysight-33521b/sine-dry-run" in result.stdout
    assert "FAIL  [preflight][cli] keysight-33510b/example" in result.stdout
    assert "failure reason: synthetic failure" in result.stdout
    assert "N/A   [live][cli] example" in result.stdout


def test_process_capture_success_writes_stdout(artifact_root: Path) -> None:
    stdout_path = artifact_root / "process" / "stdout.txt"
    stderr_path = artifact_root / "process" / "stderr.txt"
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "$result = Invoke-CapturedCommand -Name 'success' "
        + f"-FilePath {_ps_quote(sys.executable)} "
        + "-Arguments @('-c', 'print(\"captured stdout\")') "
        + f"-StdOutPath {_ps_quote(stdout_path)} -StdErrPath {_ps_quote(stderr_path)} "
        + f"-WorkingDirectory {_ps_quote(REPO_ROOT)}; "
        + "$result | ConvertTo-Json -Compress"
    )
    payload = json.loads(result.stdout)

    assert payload["exit_code"] == 0
    assert payload["success"] is True
    assert Path(payload["stdout"]) == stdout_path
    assert stdout_path.read_text(encoding="utf-8").strip() == "captured stdout"
    assert stderr_path.read_text(encoding="utf-8") == ""


def test_process_capture_failure_writes_stderr(artifact_root: Path) -> None:
    stdout_path = artifact_root / "failure" / "stdout.txt"
    stderr_path = artifact_root / "failure" / "stderr.txt"
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "$result = Invoke-CapturedCommand -Name 'failure' "
        + f"-FilePath {_ps_quote(sys.executable)} "
        + "-Arguments @('-c', 'import sys; print(\"captured stderr\", file=sys.stderr); sys.exit(7)') "
        + f"-StdOutPath {_ps_quote(stdout_path)} -StdErrPath {_ps_quote(stderr_path)} "
        + f"-WorkingDirectory {_ps_quote(REPO_ROOT)}; "
        + "$result | ConvertTo-Json -Compress"
    )
    payload = json.loads(result.stdout)

    assert payload["exit_code"] == 7
    assert payload["success"] is False
    assert Path(payload["stderr"]) == stderr_path
    assert stderr_path.read_text(encoding="utf-8").strip() == "captured stderr"


def test_text_privacy_redacts_sensitive_values_and_retains_firmware(
    artifact_root: Path,
) -> None:
    private_root = artifact_root / "private"
    private_root.mkdir()
    usb_resource = "USB0::0x0957::0x2C07::SYNTH123456::INSTR"
    tcpip_resource = "TCPIP0::synthetic-host.local::inst0::INSTR"
    text = (
        f"resource={usb_resource}\nserial=SYNTH123456\n"
        "Keysight Technologies,33521B,SYNTH123456,9.99\n"
        "firmware_revision=9.99\n"
        f"repo={REPO_ROOT}\nprivate={private_root}\n"
        "windows=C:\\Users\\synthetic\\evidence.log\n"
        "posix=/home/synthetic/evidence.log\n"
    )
    script = (
        _dot_source(HELPERS_PATH, PRIVACY_PATH)
        + f"$usb = Protect-ArtifactText -Text {_ps_quote(text)} "
        + f"-Resource {_ps_quote(usb_resource)} -RepoRoot {_ps_quote(REPO_ROOT)} "
        + f"-PrivateRoot {_ps_quote(private_root)} "
        + f"-SensitiveValues @(Get-DistinctiveSensitiveTokens -Resource {_ps_quote(usb_resource)}); "
        + f"$tcp = Protect-ArtifactText -Text {_ps_quote('host=synthetic-host.local address=192.168.44.9 resource=' + tcpip_resource)} "
        + f"-Resource {_ps_quote(tcpip_resource)} -RepoRoot {_ps_quote(REPO_ROOT)} "
        + f"-PrivateRoot {_ps_quote(private_root)} "
        + f"-SensitiveValues @(Get-DistinctiveSensitiveTokens -Resource {_ps_quote(tcpip_resource)}); "
        + "[ordered]@{ usb = $usb; tcp = $tcp } | ConvertTo-Json -Compress"
    )
    payload = json.loads(_run_powershell(script).stdout)
    combined = payload["usb"] + payload["tcp"]

    for sensitive in (
        usb_resource,
        "SYNTH123456",
        tcpip_resource,
        "synthetic-host.local",
        "192.168.44.9",
        str(REPO_ROOT),
        str(private_root),
        r"C:\Users\synthetic\evidence.log",
        "/home/synthetic/evidence.log",
    ):
        assert sensitive not in combined
    assert "Keysight Technologies,33521B,SYNTH123456,9.99" not in combined
    assert "firmware_revision=9.99" in payload["usb"]


def test_structured_json_is_sanitized_and_safe_metadata_is_retained(
    artifact_root: Path,
) -> None:
    private_root = artifact_root / "private"
    shareable_root = artifact_root / "shareable"
    private_root.mkdir()
    shareable_root.mkdir()
    resource = "USB0::0x0957::0x2C07::SYNTH876543::INSTR"
    private_payload = {
        "model_id": "keysight-33521b",
        "model": "33521B",
        "firmware_revision": "9.99",
        "connection": "usb",
        "backend": "ivi",
        "ordinary": "retained",
        "nested": {
            "resource": resource,
            "serial_number": "SYNTH876543",
            "raw_idn": "Keysight Technologies,33521B,SYNTH876543,9.99",
            "local_path": r"C:\Users\synthetic\raw.log",
        },
    }
    (private_root / "nested.json").write_text(
        json.dumps(private_payload), encoding="utf-8"
    )
    summary_path = private_root / "summary.md"
    summary_path.write_text(f"Firmware: 9.99\nResource: {resource}\n", encoding="utf-8")
    report_literal = json.dumps(private_payload).replace("'", "''")
    script = (
        _dot_source(HELPERS_PATH, PRIVACY_PATH)
        + f"$report = '{report_literal}' | ConvertFrom-Json; "
        + "$null = New-ShareableArtifactSet -PrivateReport $report "
        + f"-PrivateSummaryPath {_ps_quote(summary_path)} -RunRoot {_ps_quote(artifact_root)} "
        + f"-PrivateRoot {_ps_quote(private_root)} -ShareableRoot {_ps_quote(shareable_root)} "
        + f"-RepoRoot {_ps_quote(REPO_ROOT)} -Resource {_ps_quote(resource)}"
    )
    _run_powershell(script)

    report = json.loads((shareable_root / "report.json").read_text(encoding="utf-8"))
    nested = json.loads((shareable_root / "nested.json").read_text(encoding="utf-8"))
    assert report["model_id"] == "keysight-33521b"
    assert report["model"] == "33521B"
    assert report["firmware_revision"] == "9.99"
    assert report["connection"] == "usb"
    assert report["backend"] == "ivi"
    assert report["ordinary"] == "retained"
    assert report["artifact_visibility"] == "shareable"
    assert report["candidate_evidence_only"] is True
    assert report["promotes_live_support"] is False
    assert report["private_raw_artifacts_retained"] is True
    assert report["redaction_applied"] is True
    assert report["redaction_version"] == 1
    assert nested["nested"]["resource"] == "<redacted-resource>"
    assert nested["nested"]["serial_number"] == "<redacted>"
    assert nested["nested"]["raw_idn"] == "<redacted-idn>"
    assert nested["firmware_revision"] == "9.99"
    assert nested["model_id"] == "keysight-33521b"
    assert resource not in (shareable_root / "summary.md").read_text(encoding="utf-8")


def test_unknown_artifact_is_not_copied_to_shareable(artifact_root: Path) -> None:
    private_root = artifact_root / "private"
    shareable_root = artifact_root / "shareable"
    private_root.mkdir()
    shareable_root.mkdir()
    unknown = private_root / "raw.bin"
    unknown.write_bytes(b"synthetic private bytes")
    summary_path = private_root / "summary.md"
    summary_path.write_text("Synthetic summary", encoding="utf-8")
    script = (
        _dot_source(HELPERS_PATH, PRIVACY_PATH)
        + "$report = [ordered]@{ status = 'PASS' }; "
        + "$null = New-ShareableArtifactSet -PrivateReport $report "
        + f"-PrivateSummaryPath {_ps_quote(summary_path)} -RunRoot {_ps_quote(artifact_root)} "
        + f"-PrivateRoot {_ps_quote(private_root)} -ShareableRoot {_ps_quote(shareable_root)} "
        + f"-RepoRoot {_ps_quote(REPO_ROOT)}"
    )
    _run_powershell(script)

    assert unknown.exists()
    assert not (shareable_root / "raw.bin").exists()


def test_run_directory_layout_is_created_under_tmp_tests(artifact_root: Path) -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + f"New-ValidationRunDirectory -RepoRoot {_ps_quote(REPO_ROOT)} "
        + f"-BaseRoot {_ps_quote(artifact_root)} | ConvertTo-Json -Compress"
    )
    paths = json.loads(result.stdout)
    run_root = Path(paths["Root"])

    assert run_root.name.startswith("run_")
    assert run_root.parent == artifact_root
    assert Path(paths["Private"]).is_dir()
    assert Path(paths["Shareable"]).is_dir()
    assert REPO_ROOT / ".tmp_tests" in run_root.parents
