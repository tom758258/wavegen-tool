from __future__ import annotations

import json
import os
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


@pytest.fixture
def fake_git(monkeypatch):
    root = REPO_ROOT / ".tmp_tests" / f"fake_git_{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    head = "a" * 40

    def configure(*, dirty=False, head_exit=0, status_exit=0):
        (root / "git.cmd").write_text(
            '@echo off\nif "%~3"=="rev-parse" (\n'
            f'  echo {head}\n  exit /b {head_exit}\n)\n'
            'if "%~3"=="status" (\n'
            + ('  echo  M tracked-source.py\n' if dirty else '')
            + f'  exit /b {status_exit}\n)\nexit /b 1\n',
            encoding="utf-8",
        )
        return head

    configure()
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ["PATH"])
    try:
        yield configure
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
    assert report["suite"] == "smoke"
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
    assert report["suite"] == "smoke"
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


@pytest.mark.parametrize("script", [PREFLIGHT_SCRIPT, LIVE_SCRIPT])
def test_invalid_suite_fails_before_artifacts(script, artifact_root):
    arguments = ["-Target", "keysight-33521b", "-Suite", "unknown"]
    if script == LIVE_SCRIPT:
        arguments += ["-Connection", "usb", "-Resource", USB_RESOURCE, "-PlanOnly"]
    result = _run_script(script, artifact_root, *arguments)
    assert result.returncode != 0
    assert not list(artifact_root.iterdir())


def test_explicit_smoke(artifact_root, fake_git):
    fake_git(dirty=True)
    result = _run_script(
        LIVE_SCRIPT, artifact_root, "-Target", "keysight-33521b", "-Suite", "smoke",
        "-Connection", "usb", "-Resource", USB_RESOURCE, "-PlanOnly",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = _read_report(_only_run(artifact_root))
    assert report["suite"] == "smoke"
    assert "ch1/sine-config" in report["planned_cases"]
    assert "Output ON will not be used" in result.stdout


@pytest.mark.parametrize("model,channels", [
    ("33521b", [1]), ("33512b", [1, 2]), ("33510b", [1, 2]),
])
def test_full_plan_inventory_and_privacy(artifact_root, monkeypatch, fake_git, model, channels):
    # Any unexpected attempt to construct VISA fails the subprocess and leaves a marker.
    guard = artifact_root / "guard"
    guard.mkdir()
    marker = guard / "visa-attempt"
    (guard / "sitecustomize.py").write_text(
        "import pyvisa\nfrom pathlib import Path\n"
        "def forbidden(*args, **kwargs):\n"
        f"    Path({str(marker)!r}).touch()\n"
        "    raise AssertionError('VISA forbidden in PlanOnly')\n"
        "pyvisa.ResourceManager = forbidden\n", encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(guard))
    result = _run_script(
        LIVE_SCRIPT, artifact_root, "-Target", f"keysight-{model}", "-Suite", "full",
        "-Connection", "usb", "-Backend", "system", "-Resource", USB_RESOURCE, "-PlanOnly",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
    run = _only_run(artifact_root)
    report = _read_report(run)
    assert report["suite"] == "full"
    assert report["status"] == "PASS"
    assert report["git_head"] == "a" * 40
    assert report["git_clean"] is True
    assert report["expected_model"] == f"keysight-{model}"
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False
    assert report["channels_planned"] == channels
    assert report["channels_tested"] == []
    assert "Windows PowerShell" in report["reference_environment"]
    assert "Only dedicated output-control cases" in result.stdout
    inventory = report["full_inventory"]
    expected_primary = {
        "status",
        "static-sine", "static-square", "static-ramp", "static-triangle",
        "static-pulse", "static-dc", "static-noise", "static-prbs",
        "modulation-am", "modulation-am-dssc", "modulation-fm", "modulation-pm",
        "modulation-fsk", "modulation-bpsk", "modulation-pwm", "modulation-sum",
        "burst-immediate", "burst-bus", "burst-timer", "burst-external", "burst-gated",
        "sweep-sine-linear", "sweep-sine-logarithmic", "sweep-sine-bus", "sweep-sine-timer",
        "sweep-square-linear", "sweep-ramp-linear", "sweep-triangle-linear",
        "list-transition-cw-recovery", "list-bus-trigger", "configure-output", "output-control",
    }
    expected_parity = {
        "status", "static-sine", "modulation-am", "burst-immediate",
        "sweep-sine-linear", "list-transition-cw-recovery", "configure-output", "output-control",
    }
    primary = [case for case in inventory if case["channel"] == 1]
    secondary = [case for case in inventory if case["channel"] == 2]
    assert {case["name"] for case in primary} == {f"ch1/{name}" for name in expected_primary}
    assert len(primary) == 33
    assert {case["name"] for case in secondary} == (
        {f"ch2/{name}" for name in expected_parity} if 2 in channels else set()
    )
    assert len(secondary) == (8 if 2 in channels else 0)
    assert len(report["planned_cases"]) == (48 if 2 in channels else 38)
    pulse = next(case for case in primary if case["name"] == "ch1/static-pulse")["steps"][0]["arguments"]
    assert pulse[pulse.index("--leading-edge-s") + 1] == "0.00000004"
    assert pulse[pulse.index("--trailing-edge-s") + 1] == "0.00000006"
    assert "--edge-time-s" not in pulse
    pwm = next(case for case in primary if case["name"] == "ch1/modulation-pwm")["steps"][0]["arguments"]
    assert pwm[pwm.index("--edge-time-s") + 1] == "0.00000005"
    assert "--leading-edge-s" not in pwm and "--trailing-edge-s" not in pwm
    assert {case["channel"] for case in inventory} == set(channels)
    assert all(case["required"] for case in inventory)
    assert {case["name"] for case in inventory} <= set(report["planned_cases"])
    transition = next(case for case in inventory if case["name"] == "ch1/list-transition-cw-recovery")
    assert [step["arguments"][0] for step in transition["steps"]] == [
        "configure-sine-list-sweep", "configure-square-list-sweep",
        "configure-ramp-list-sweep", "configure-triangle-list-sweep", "configure-sine",
    ]
    for case in inventory:
        for step in case["steps"]:
            if step["arguments"][:3] == ["output", "--state", "on"]:
                assert case["family"] == "output-control"
    text = "\n".join(path.read_text(encoding="utf-8") for path in (run / "shareable").rglob("*") if path.is_file())
    for private in (USB_RESOURCE, "SYNTHETIC123", str(REPO_ROOT), str(run)):
        assert private not in text
    assert USB_RESOURCE not in result.stdout
    assert _read_report(run, "shareable")["promotes_live_support"] is False
    preflight = next((artifact_root / "_preflight").glob("run_*/private/report.json"))
    readiness = json.loads(preflight.read_text(encoding="utf-8"))
    assert readiness["suite"] == "full" and readiness["status"] == "PASS"
    assert readiness["hardware_touched"] is False
    assert readiness["visa_io_performed"] is False
    assert readiness["git_head"] == report["git_head"]
    assert readiness["git_clean"] is True


@pytest.mark.parametrize("connection,backend,resource", [
    ("tcpip", "system", "TCPIP0::synthetic.invalid::inst0::INSTR"),
    ("usb", "@py", USB_RESOURCE),
])
def test_full_reference_environment_fails_closed(artifact_root, connection, backend, resource):
    result = _run_script(
        LIVE_SCRIPT, artifact_root, "-Target", "keysight-33521b", "-Suite", "full",
        "-Connection", connection, "-Backend", backend, "-Resource", resource, "-PlanOnly",
    )
    assert result.returncode == 2
    assert "Full requires" in result.stderr
    assert not list(artifact_root.iterdir())


@pytest.mark.parametrize("script", [PREFLIGHT_SCRIPT, LIVE_SCRIPT])
@pytest.mark.parametrize("failure", ["dirty", "head-unavailable", "status-failed"])
def test_full_git_provenance_rejects_before_artifacts(artifact_root, fake_git, script, failure):
    fake_git(
        dirty=failure == "dirty",
        head_exit=1 if failure == "head-unavailable" else 0,
        status_exit=1 if failure == "status-failed" else 0,
    )
    arguments = ["-Target", "keysight-33521b", "-Suite", "full"]
    if script == LIVE_SCRIPT:
        # Omit PlanOnly: rejection must also happen before interactive/live execution.
        arguments += ["-Connection", "usb", "-Backend", "system", "-Resource", USB_RESOURCE]
    result = _run_script(script, artifact_root, *arguments)
    assert result.returncode == 2
    assert "Full requires" in result.stderr and "Git" in result.stderr
    assert not list(artifact_root.iterdir())
    assert "Type YES" not in result.stdout


@pytest.mark.parametrize("suite", ["full", "smoke"])
@pytest.mark.parametrize("environment", ["non-windows", "core"])
def test_preflight_reference_environment_is_full_only(artifact_root, monkeypatch, suite, environment):
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is unavailable")
    if environment == "non-windows":
        monkeypatch.setenv("OS", "SyntheticNonWindows")
        result = _run_script(PREFLIGHT_SCRIPT, artifact_root, "-Target", "keysight-33521b", "-Suite", suite)
    else:
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"

        command = "$PSVersionTable.PSEdition = 'Core'; & " + quote(PREFLIGHT_SCRIPT)
        command += f" -Target keysight-33521b -Suite {suite} -Python " + quote(sys.executable)
        command += " -OutputRoot " + quote(artifact_root) + "; exit $LASTEXITCODE"
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
    if suite == "full":
        assert result.returncode == 2
        assert "Full requires Windows PowerShell Desktop on Windows" in result.stderr
        assert not list(artifact_root.iterdir())
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert _read_report(_only_run(artifact_root))["status"] == "PASS"


def _run_powershell_test(artifact_root, body):
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is unavailable")
    harness = artifact_root / "harness.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        ". ./scripts/_validation_helpers.ps1\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        "(Join-Path (Get-Location) 'scripts/live-cli-check.ps1'), [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { throw 'Parse errors' }\n"
        "foreach ($node in $ast.EndBlock.Statements) {\n"
        "  if ($node -is [System.Management.Automation.Language.FunctionDefinitionAst]) {\n"
        "    . ([scriptblock]::Create($node.Extent.Text))\n"
        "  }\n}\n" + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.split("RESULT:", 1)[1])


def test_full_git_revision_must_remain_unchanged(artifact_root, fake_git):
    result = _run_powershell_test(artifact_root, r'''
$head = Assert-FullGitRevision -ProjectRoot (Get-Location).Path -ExpectedHead ('a' * 40)
$changedRejected = $false
try { $null = Assert-FullGitRevision -ProjectRoot (Get-Location).Path -ExpectedHead ('b' * 40) } catch {
    $changedRejected = $_.Exception.Message -like '*HEAD*unchanged*'
}
Write-Output ('RESULT:' + (@{ head = $head; changed_rejected = $changedRejected } | ConvertTo-Json -Compress))
''')
    assert result == {"head": "a" * 40, "changed_rejected": True}


@pytest.mark.parametrize("failure", ["none", "command", "cleanup", "case-dirty", "dirty", "limit", "na"])
def test_full_failure_gate_and_best_effort_off(artifact_root, failure):
    result = _run_powershell_test(artifact_root, r'''
$failure = 'FAILURE'
$Suite = 'full'
$cases = [System.Collections.ArrayList]::new()
$channelsTested = [System.Collections.ArrayList]::new()
$targetCapabilityModel = '33512B'
$liveCommon = @('--json')
$run = [pscustomobject]@{ Private = (Get-Location).Path }
$script:state = 'off'
$script:offCalls = 0
$script:onCalls = 0
$script:onlyOutput = @(Get-FullValidationInventory -Channels @(2) | Where-Object { $_.family -eq 'output-control' })
function Get-FullValidationInventory { param($Channels); return $script:onlyOutput }
function Invoke-WavegenJson {
    param($Name, $CliArguments, $ArtifactDirectory)
    $command = $CliArguments[0]
    if ($command -ne 'read-errors' -and '--channel' -in $CliArguments) {
        $index = [array]::IndexOf($CliArguments, '--channel')
        if ($CliArguments[$index + 1] -ne '2') { throw 'Wrong channel used!' }
    }
    if ($command -eq 'output') {
        $script:state = $CliArguments[2]
        if ($script:state -eq 'off') { $script:offCalls++ } else { $script:onCalls++ }
        if ($failure -eq 'command' -and $script:state -eq 'on') { throw 'Injected failure after enabling output' }
        if ($failure -eq 'cleanup' -and $Name -like '*cleanup-command') { throw 'Injected cleanup failure' }
    }
    $payload = [pscustomobject]@{
        success = $true; action = $command; model = '33512B'; channel = 2
        output_state = $script:state; function = 'SIN'; frequency_hz = 1000
        amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50'
        has_errors = (($failure -eq 'dirty' -and $Name -eq 'final-error-queue') -or ($failure -eq 'case-dirty' -and $Name -like '*step2-errors'))
        empty_confirmed = -not ($failure -eq 'limit' -and $Name -eq 'final-error-queue')
        limit_reached = ($failure -eq 'limit' -and $Name -eq 'final-error-queue')
    }
    return [pscustomobject]@{ Process = @{ exit_code = 0 }; ParseError = $null; Payload = $payload }
}
Invoke-FullLiveCases -Channels @(2) -CanRun ($failure -ne 'na')
$finalReasons = @()
try { Assert-FullErrorQueue -Name 'final-error-queue' } catch { $finalReasons += $_.Exception.Message }
Add-LiveCase -Cases $cases -Name 'final-error-queue' -Status $(if ($finalReasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $finalReasons
$pass = Test-FullValidationPass -Cases @($cases) -RequiredNames @('ch2/initial-safe-off', 'ch2/output-control', 'ch2/final-safe-off', 'final-error-queue')
Write-Output ('RESULT:' + (@{ pass = $pass; state = $script:state; off_calls = $script:offCalls; on_calls = $script:onCalls; cases = @($cases) } | ConvertTo-Json -Depth 10 -Compress))
'''.replace("FAILURE", failure))
    assert result["pass"] is (failure == "none")
    assert result["state"] == "off"
    assert result["off_calls"] >= 2
    assert result["on_calls"] == (0 if failure == "na" else 1)
    statuses = {case["name"]: case["status"] for case in result["cases"]}
    if failure in {"command", "cleanup", "case-dirty"}:
        assert statuses["ch2/output-control"] == "FAIL"
    if failure == "na":
        assert statuses["ch2/output-control"] == "N/A"
    if failure in {"dirty", "limit"}:
        assert statuses["final-error-queue"] == "FAIL"


def test_trigger_validation_guard_keeps_pending_model_out_of_product(monkeypatch, capsys):
    from wavegen_tool_cli.cli import main
    from wavegen_tool_core import visa
    from wavegen_tool_core.simulator import Simulated33521BState, SimulatedResourceManager

    writes = []
    def factory(backend):
        manager = SimulatedResourceManager(Simulated33521BState(model_id="keysight-33510b"))
        original_open = manager.open_resource
        def open_resource(resource):
            assert resource == USB_RESOURCE
            session = original_open(manager.state.resource_name)
            original_write = session.write
            def write(command):
                writes.append(command)
                return original_write(command)
            session.write = write
            return session
        manager.open_resource = open_resource
        return manager
    monkeypatch.setattr(visa, "create_resource_manager", factory)
    common = ["trigger", "--resource", USB_RESOURCE, "--json"]
    assert main(common) != 0
    assert writes == []
    capsys.readouterr()
    assert main(common + ["--validation-allow-pending-live-support", "--model", "keysight-33512b"]) != 0
    assert writes == []
    capsys.readouterr()
    assert main(common + ["--validation-allow-pending-live-support", "--model", "keysight-33510b"]) == 0
    assert writes == ["*TRG"]
    assert json.loads(capsys.readouterr().out)["model"] == "33510B"


def test_full_readback_accepts_instrument_function_spellings(artifact_root):
    result = _run_powershell_test(artifact_root, r'''
foreach ($pair in @(@('SIN', 'SINUSOID'), @('SQU', 'SQUARE'), @('PULS', 'PULSE'), @('NOIS', 'NOISE'), @('TRI', 'TRIANGLE'))) {
    foreach ($spelling in $pair) {
        Assert-FullReadback -Payload ([pscustomobject]@{ function = $spelling }) -Expected @{ function = $pair[0] }
    }
}
$rejected = $false
try { Assert-FullReadback -Payload ([pscustomobject]@{ function = 'RAMP' }) -Expected @{ function = 'SQU' } } catch { $rejected = $true }
Write-Output ('RESULT:' + (@{ rejected = $rejected } | ConvertTo-Json -Compress))
''')
    assert result['rejected'] is True


@pytest.mark.parametrize("model,accepted", [("keysight-33512b", True), ("keysight-33521b", False)])
def test_full_output_config_channel_uses_detected_capabilities(monkeypatch, capsys, model, accepted):
    from wavegen_tool_cli.cli import main
    from wavegen_tool_core import visa
    from wavegen_tool_core.simulator import Simulated33521BState, SimulatedResourceManager

    manager = SimulatedResourceManager(Simulated33521BState(model_id=model))
    original_open = manager.open_resource
    manager.open_resource = lambda resource: original_open(manager.state.resource_name)
    monkeypatch.setattr(visa, "create_resource_manager", lambda backend: manager)
    result = main([
        "configure-output", "--resource", USB_RESOURCE, "--channel", "2",
        "--load", "50", "--json", "--validation-allow-pending-live-support", "--model", model,
    ])
    payload = json.loads(capsys.readouterr().out)
    assert (result == 0) is accepted
    assert payload["success"] is accepted
    if accepted:
        assert payload["channel"] == 2 and payload["model"] == "33512B"
