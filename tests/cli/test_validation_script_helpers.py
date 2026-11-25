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


def _run_powershell(script: str, *, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
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
        timeout=timeout,
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


def test_target_profiles_and_resolution_match_core_registry() -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH, PRIVACY_PATH)
        + "@{ loaded = 'loaded'; profiles = @($script:ValidationTargetProfiles); "
        + "known = (Resolve-ValidationTarget -Target 'keysight-33521b'); "
        + "all_targets = @(Resolve-ValidationTargets -Target 'all') "
        + "} | ConvertTo-Json -Depth 5 -Compress"
    )
    data = json.loads(result.stdout)
    assert data["loaded"] == "loaded"
    profiles = data["profiles"]
    expected_ids = registered_model_ids()

    assert tuple(profile["model_id"] for profile in profiles) == expected_ids
    assert {
        profile["model_id"]: profile["model"] for profile in profiles
    } == {
        model_id: model_info_for_model_id(model_id).canonical_model
        for model_id in expected_ids
    }
    assert data["known"] == {
        "model_id": "keysight-33521b",
        "model": "33521B",
    }
    assert [item["model_id"] for item in data["all_targets"]] == list(expected_ids)


def test_target_resolution_unknown_fails_closed() -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "Resolve-ValidationTarget -Target 'keysight-unknown' | Out-Null",
        check=False,
    )

    assert result.returncode != 0
    assert "Unsupported target" in result.stderr


def test_run_directory_layout_is_created_under_tmp_tests(artifact_root: Path) -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + f"Assert-PathUnderRoot -RootPath {_ps_quote(REPO_ROOT / '.tmp_tests')} "
        + f"-Path {_ps_quote(artifact_root / 'inside')}; "
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


def test_path_safety_rejects_outside() -> None:
    outside = REPO_ROOT / "outside-validation-artifacts"
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


def test_process_capture_success_and_failure(artifact_root: Path) -> None:
    success_stdout = artifact_root / "process" / "stdout.txt"
    success_stderr = artifact_root / "process" / "stderr.txt"
    failure_stdout = artifact_root / "failure" / "stdout.txt"
    failure_stderr = artifact_root / "failure" / "stderr.txt"
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "$success = Invoke-CapturedCommand -Name 'success' "
        + f"-FilePath {_ps_quote(sys.executable)} "
        + "-Arguments @('-c', 'print(\"captured stdout\")') "
        + f"-StdOutPath {_ps_quote(success_stdout)} -StdErrPath {_ps_quote(success_stderr)} "
        + f"-WorkingDirectory {_ps_quote(REPO_ROOT)}; "
        + "$failure = Invoke-CapturedCommand -Name 'failure' "
        + f"-FilePath {_ps_quote(sys.executable)} "
        + "-Arguments @('-c', 'import sys; print(\"captured stderr\", file=sys.stderr); sys.exit(7)') "
        + f"-StdOutPath {_ps_quote(failure_stdout)} -StdErrPath {_ps_quote(failure_stderr)} "
        + f"-WorkingDirectory {_ps_quote(REPO_ROOT)}; "
        + "@{ success = $success; failure = $failure } | ConvertTo-Json -Depth 5 -Compress"
    )
    payload = json.loads(result.stdout)
    success = payload["success"]
    failure = payload["failure"]

    assert success["exit_code"] == 0
    assert success["success"] is True
    assert Path(success["stdout"]) == success_stdout
    assert success_stdout.read_text(encoding="utf-8").strip() == "captured stdout"
    assert success_stderr.read_text(encoding="utf-8") == ""
    assert failure["exit_code"] == 7
    assert failure["success"] is False
    assert Path(failure["stderr"]) == failure_stderr
    assert failure_stderr.read_text(encoding="utf-8").strip() == "captured stderr"


@pytest.mark.parametrize("mode", ["process", "tree", "pipes"])
def test_process_capture_timeout_stays_bounded(artifact_root: Path, mode: str) -> None:
    pid_path = artifact_root / "pids.txt"
    stdout_path = artifact_root / "stdout.txt"
    stderr_path = artifact_root / "stderr.txt"
    command = "import os, subprocess, sys, time; from pathlib import Path; "
    if mode == "process":
        command += "pids = [os.getpid()]; "
    else:
        command += (
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
            "pids = [os.getpid(), child.pid]; "
        )
    command += f"Path({str(pid_path)!r}).write_text(' '.join(map(str, pids))); "
    if mode != "pipes":
        command += "time.sleep(10)"
    options = "-KillProcessTree " if mode == "tree" else "-CaptureInMemory "
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "try { $null = Invoke-CapturedCommand -Name 'stalled command' "
        + f"-FilePath {_ps_quote(sys.executable)} -Arguments @('-c', {_ps_quote(command)}) "
        + f"-StdOutPath {_ps_quote(stdout_path)} -StdErrPath {_ps_quote(stderr_path)} "
        + f"-TimeoutSeconds 2 {options}; throw 'Unexpected success'; "
        + "} catch [TimeoutException] { "
        + "$timeoutMessage = $_.Exception.Message; }; "
        + f"$pids = (Get-Content -LiteralPath {_ps_quote(pid_path)}) -split ' '; "
        + "if (Get-Process -Id ([int]$pids[0]) -ErrorAction SilentlyContinue) { throw 'Parent still running' }; "
        + ("if (Get-Process -Id ([int]$pids[1]) -ErrorAction SilentlyContinue) { throw 'Child still running' }; "
           if mode == "tree" else "")
        + "$timeoutMessage",
        timeout=9,
    )
    assert "stalled command timed out" in result.stdout
    if mode == "pipes":
        assert "waiting for stdout/stderr to close" in result.stdout
        assert "termination could not be confirmed" in result.stdout
    else:
        assert "termination could not be confirmed" not in result.stdout
    assert not stdout_path.exists()
    assert not stderr_path.exists()


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
        "channels": [1, 2],
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
    sensitive_marker = "SYNTH_PRIVATE_SECRET_123456"
    malformed = (
        '{"resource":"USB0::0x0957::0x2C07::'
        f'{sensitive_marker}::INSTR"'
    )
    broken_private_path = private_root / "broken.json"
    broken_private_path.write_text(malformed, encoding="utf-8")
    unknown = private_root / "raw.bin"
    unknown.write_bytes(b"synthetic private bytes")
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
    assert report["channels"] == [1, 2]
    assert all(type(channel) is int for channel in report["channels"])
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

    shareable_content = (shareable_root / "broken.json").read_text(encoding="utf-8")
    placeholder = json.loads(shareable_content)
    assert broken_private_path.exists()
    assert placeholder["artifact_available"] is False
    assert placeholder["parse_status"] == "failed"
    assert placeholder["private_raw_artifact_retained"] is True
    assert sensitive_marker not in shareable_content
    assert shareable_content != malformed
    assert unknown.exists()
    assert not (shareable_root / "raw.bin").exists()


def test_extra_detected_serial_is_removed_from_tcpip_shareable_artifacts(
    artifact_root: Path,
) -> None:
    private_root = artifact_root / "private"
    shareable_root = artifact_root / "shareable"
    identity_root = private_root / "identity"
    identity_root.mkdir(parents=True)
    shareable_root.mkdir()
    resource = "TCPIP0::synthetic-host.invalid::inst0::INSTR"
    serial = "SYNTH_SERIAL_987654"
    firmware = "9.99"
    private_payload = {
        "detected_identity": {
            "serial": serial,
            "firmware": firmware,
        }
    }
    (identity_root / "stdout.json").write_text(
        json.dumps(private_payload), encoding="utf-8"
    )
    (identity_root / "diagnostic.txt").write_text(
        f"serial={serial}\nfirmware={firmware}\n", encoding="utf-8"
    )
    summary_path = private_root / "summary.md"
    summary_path.write_text(
        f"Serial: {serial}\nFirmware: {firmware}\n", encoding="utf-8"
    )
    report_literal = json.dumps(private_payload).replace("'", "''")
    script = (
        _dot_source(HELPERS_PATH, PRIVACY_PATH)
        + f"$report = '{report_literal}' | ConvertFrom-Json; "
        + "$null = New-ShareableArtifactSet -PrivateReport $report "
        + f"-PrivateSummaryPath {_ps_quote(summary_path)} "
        + f"-RunRoot {_ps_quote(artifact_root)} "
        + f"-PrivateRoot {_ps_quote(private_root)} "
        + f"-ShareableRoot {_ps_quote(shareable_root)} "
        + f"-RepoRoot {_ps_quote(REPO_ROOT)} "
        + f"-Resource {_ps_quote(resource)} "
        + f"-ExtraSensitiveValues @({_ps_quote(serial)})"
    )

    _run_powershell(script)

    shareable_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in shareable_root.rglob("*")
        if path.is_file()
    )
    assert serial not in shareable_text
    assert firmware in shareable_text


def test_minimal_publication_removes_nested_private_arbitrary_fixtures(
    artifact_root: Path,
) -> None:
    # Synthetic CSV/JSON inputs must not make successful minimal publication fail.
    result = _run_powershell(
        _dot_source(HELPERS_PATH, PRIVACY_PATH)
        + f"$run = New-ValidationRunDirectory -RepoRoot {_ps_quote(REPO_ROOT)} "
        + f"-BaseRoot {_ps_quote(artifact_root)}; "
        + "$fixtures = Join-Path $run.Private 'fixtures'; "
        + "$null = New-Item -ItemType Directory -Force -Path $fixtures; "
        + "Write-Utf8NoBomText -LiteralPath (Join-Path $fixtures 'wave.csv') -Text '0.25'; "
        + "Write-Utf8NoBomText -LiteralPath (Join-Path $fixtures 'sequence.json') -Text '{}'; "
        + "$report = [pscustomobject]@{ status = 'PASS'; suite = 'full'; cases = @() }; "
        + f"$published = Publish-ValidationArtifacts -Report $report -Run $run -RepoRoot {_ps_quote(REPO_ROOT)} "
        + "-SummaryText 'Synthetic no-hardware validation'; "
        + "if (-not $published) { throw 'Nested fixtures blocked safe publication'; }; "
        + "@{ root = $run.Root; status = $report.status } | ConvertTo-Json -Compress"
    )
    payload = json.loads(result.stdout)
    run = Path(payload["root"])
    assert payload["status"] == "PASS"
    assert not (run / "private").exists()
    assert {item.name for item in (run / "shareable").iterdir()} == {
        "report.json", "summary.md",
    }
    report = json.loads((run / "shareable" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["private_raw_artifacts_retained"] is False
    assert report["redaction_applied"] is True


def test_publication_failure_removes_partial_evidence(artifact_root: Path) -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH, PRIVACY_PATH)
        + f"$run = New-ValidationRunDirectory -RepoRoot {_ps_quote(REPO_ROOT)} "
        + f"-BaseRoot {_ps_quote(artifact_root)}; "
        + "function New-ShareableArtifactSet { "
        + "Write-Utf8NoBomText -LiteralPath (Join-Path $run.Shareable 'report.json') -Text '{}'; "
        + "throw 'synthetic private detail'; }; "
        + "$report = [pscustomobject]@{status='PASS'}; "
        + "$published = Publish-ValidationArtifacts -Report $report -Run $run "
        + f"-RepoRoot {_ps_quote(REPO_ROOT)} "
        + "-SummaryText 'synthetic raw summary' -DebugArtifacts; "
        + "if ($published -or $report.status -ne 'FAIL') { throw 'Publication did not fail closed'; }"
    )
    assert "Safe report publication failed." in result.stderr
    files = [path for path in artifact_root.rglob("*") if path.is_file()]
    assert [path.name for path in files] == ["error-summary.md"]
    assert "synthetic" not in files[0].read_text(encoding="utf-8")


def test_full_mode_resolution_and_family_filtering() -> None:
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "$selected = @(Resolve-SelectedValidationModes -Suite full -Modes @('output,burst', 'static,sweep,modulation', 'burst') -Specified $true); "
        + "$recipes = @(Get-FullValidationInventory -Channels @(1,2) -Modes $selected); "
        + "@{ modes = $selected; recipes = $recipes } | ConvertTo-Json -Depth 12 -Compress"
    )
    data = json.loads(result.stdout)
    assert data["modes"] == ["static", "modulation", "burst", "sweep", "output"]
    assert {recipe["family"] for recipe in data["recipes"]} == {
        "inspection", "static", "modulation", "burst", "sweep", "output-config", "output-control",
        "sync-configuration", "sync-control",
    }
    steps = {recipe["name"]: recipe["steps"][0] for recipe in data["recipes"]}
    for waveform in ("square", "ramp", "triangle", "pulse"):
        assert steps[f"ch1/static-{waveform}"]["result"]["phase_deg"] != 0
    prbs_settings = [
        step["result"] for name, step in steps.items() if name.startswith("ch1/static-prbs")
    ]
    assert {settings["pattern"] for settings in prbs_settings} == {
        "PN7", "PN9", "PN11", "PN15", "PN20", "PN23",
    }
    assert any(result.get("edge_time_s") == 50e-9 for result in prbs_settings)
    for recipe, shape in {
        "am-square": "square", "fm-ramp": "up-ramp", "pm-triangle": "down-ramp",
        "pwm-pulse": "triangle", "sum-pulse": "square",
    }.items():
        assert steps[f"ch1/modulation-{recipe}"]["advanced_expected"]["settings"]["shape"] == shape
    for recipe, delay in {
        "burst-immediate": 0, "burst-square": 0.1,
        "sweep-square-logarithmic": 0, "sweep-ramp-logarithmic": 0.1,
    }.items():
        assert steps[f"ch1/{recipe}"]["advanced_expected"]["settings"]["trigger_delay_s"] == delay
    assert steps["ch1/sweep-square-logarithmic"]["result"]["duty_cycle_percent"] == 30
    assert steps["ch1/sweep-ramp-logarithmic"]["result"]["symmetry_percent"] == 30

    waveform_readback_recipes = {
        "ch1/static-square", "ch1/static-ramp", "ch1/static-triangle",
        "ch1/sweep-square-logarithmic", "ch1/sweep-ramp-logarithmic",
    } | {name for name in steps if name.startswith("ch1/static-prbs")}
    assert {
        name for name, step in steps.items() if "--verify-waveform-readback" in step["arguments"]
    } == waveform_readback_recipes
    assert {
        name for name, step in steps.items() if step["result"].get("waveform_readback_verified") is True
    } == waveform_readback_recipes

    output = next(recipe for recipe in data["recipes"] if recipe["name"] == "ch1/configure-output")
    assert any(step["readback"]["load"] == "1000" for step in output["steps"])
    sync_settings = {
        (step["result"]["source_channel"], step["result"]["sync_mode"], step["result"]["polarity"])
        for recipe in data["recipes"] if recipe["family"] == "sync-configuration"
        for step in recipe["steps"]
        if step["result"]["sync_state"] == "off"
    }
    assert sync_settings == {
        (1, "normal", "normal"), (1, "carrier", "normal"), (1, "normal", "inverted"),
        (2, "carrier", "inverted"), (2, "normal", "normal"),
    }


def test_full_mode_subsets_and_sequence_fixtures(artifact_root: Path) -> None:
    # Batch inventory-only checks in one PowerShell process. The runner tests
    # cover real CLI subprocess integration; no preflight replay is needed here.
    selections = {
        "static": ("ch1/static-sine", "ch1/burst-bus"),
        "modulation": ("ch1/modulation-am", "ch1/static-sine"),
        "burst": ("ch1/burst-counted-start-phase", "ch1/static-sine"),
        "sweep": ("ch1/sweep-external-positive", "ch1/list-bus-trigger"),
        "list-sweep": ("ch1/list-bus-trigger", "ch1/sweep-sine-linear"),
        "output": ("ch1/sync-control", "ch1/static-sine"),
        "burst,output": ("ch1/burst-bus", "ch1/static-sine"),
        "arbitrary": ("ch1/arbitrary-builtin-select", "ch1/sequence-bus"),
        "arbitrary,sequence": ("ch1/arbitrary-clear", "ch1/static-sine"),
        "software-sequence": ("ch1/software-sequence-basic", "ch1/sequence-bus"),
        "basic-workflows": ("ch1/amplitude-ramp", "ch1/software-sequence-basic"),
    }
    requested = ", ".join(_ps_quote(selection) for selection in selections)
    fixtures_dir = artifact_root / "fixtures"
    directory = _ps_quote(fixtures_dir)
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + f"$directory = {directory}; "
        + "$fixtures = New-ArbitraryValidationFixtures -Directory $directory; "
        + "function Get-InventorySummary($modes) { "
        + "  @(Get-FullValidationInventory -Channels @(1) -Modes $modes "
        + "    -ArbitraryFixtures $fixtures -SequenceFixtureDirectory $directory) "
        + "    | Select-Object name, family "
        + "}; "
        + "$all = @(Get-InventorySummary $script:FullValidationModes); "
        + "$subsets = @{}; "
        + f"foreach ($selection in @({requested})) {{ "
        + "  $modes = @(Resolve-SelectedValidationModes -Suite full -Modes @($selection) -Specified $true); "
        + "  $subsets[$selection] = @{ modes = $modes; recipes = @(Get-InventorySummary $modes) } "
        + "}; "
        + "$smoke = $false; $missing = $false; "
        + "try { @(Resolve-SelectedValidationModes -Suite smoke -Modes @('static') -Specified $true) | Out-Null } "
        + "catch { if ($_.Exception.Message -notlike '*-Modes requires -Suite full*') { throw }; $smoke = $true }; "
        + "try { @(Get-ApplicableValidationModes -Modes @('arbitrary') -Capabilities @{ max_arbitrary_sample_rate_hz = $null } -RejectUnsupportedArbitrary $true) | Out-Null } "
        + "catch { if ($_.Exception.Message -notlike '*does not support Arbitrary or Sequence validation*') { throw }; $missing = $true }; "
        + "@{ all = $all; subsets = $subsets; smoke_rejected = $smoke; "
        + "unsupported_rejected = $missing } | ConvertTo-Json -Depth 6 -Compress"
    )
    data = json.loads(result.stdout)
    assert data["smoke_rejected"] is True
    assert data["unsupported_rejected"] is True
    assert set(data["subsets"]) == set(selections)
    output_families = {"output-config", "output-control", "sync-configuration", "sync-control"}
    for selection, (expected, unexpected) in selections.items():
        modes = selection.split(",")
        subset = data["subsets"][selection]
        assert subset["modes"] == modes, selection
        allowed = {"inspection", *modes}
        if "output" in modes:
            allowed = (allowed - {"output"}) | output_families
        names = {recipe["name"] for recipe in subset["recipes"]}
        assert {recipe["family"] for recipe in subset["recipes"]} == allowed, selection
        assert names == {
            recipe["name"] for recipe in data["all"] if recipe["family"] in allowed
        }, selection
        assert len(names) == len(subset["recipes"]), selection
        assert expected in names and unexpected not in names, selection


    idle_samples = (fixtures_dir / "idle.csv").read_text(encoding="utf-8").splitlines()
    signal_samples = (fixtures_dir / "wave.csv").read_text(encoding="utf-8").splitlines()
    assert idle_samples == ["0"] * 16
    assert len(signal_samples) == 16
    assert any(float(sample) != 0 for sample in signal_samples)

    bus = json.loads((fixtures_dir / "sequence-bus-functional.json").read_text(encoding="utf-8"))
    external = json.loads((fixtures_dir / "sequence-external.json").read_text(encoding="utf-8"))
    negative = json.loads((fixtures_dir / "sequence-external-negative.json").read_text(encoding="utf-8"))
    for source, document in (("bus", bus), ("external", external)):
        assert document["trigger_source"] == source
        steps = document["steps"]
        assert [step["play_control"] for step in steps] == ["onceWaitTrig", "repeat"]
        assert steps[0]["waveform"] != steps[1]["waveform"]
        duration_s = len(signal_samples) * steps[1]["repeat_count"] / document["sample_rate_hz"]
        assert 1 <= duration_s <= 5
    assert external["trigger_slope"] == "positive"
    assert negative == {**external, "trigger_slope": "negative"}
    assert bus["steps"][0]["waveform"] != external["steps"][0]["waveform"]


def test_dual_channel_inventory_model_filter():
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "$m = @(Resolve-SelectedValidationModes -Suite full); "
        + "$dual = @(Get-FullValidationInventory -Channels @(1,2) -Modes dual-channel); "
        + "$single = @(Get-FullValidationInventory -Channels @(1) -Modes static); "
        + "$filtered = @(Get-ApplicableValidationModes -Modes $m -Capabilities @{channel_count=1;max_arbitrary_sample_rate_hz=250000000}); "
        + "@{ modes=$m; dual=$dual; single=$single; filtered=$filtered } | ConvertTo-Json -Depth 12 -Compress"
    )
    data = json.loads(result.stdout)
    assert len(data["modes"]) == 11 and data["modes"][-1] == "dual-channel"
    assert "dual-channel" not in data["filtered"]
    assert len([case for case in data["dual"] if case["family"] == "dual-channel"]) == 13
    assert all(case["steps"][0]["sequence_intent"]["channels"] == [1, 2]
               for case in data["dual"] if case["family"] == "dual-channel")
    phase = next(case for case in data["single"] if case["name"] == "ch1/static-phase-sync")
    assert phase["family"] == "static" and phase["required"] and phase["observation"]
    assert phase.get("dual_channel") is None
    step, = phase["steps"]
    assert step["arguments"][0] == "run-software-sequence"
    intent = step["sequence_intent"]
    assert intent["actions"] == [
        "configure-sine", "phase-sync", "output", "wait", "output", "readback-advanced",
    ]
    assert intent["output_on"] is True and intent["wait_seconds"] == 3
    assert {"index": 1, "path": ["phase_settings_preserved"], "value": True} in intent["joint_checks"]
    assert {"index": 4, "path": ["output_state"], "value": "off"} in intent["joint_checks"]



def test_joint_sequence_assertion_rejects_mismatched_nested_result():
    result = _run_powershell(
        _dot_source(HELPERS_PATH)
        + "$intent = [ordered]@{actions=@('dual-channel-status');output_on=$false;"
        + "upload_count=0;wait_seconds=0;channels=@(1,2);joint_checks=@("
        + "@{index=0;path=@('channel_status','2','frequency_hz');value=2000})}; "
        + "$payload = '{\"step_count\":1,\"loop_count\":1,\"execution_units\":1,"
        + "\"total_wait_seconds\":0,\"channels\":[1,2],\"executed\":true,"
        + "\"status\":\"completed\",\"completed_loops\":1,\"completed_step_executions\":1,"
        + "\"results\":[{\"action\":\"dual-channel-status\",\"channel_status\":{\"2\":{\"frequency_hz\":2000}}}],"
        + "\"cleanup\":{\"errors\":[],\"attempted_channels\":[],\"confirmed_off_channels\":[]}}' | ConvertFrom-Json; "
        + "Assert-SoftwareSequenceResult -Payload $payload -Intent $intent -Channel 1; "
        + "$payload.results[0].channel_status.'2'.frequency_hz = 1000; "
        + "try { Assert-SoftwareSequenceResult -Payload $payload -Intent $intent -Channel 1 } "
        + "catch { Write-Output 'rejected'; exit 0 }; exit 1"
    )
    assert result.stdout.strip() == "rejected"
