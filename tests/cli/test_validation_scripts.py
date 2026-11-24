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
USB_SERIAL = "SYNTHETIC123"
USB_RESOURCE = f"USB0::0x0957::0x0000::{USB_SERIAL}::INSTR"
TCPIP_HOST = "synthetic.invalid"
TCPIP_RESOURCE = f"TCPIP0::{TCPIP_HOST}::inst0::INSTR"


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
        timeout=180,
    )


def _only_run(artifact_root: Path) -> Path:
    runs = sorted(path for path in artifact_root.iterdir() if path.name.startswith("run_"))
    assert len(runs) == 1
    return runs[0]


def _read_report(run_root: Path, visibility: str = "shareable") -> dict[str, object]:
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
    assert shareable["private_raw_artifacts_retained"] is False
    assert not (run_root / "private").exists()
    assert {file.name for file in (run_root / "shareable").iterdir()} == {"report.json", "summary.md"}


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
        stdin="\n",
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
    assert "Shareable report:" in result.stdout
    assert "Shareable summary:" in result.stdout
    run_root = _only_run(artifact_root)
    report = _read_report(run_root)
    assert report["status"] == "CANCELLED"
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False
    assert (run_root / "shareable" / "report.json").is_file()
    assert str(run_root / "shareable" / "report.json") in result.stdout
    assert str(run_root / "shareable" / "summary.md") in result.stdout
    assert not (run_root / "private").exists()


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
                "-Suite",
                "full",
                "-Connection",
                "usb",
                "-Resource",
                TCPIP_RESOURCE,
                "-PlanOnly",
            ],
            "does not match",
        ),
        (
            [
                "-Target",
                "keysight-33512b",
                "-Suite",
                "full",
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



def _preflight_diagnostics(artifact_root: Path) -> str:
    # Only sanitized, shareable failure reasons enter CI assertion output.
    reports = sorted((artifact_root / "_preflight").glob("run_*/shareable/report.json"))
    if not reports:
        return " (no preflight shareable report)"
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    failures = [
        {"name": case["name"], "failure_reasons": case.get("failure_reasons", [])}
        for case in report["cases"] if case["status"] != "PASS"
    ]
    return " preflight failures=" + json.dumps(failures[:16], ensure_ascii=False)


@pytest.mark.parametrize("model,channels,connection,resource,private_token", [
    ("33521b", [1], "tcpip", TCPIP_RESOURCE, TCPIP_HOST),
    ("33512b", [1, 2], "usb", USB_RESOURCE, USB_SERIAL),
    ("33510b", [1, 2], "usb", USB_RESOURCE, USB_SERIAL),
])
def test_full_plan_inventory_and_privacy(
    artifact_root, monkeypatch, fake_git, model, channels, connection, resource, private_token,
):
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
        "-Connection", connection, "-Backend", "system", "-Resource", resource, "-PlanOnly",
    )
    assert result.returncode == 0, result.stdout + result.stderr + _preflight_diagnostics(artifact_root)
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
    assert report["connection"] == connection
    assert report["backend"] == "system"
    assert report["resource"] == "<redacted-resource>"
    assert "Windows PowerShell" in report["reference_environment"]
    assert f"explicit {connection.upper()} resource" in report["reference_environment"]
    assert "system VISA" in report["reference_environment"]
    inventory = report["full_inventory"]
    expected_primary = {
        "status",
        "static-sine", "static-square", "static-ramp", "static-triangle",
        "static-pulse", "static-dc", "static-noise", "static-prbs",
        "static-prbs-pn7", "static-prbs-pn11", "static-prbs-pn15",
        "static-prbs-pn20", "static-prbs-pn23",
        "modulation-am", "modulation-am-dssc", "modulation-fm", "modulation-pm",
        "modulation-fsk", "modulation-bpsk", "modulation-pwm", "modulation-sum",
        "modulation-am-square", "modulation-fm-ramp", "modulation-pm-triangle",
        "modulation-fsk-square", "modulation-bpsk-triangle", "modulation-pwm-pulse",
        "modulation-sum-pulse",
        "burst-immediate", "burst-bus", "burst-timer", "burst-external", "burst-gated",
        "burst-external-negative", "burst-gated-inverted",
        "burst-counted-start-phase", "burst-gated-start-phase",
        "burst-square", "burst-ramp", "burst-triangle", "burst-prbs",
        "sweep-sine-linear", "sweep-sine-logarithmic", "sweep-sine-bus", "sweep-sine-timer",
        "sweep-square-linear", "sweep-ramp-linear", "sweep-triangle-linear",
        "sweep-square-logarithmic", "sweep-ramp-logarithmic", "sweep-triangle-logarithmic",
        "sweep-external-positive", "sweep-external-negative",
        "list-transition-cw-recovery", "list-bus-trigger", "configure-output", "output-control",
        "sync-configuration", "sync-control",
        "software-sequence-basic", "software-sequence-advanced",
        "timed-output", "amplitude-ramp", "output-cycle", "waveform-sequence",
        "frequency-step", "quick-output-preset",
        "amplitude-ramp-descending", "frequency-step-descending",
        "quick-output-preset-sine", "quick-output-preset-triangle",
    }
    expected_parity = {
        "status", "static-sine", "modulation-am", "burst-immediate",
        "sweep-sine-linear", "list-transition-cw-recovery", "configure-output", "output-control",
        "sync-configuration", "software-sequence-advanced",
    }
    if model != "33510b":
        expected_primary |= {
            "arbitrary-upload-configure", "arbitrary-clear", "arbitrary-builtin-select",
            "sequence-bus", "sequence-bus-functional", "sequence-external",
            "sequence-external-negative", "software-sequence-arbitrary",
        }
        expected_primary |= {
            f"arbitrary-builtin-{name}" for name in (
                "cardiac", "exp-rise", "exp-fall", "gaussian", "haversine",
                "lorentz", "d-lorentz", "neg-ramp",
            )
        }
        if model == "33512b":
            expected_parity |= {"arbitrary-upload-configure", "sequence-bus"}
    primary = [case for case in inventory if case["channel"] == 1]
    secondary = [case for case in inventory if case["channel"] == 2]
    assert {case["name"] for case in primary} == {f"ch1/{name}" for name in expected_primary}
    assert {case["name"] for case in secondary} == (
        {f"ch2/{name}" for name in expected_parity} if 2 in channels else set()
    )
    assert len(report["planned_cases"]) == len(inventory) + 5 + 2 * len(channels)
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
    assert "readback-advanced" not in {case["name"] for case in inventory}
    for case in inventory:
        for step in case["steps"]:
            if "--verify-readback" in step["arguments"]:
                assert step["advanced_expected"]["advanced_mode"] != "CW"
    assert next(
        case for case in inventory
        if case["name"] == "ch1/list-transition-cw-recovery"
    )["steps"][-1]["advanced_expected"]["advanced_mode"] == "CW"
    transition = next(case for case in inventory if case["name"] == "ch1/list-transition-cw-recovery")
    assert [step["arguments"][0] for step in transition["steps"]] == [
        "configure-sine-list-sweep", "configure-square-list-sweep",
        "configure-ramp-list-sweep", "configure-triangle-list-sweep", "configure-sine",
    ]
    list_bus = next(case for case in primary if case["name"] == "ch1/list-bus-trigger")
    list_bus_args = list_bus["steps"][0]["arguments"]
    assert list_bus_args[list_bus_args.index("--frequencies-hz") + 1] == "2,5,8"
    assert sum(step["arguments"][0] == "trigger" for step in list_bus["steps"]) == 3
    sweep_names = {
        "sweep-sine-linear", "sweep-sine-logarithmic", "sweep-sine-bus",
        "sweep-sine-timer", "sweep-square-linear", "sweep-ramp-linear",
        "sweep-triangle-linear", "sweep-external-positive", "sweep-external-negative",
        "sweep-square-logarithmic", "sweep-ramp-logarithmic", "sweep-triangle-logarithmic",
    }
    for case in primary:
        if case["name"].split("/", 1)[1] in sweep_names:
            args = case["steps"][0]["arguments"]
            assert args[args.index("--start-frequency-hz") + 1] == "2"
            assert args[args.index("--stop-frequency-hz") + 1] == "8"
    for case in inventory:
        for step in case["steps"]:
            if step["arguments"][:3] == ["output", "--state", "on"]:
                assert case["family"] == "output-control" or case["observation"]
    observed = {case["name"] for case in primary if case["observation"]}
    assert observed == {
        "ch1/burst-bus", "ch1/burst-timer", "ch1/burst-external", "ch1/burst-external-negative",
        "ch1/burst-gated", "ch1/burst-gated-inverted", "ch1/sweep-sine-bus",
        "ch1/sweep-sine-timer", "ch1/sweep-external-positive",
        "ch1/sweep-external-negative", "ch1/list-bus-trigger", "ch1/software-sequence-basic",
        "ch1/timed-output", "ch1/amplitude-ramp", "ch1/output-cycle",
        "ch1/waveform-sequence", "ch1/frequency-step", "ch1/amplitude-ramp-descending",
        "ch1/frequency-step-descending",
    } | ({
        "ch1/sequence-bus-functional", "ch1/sequence-external", "ch1/sequence-external-negative",
    } if model != "33510b" else set())
    assert observed.isdisjoint(case["name"] for case in report["cases"])
    text = "\n".join(path.read_text(encoding="utf-8") for path in (run / "shareable").rglob("*") if path.is_file())
    for private in (resource, private_token, str(REPO_ROOT), str(run)):
        assert private not in text
    assert resource not in result.stdout
    assert private_token not in result.stdout
    assert _read_report(run, "shareable")["promotes_live_support"] is False
    preflight = next((artifact_root / "_preflight").glob("run_*/shareable/report.json"))
    readiness = json.loads(preflight.read_text(encoding="utf-8"))
    assert readiness["suite"] == "full" and readiness["status"] == "PASS"
    assert readiness["hardware_touched"] is False
    assert readiness["visa_io_performed"] is False
    assert readiness["git_head"] == report["git_head"]
    assert readiness["git_clean"] is True
    assert report["complete_mode_selection"] is True
    assert readiness["complete_mode_selection"] is True
    assert readiness["selected_modes"] == report["selected_modes"]
    if model == "33521b":
        _assert_full_recipe_details(report, readiness)


def _assert_full_recipe_details(report, readiness):
    # Reuse the complete single-channel run for recipe-level assertions. Subset
    # selection itself is covered by the inventory helper and representative runners.
    recipes = {recipe["name"]: recipe for recipe in report["full_inventory"]}
    for name, phase in (
        ("ch1/burst-immediate", 0),
        ("ch1/burst-counted-start-phase", 90),
        ("ch1/burst-gated-start-phase", 45.1),
    ):
        recipe = recipes[name]
        configure, = recipe["steps"]
        assert recipe["observation"] is None
        assert configure["result"]["burst_start_phase_deg"] == phase
        assert configure["advanced_expected"]["settings"]["phase_deg"] == phase
        assert "--verify-readback" in configure["arguments"]

    cases = {case["name"].split("/", 1)[1]: case["status"] for case in readiness["cases"]}
    assert cases["ch1/burst-invalid-start-phase-dry-run"] == "PASS"
    for name in ("ch1/burst-counted-start-phase", "ch1/burst-gated-start-phase"):
        assert cases[f"{name}/step1-dry-run"] == "PASS"
        assert cases[f"{name}/step1-simulate"] == "PASS"

    assert {"sync/initial-safe-off", "sync/final-safe-off"} <= set(report["planned_cases"])
    control = next(x for x in report["full_inventory"] if x["name"] == "ch1/sync-control")
    assert [step["arguments"] for step in control["steps"]] == [
        ["sync-output", "--state", "on"], ["sync-output", "--state", "off"],
    ]

    sync_stages = {
        case["name"].split("/", 1)[1]: case["status"]
        for case in readiness["cases"] if "/ch1/sync-" in case["name"]
    }
    assert sync_stages == {
        **{
            f"ch1/sync-configuration/step{step}-{execution}": "PASS"
            for step in (1, 2, 3) for execution in ("dry-run", "simulate")
        },
        "ch1/sync-control/step1-simulate": "PASS",
        "ch1/sync-control/step2-simulate": "PASS",
    }

    for slope, idle in (("positive", "low"), ("negative", "high")):
        name = f"ch1/sweep-external-{slope}"
        recipe = next(case for case in report["full_inventory"] if case["name"] == name)
        configure, output = recipe["steps"]
        assert recipe["required"] is True
        assert recipe["observation"]["input"] == "external-trigger"
        assert recipe["observation"]["idle"] == idle
        assert configure["arguments"][0] == "configure-sine-sweep"
        assert configure["advanced_expected"]["settings"]["trigger_slope"] == slope
        assert output["arguments"] == ["output", "--state", "on"]
        assert {
            case["name"].split("/", 1)[1]: case["status"]
            for case in readiness["cases"] if f"/{name}/" in case["name"]
        } == {
            f"{name}/step1-dry-run": "PASS",
            f"{name}/step1-simulate": "PASS",
            f"{name}/step2-simulate": "PASS",
        }

    recipes = {
        recipe["name"]: recipe for recipe in report["full_inventory"]
        if recipe["family"] == "software-sequence"
    }
    assert set(recipes) == {
        "ch1/software-sequence-basic", "ch1/software-sequence-advanced",
        "ch1/software-sequence-arbitrary",
    }
    for recipe in recipes.values():
        step, = recipe["steps"]
        assert step["arguments"][:2] == ["run-software-sequence", "--file"]
    basic, = recipes["ch1/software-sequence-basic"]["steps"]
    assert basic["sequence_intent"]["output_on"] is True
    assert recipes["ch1/software-sequence-basic"]["observation"]
    arbitrary, = recipes["ch1/software-sequence-arbitrary"]["steps"]
    assert arbitrary["sequence_intent"]["upload_count"] == 1
    assert arbitrary["sequence_intent"]["output_on"] is False

    cases = {case["name"]: case["status"] for case in readiness["cases"]}
    for name in recipes:
        for execution_mode in ("dry-run", "simulate"):
            assert cases[f"keysight-33521b/{name}/step1-{execution_mode}"] == "PASS"

    negative = next(
        case for case in report["full_inventory"] if case["name"] == "ch1/sequence-external-negative"
    )
    assert negative["observation"]["input"] == "external-trigger"
    assert negative["observation"]["idle"] == "high"
    assert negative["steps"][2]["result"]["trigger_slope"] == "negative"
    clear = next(x for x in report["full_inventory"] if x["name"] == "ch1/arbitrary-clear")
    assert any(step["arguments"][0] == "clear-arbitrary-memory" and
               "--confirm-clear" in step["arguments"] for step in clear["steps"])



def test_smoke_rejects_modes_before_artifacts(artifact_root):
    for script in (PREFLIGHT_SCRIPT, LIVE_SCRIPT):
        args = ["-Target", "keysight-33521b", "-Modes", "static"]
        if script == LIVE_SCRIPT:
            args += ["-Connection", "usb", "-Resource", USB_RESOURCE, "-PlanOnly"]
        result = _run_script(script, artifact_root, *args)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "-Modes requires -Suite full" in result.stderr
        assert not list(artifact_root.iterdir())


def test_full_preflight_static_direct_without_hardware(
    artifact_root: Path, fake_git,
) -> None:
    result = _run_script(
        PREFLIGHT_SCRIPT, artifact_root, "-Target", "keysight-33521b",
        "-Suite", "full", "-Modes", "static",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = _read_report(_only_run(artifact_root))
    assert report["status"] == "PASS"
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False
    assert report["selected_modes"] in ("static", ["static"])
    assert report["complete_mode_selection"] is False
    cases = {case["name"]: case["status"] for case in report["cases"]}
    for mode in ("dry-run", "simulate"):
        assert cases[f"keysight-33521b/ch1/static-sine/step1-{mode}"] == "PASS"
    assert not any("/ch1/arbitrary-" in name for name in cases)


@pytest.mark.parametrize("mode,expected,unexpected", [
    ("burst,output", "ch1/burst-bus", "ch1/static-sine"),
    ("arbitrary", "ch1/arbitrary-builtin-select", "ch1/sequence-bus"),
    ("basic-workflows", "ch1/amplitude-ramp", "ch1/software-sequence-basic"),
])
def test_full_plan_filters_modes_and_propagates_to_preflight(
    artifact_root, fake_git, mode, expected, unexpected,
):
    extra_args = ("-DebugArtifacts",) if mode == "arbitrary" else ()
    result = _run_script(
        LIVE_SCRIPT, artifact_root, "-Target", "keysight-33521b",
        "-Suite", "full", "-Modes", mode, "-Connection", "usb",
        "-Backend", "system", "-Resource", USB_RESOURCE, "-PlanOnly", *extra_args,
    )
    assert result.returncode == 0, result.stdout + result.stderr + _preflight_diagnostics(artifact_root)
    report = _read_report(_only_run(artifact_root))
    assert expected in report["planned_cases"]
    assert unexpected not in report["planned_cases"]
    assert report["complete_mode_selection"] is False
    assert report["selected_modes"] in (mode, mode.split(","))
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False
    if mode == "burst,output":
        assert {
            "ch1/burst-counted-start-phase", "ch1/burst-gated-start-phase",
            "ch1/sync-control", "ch1/sync-configuration", "ch1/output-control",
            "ch1/configure-output",
        } <= set(report["planned_cases"])
    if mode == "arbitrary":
        assert {"ch1/arbitrary-upload-configure", "ch1/arbitrary-clear"} <= set(report["planned_cases"])
        private_report = _read_report(_only_run(artifact_root), "private")
        builtin = next(x for x in private_report["full_inventory"] if x["name"] == expected)
        load, catalog, reuse = builtin["steps"]
        assert load["arguments"] == reuse["arguments"] and "--load-builtin" in load["arguments"]
        assert load["result"]["builtin_loaded"] is True
        assert catalog["arguments"] == ["list-arbitrary-memory"]
        assert catalog["result"]["names"] == [r"INT:\BUILTIN\SINC.ARB"]
        assert reuse["result"]["builtin_loaded"] is False
        assert reuse["readback"]["output_state"] == "off"
        for recipe in private_report["full_inventory"]:
            if not recipe["name"].startswith("ch1/arbitrary-builtin-") or recipe is builtin:
                continue
            select, catalog = recipe["steps"]
            name = select["result"]["name"]
            assert select["readback"]["output_state"] == "off"
            assert catalog["result"]["names"] == [rf"INT:\BUILTIN\{name}.ARB"]
    preflight = next((artifact_root / "_preflight").glob("run_*/shareable/report.json"))
    readiness = json.loads(preflight.read_text(encoding="utf-8"))
    assert readiness["status"] == "PASS"
    if mode == "basic-workflows":
        recipes = {
            recipe["name"]: recipe for recipe in report["full_inventory"]
            if recipe["family"] != "inspection"
        }
        assert set(recipes) == {f"ch1/{name}" for name in (
            "timed-output", "amplitude-ramp", "output-cycle",
            "waveform-sequence", "frequency-step", "quick-output-preset",
            "amplitude-ramp-descending", "frequency-step-descending",
            "quick-output-preset-sine", "quick-output-preset-triangle",
        )}
        for name, recipe in recipes.items():
            step, = recipe["steps"]
            turns_on = not name.startswith("ch1/quick-output-preset")
            assert step["workflow_intent"]["output_on"] is turns_on
            assert bool(recipe["observation"]) is turns_on
        cases = {case["name"]: case["status"] for case in readiness["cases"]}
        for name in recipes:
            for execution_mode in ("dry-run", "simulate"):
                assert cases[f"keysight-33521b/{name}/step1-{execution_mode}"] == "PASS"
        assert cases["keysight-33521b/basic-workflows-worker-lifecycle"] == "PASS"
    assert readiness["hardware_touched"] is False
    assert readiness["selected_modes"] == report["selected_modes"]
    assert readiness["complete_mode_selection"] is False
    assert readiness["visa_io_performed"] is False
    case_names = {case["name"] for case in readiness["cases"]}
    assert any(name.startswith(f"keysight-33521b/{expected}/") for name in case_names)
    assert not any(name.startswith(f"keysight-33521b/{unexpected}/") for name in case_names)
    if mode == "burst,output":
        assert "keysight-33521b/ch1/sync-control/step2-simulate" in case_names
    if mode == "arbitrary":
        cases = {case["name"]: case["status"] for case in readiness["cases"]}
        for step in (1, 3):
            for execution in ("dry-run", "simulate"):
                assert cases[f"keysight-33521b/ch1/arbitrary-builtin-select/step{step}-{execution}"] == "PASS"


def test_invalid_full_modes_fail_without_artifacts(artifact_root, fake_git):
    for modes in ("bogus", ",", "static,bogus", " "):
        args = ["-Target", "keysight-33521b", "-Suite", "full", "-Modes", modes]
        result = _run_script(PREFLIGHT_SCRIPT, artifact_root, *args)
        assert result.returncode == 2, result.stdout + result.stderr
        assert not list(artifact_root.iterdir())


def test_single_unsupported_arbitrary_mode_is_refused(artifact_root, fake_git):
    result = _run_script(
        LIVE_SCRIPT, artifact_root, "-Target", "keysight-33510b",
        "-Suite", "full", "-Modes", "arbitrary", "-Connection", "usb",
        "-Resource", USB_RESOURCE, "-PlanOnly",
    )
    assert result.returncode != 0
    report = _read_report(_only_run(artifact_root))
    assert any(case["name"] == "preflight" and case["status"] == "FAIL"
               for case in report["cases"])
    assert report["hardware_touched"] is False
    assert report["visa_io_performed"] is False



@pytest.mark.parametrize("connection,resource", [
    ("tcpip", TCPIP_RESOURCE),
    ("usb", USB_RESOURCE),
])
def test_full_reference_environment_fails_closed(artifact_root, connection, resource):
    result = _run_script(
        LIVE_SCRIPT, artifact_root, "-Target", "keysight-33521b", "-Suite", "full",
        "-Connection", connection, "-Backend", "@py", "-Resource", resource, "-PlanOnly",
    )
    assert result.returncode == 2
    assert "Backend system" in result.stderr
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
    assert "Press Enter" not in result.stdout


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
            cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=180,
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
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.split("RESULT:", 1)[1])


def test_full_publish_provenance_failure_retains_shareable_evidence(artifact_root, fake_git):
    # Main and child preflight start clean; only the final publication sees dirty Git.
    # One Full mode exercises the same publication gate without replaying all recipes.
    harness = artifact_root / "publish-provenance.ps1"
    script_path = str(LIVE_SCRIPT).replace("'", "''")
    harness.write_text(
        "param([string]$Python, [string]$OutputRoot)\n"
        "function global:git {\n"
        "    $global:LASTEXITCODE = 0\n"
        "    if ($args[2] -eq 'rev-parse') { 'a' * 40 }\n"
        "    elseif ($args[2] -eq 'status' -and (Get-PSCallStack).FunctionName -contains 'Publish-LiveArtifacts') {\n"
        "        ' M tracked-source.py'\n"
        "    }\n"
        "}\n"
        f"& '{script_path}' -Target keysight-33521b -Suite full -Modes static -Connection usb "
        f"-Backend system -Resource '{USB_RESOURCE}' -PlanOnly -DebugArtifacts -Python $Python -OutputRoot $OutputRoot\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = _run_script(harness, artifact_root)
    assert result.returncode == 1, result.stdout + result.stderr
    run = _only_run(artifact_root)
    for visibility in ("private", "shareable"):
        report = _read_report(run, visibility)
        assert report["status"] == "FAIL"
        assert report["git_clean"] is False
        assert report["git_head"] == "a" * 40
        assert "artifact_error" not in report
        failures = [case for case in report["cases"] if case["status"] == "FAIL"]
        assert len(failures) == 1 and failures[0]["name"] == "git-provenance"
        assert failures[0]["failure_reasons"] == ["Full requires a clean Git working tree."]
        assert report["summary_counts"] == {
            "passed": len(report["cases"]) - 1, "failed": 1,
            "not_applicable": 0, "total": len(report["cases"]),
        }
        assert "git-provenance" not in report["planned_cases"]
    nested = next((artifact_root / "_preflight").glob("run_*/private/report.json"))
    assert json.loads(nested.read_text(encoding="utf-8"))["artifact_mode"] == "debug"
    assert (run / "private" / "capabilities" / "stdout.json").is_file()
    assert (run / "shareable" / "capabilities" / "stdout.json").is_file()
    summary = run / "shareable" / "summary.md"
    assert "Full requires a clean Git working tree." in summary.read_text(encoding="utf-8")
    assert "Result: FAIL" in result.stdout
    assert str(run / "shareable" / "report.json") in result.stdout
    assert str(summary) in result.stdout
    assert "unavailable" not in result.stdout.lower()


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


@pytest.mark.parametrize("failure", [
    "none", "command", "cleanup", "case-dirty", "dirty", "limit", "na", "reset",
    "reset-empty-report", "reset-main-on-before", "reset-main-on-after", "reset-unreported",
    "before-timeout", "queue-timeout", "off-timeout", "termination-unconfirmed",
])
def test_full_failure_gate_and_best_effort_off(artifact_root, failure):
    result = _run_powershell_test(artifact_root, r'''
$failure = 'FAILURE'
$Suite = 'full'
$cases = [System.Collections.ArrayList]::new()
$channelsTested = [System.Collections.ArrayList]::new()
$targetCapabilityModel = '33512B'
$liveCommon = @('--json')
$repoRoot = (Get-Location).Path
$run = [pscustomobject]@{ Private = $repoRoot }
$Python = 'mock-python'
$Resource = 'synthetic-resource'
$backendName = 'system'
$expectedModel = 'keysight-33512b'
$DebugArtifacts = $false
$script:state = 'off'
$script:offCalls = 0
$script:onCalls = 0
$script:resetCalls = 0
$script:finalOffAttempts = @()
$script:caseOffAttempts = @()
$inventory = @(Get-FullValidationInventory -Channels @(2))
$script:casesUnderTest = @($inventory | Where-Object family -eq 'output-control') +
    @($inventory | Where-Object name -eq 'ch2/status')
$fullInventory = @($script:casesUnderTest)
function Invoke-WavegenJson {
    param($Name, $CliArguments, $ArtifactDirectory)
    $command = $CliArguments[0]
    if ($Name -eq 'sync/final-safe-off' -or $Name -eq 'ch2/final-safe-off-command') {
        $script:finalOffAttempts += $Name
    }
    if ($Name -like '*/cleanup-command' -or $Name -like '*/cleanup-sync') {
        $script:caseOffAttempts += $command
        if ($failure -like 'reset-main-on-*') { throw [TimeoutException]::new("$command OFF timed out.") }
    }
    if ($command -eq 'sync-output') {
        if ('--channel' -in $CliArguments -or $CliArguments[2] -ne 'off') { throw 'Unexpected Sync control' }
        $payload = [pscustomobject]@{
            success = $true; action = $command; model = '33512B'
            sync_state = 'off'; readback_verified = $true
        }
        return [pscustomobject]@{ Process = @{ exit_code = 0 }; ParseError = $null; Payload = $payload }
    }
    if ($command -ne 'read-errors' -and '--channel' -in $CliArguments) {
        $index = [array]::IndexOf($CliArguments, '--channel')
        if ($CliArguments[$index + 1] -ne '2') { throw 'Wrong channel used!' }
    }
    if (($failure -eq 'before-timeout' -and $Name -like '*step1') -or
        ($failure -eq 'queue-timeout' -and $script:state -eq 'on' -and $command -eq 'read-errors') -or
        ($failure -eq 'off-timeout' -and $command -eq 'output' -and $CliArguments[2] -eq 'off' -and
         ($Name -like '*step3' -or $Name -like '*cleanup-command' -or $Name -like '*final-safe-off-command'))) {
        throw [TimeoutException]::new("$Name timed out")
    }
    if ($command -eq 'output') {
        $script:state = $CliArguments[2]
        if ($script:state -eq 'off') { $script:offCalls++ } else { $script:onCalls++ }
        if ($failure -eq 'command' -and $script:state -eq 'on') { throw 'Injected failure after enabling output' }
        if ($failure -eq 'termination-unconfirmed' -and $script:state -eq 'on') {
            $timeout = [TimeoutException]::new('Output ON timed out. Subprocess termination could not be confirmed.')
            $timeout.Data['TerminationUnconfirmed'] = $true
            throw $timeout
        }
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
function Invoke-CapturedCommand {
    param($Name, $FilePath, $Arguments, $StdOutPath, $StdErrPath, $WorkingDirectory, [switch]$CaptureInMemory, $TimeoutSeconds)
    $script:resetCalls++
    if ($failure -eq 'reset') {
        return @{ exit_code = 1; stdout_text = '{"before_reset":true,"after_reset":false,"failure_cleanup":true}'; stderr_text = $Resource }
    }
    if ($failure -eq 'reset-unreported') {
        return @{ exit_code = 0; stdout_text = ''; stderr_text = '' }
    }
    if ($failure -eq 'reset-empty-report') {
        return @{ exit_code = 1; stdout_text = '{}'; stderr_text = $Resource }
    }
    if ($failure -eq 'reset-main-on-before') {
        $script:state = 'on'
        return @{ exit_code = 1; stdout_text = '{"main_output_on_channel":2,"main_output_on_after_reset":false}'; stderr_text = $Resource }
    }
    if ($failure -eq 'reset-main-on-after') {
        $script:state = 'on'
        return @{ exit_code = 1; stdout_text = '{"before_reset":true,"after_reset":true,"main_output_on_channel":2,"main_output_on_after_reset":true}'; stderr_text = $Resource }
    }
    return @{ exit_code = 0; stdout_text = '{"before_reset":true,"after_reset":true}'; stderr_text = '' }
}
Invoke-FullLiveCases -Channels @(2) -CanRun ($failure -ne 'na')
$finalReasons = @()
try { Assert-FullErrorQueue -Name 'final-error-queue' } catch { $finalReasons += $_.Exception.Message }
Add-LiveCase -Cases $cases -Name 'final-error-queue' -Status $(if ($finalReasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $finalReasons
$pass = Test-FullValidationPass -Cases @($cases) -RequiredNames @('ch2/initial-safe-off', 'ch2/output-control', 'ch2/final-safe-off', 'final-error-queue')
Write-Output ('RESULT:' + (@{
    pass = $pass; state = $script:state; off_calls = $script:offCalls; on_calls = $script:onCalls
    reset_calls = $script:resetCalls; final_off_attempts = $script:finalOffAttempts; cases = @($cases)
    case_off_attempts = $script:caseOffAttempts
} | ConvertTo-Json -Depth 10 -Compress))
'''.replace("FAILURE", failure))
    assert result["pass"] is (failure == "none")
    assert result["state"] == ("on" if failure == "off-timeout" else "off")
    assert result["off_calls"] >= 2
    reset_failure = failure.startswith("reset")
    assert result["on_calls"] == (0 if reset_failure or failure in {"na", "before-timeout"} else 1)
    assert result["reset_calls"] == (0 if failure == "na" else 2 if failure in {"none", "dirty", "limit"} else 1)
    statuses = {case["name"]: case["status"] for case in result["cases"]}
    case = next(case for case in result["cases"] if case["name"] == "ch2/output-control")
    if failure == "reset":
        assert case["sync_reset_off"] == {
            "before_reset": "confirmed", "after_reset": "unconfirmed",
            "failure_cleanup": "confirmed",
        }
        assert "synthetic-resource" not in json.dumps(case)
    elif failure == "reset-unreported":
        assert case["sync_reset_off"] == {
            "before_reset": "unreported", "after_reset": "unreported", "failure_cleanup": "unreported",
        }
        assert statuses["ch2/status"] == "N/A"
    elif failure in {"reset-empty-report", "reset-main-on-before", "reset-main-on-after"}:
        phase = failure.rsplit("-", 1)[-1]
        expected_sync = "confirmed" if phase == "after" else "not-run"
        assert case["sync_reset_off"] == {
            "before_reset": expected_sync, "after_reset": expected_sync, "failure_cleanup": "not-run",
        }
        assert statuses["ch2/status"] == "N/A"
        assert "Full validation reset failed (exit code 1)" in case["failure_reasons"][0]
        assert "synthetic-resource" not in json.dumps(case)
        if failure.startswith("reset-main-on-"):
            assert f"CH2 output was ON {phase}" in case["failure_reasons"][0]
            assert result["case_off_attempts"] == ["output", "sync-output"]
            assert case["cleanup_status"] == "FAIL"
            assert any("output OFF timed out" in reason for reason in case["failure_reasons"])
            assert any("sync-output OFF timed out" in reason for reason in case["failure_reasons"])
            assert statuses["ch2/final-safe-off"] == statuses["sync/final-safe-off"] == "PASS"
    elif failure == "none":
        assert case["sync_reset_off"] == {
            "before_reset": "confirmed", "after_reset": "confirmed", "failure_cleanup": "not-run",
        }
    if reset_failure or failure in {"command", "cleanup", "case-dirty"}:
        assert statuses["ch2/output-control"] == "FAIL"
    if failure == "na":
        assert statuses["ch2/output-control"] == "N/A"
    if failure in {"dirty", "limit"}:
        assert statuses["final-error-queue"] == "FAIL"
    if failure.endswith("-timeout") or failure == "termination-unconfirmed":
        assert case["status"] == "FAIL"
        assert any("timed out" in reason for reason in case["failure_reasons"])
        assert statuses["ch2/status"] == "N/A"
        if failure in {"off-timeout", "termination-unconfirmed"}:
            assert case["cleanup_status"] == "FAIL"
            assert statuses["ch2/final-safe-off"] == "FAIL"
            assert any("unconfirmed" in reason or "not confirmed" in reason
                       for reason in case["failure_reasons"])
        else:
            assert case["cleanup_status"] == "PASS"
    if failure == "off-timeout":
        assert result["final_off_attempts"] == ["ch2/final-safe-off-command", "sync/final-safe-off"]
        assert statuses["sync/final-safe-off"] == "PASS"


@pytest.mark.parametrize(("answer", "recipe"), [
    *[(answer, "burst-external") for answer in ("Yes", "No", "", "skip", "interrupted", "status-timeout")],
    ("Yes", "sweep-external-negative"),
])
def test_full_external_observation_requires_confirmation_and_safe_off(artifact_root, answer, recipe):
    result = _run_powershell_test(artifact_root, r'''
$script:operatorAnswer = 'ANSWER'
$cases = [System.Collections.ArrayList]::new()
$channelsTested = [System.Collections.ArrayList]::new()
$targetCapabilityModel = '33512B'
$script:outputs = @{ 1 = 'off'; 2 = 'off' }
$script:outputOnCalls = 0
$script:mainOffAtSyncCleanup = $false
$recipeName = 'ch1/RECIPE'
$isSweep = $recipeName -like '*/sweep-*'
$fullInventory = @(Get-FullValidationInventory -Channels @(1, 2) | Where-Object name -eq $recipeName)
function Read-LiveConfirmation {
    if ($script:outputs[1] -ne 'off') { throw 'Output must be off during preparation' }
    return $script:operatorAnswer -ne 'skip'
}
function Start-Sleep {
    if ($script:outputs[1] -ne 'on') { throw 'Output must be on during observation' }
    if ($script:operatorAnswer -eq 'interrupted') { throw 'Interrupted observation' }
}
function Read-Host {
    if ($script:outputs[1] -ne 'off') { throw 'Output must be off before answering' }
    return $script:operatorAnswer
}
function Invoke-FullCommand {
    param($Name, $Arguments, [int]$Channel)
    $command = $Arguments[0]
    if ($command -eq 'sync-output') {
        if ($Channel -ne 0 -or $Arguments[2] -ne 'off') { throw 'Unexpected Sync control' }
        if ($Name -like '*/cleanup-sync') { $script:mainOffAtSyncCleanup = $script:outputs[1] -eq 'off' }
        return [pscustomobject]@{ sync_state = 'off'; readback_verified = $true }
    }
    if ($command -eq 'trigger') { throw 'External case must use a physical trigger' }
    if ($command -eq 'readback-advanced') {
        if ($isSweep) {
            return [pscustomobject]@{
                advanced_mode = 'SWEEP'
                settings = [pscustomobject]@{
                    spacing = 'linear'; start_frequency_hz = 2; stop_frequency_hz = 8
                    sweep_time_s = 1; hold_time_s = 0.2; return_time_s = 0.2
                    trigger_source = 'external'; trigger_slope = 'negative'
                }
            }
        }
        return [pscustomobject]@{
            advanced_mode = 'BURST'
            settings = [pscustomobject]@{
                burst_mode = 'counted'; count = 3; phase_deg = 0
                trigger_source = 'external'; trigger_slope = 'positive'
            }
        }
    }
    if ($script:operatorAnswer -eq 'status-timeout' -and $command -eq 'status' -and $script:outputs[1] -eq 'on') {
        throw [TimeoutException]::new('Status query timed out.')
    }
    if ($command -eq 'output') {
        if ($Arguments[2] -eq 'on') {
            if ($Channel -ne 1) { throw 'Wrong output was enabled' }
            $script:outputOnCalls++
        }
        $script:outputs[$Channel] = $Arguments[2]
    }
    return [pscustomobject]@{
        output_state = $script:outputs[$Channel]
        function = if ($isSweep) { 'SIN' } else { 'PULS' }
        frequency_hz = 1
        amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50'
        burst_mode = 'counted'; burst_count = 3; burst_trigger_source = 'external'; burst_trigger_slope = 'positive'
        spacing = 'linear'; trigger_source = 'external'; trigger_slope = 'negative'
        readback_verified = $true
        has_errors = $false; empty_confirmed = $true; limit_reached = $false
    }
}
function Invoke-FullValidationReset {
    param($Name)
    if ($script:outputs[1] -ne 'off' -or $script:outputs[2] -ne 'off') {
        throw 'Reset requires all outputs OFF'
    }
}
Invoke-FullLiveCases -Channels @(1, 2) -CanRun $true
$case = $cases | Where-Object name -eq $recipeName
$pass = Test-FullValidationPass -Cases @($cases) -RequiredNames @($recipeName)
Write-Output ('RESULT:' + (@{
    pass = $pass; case = $case; outputOnCalls = $script:outputOnCalls
    mainOffAtSyncCleanup = $script:mainOffAtSyncCleanup
    ch1 = $script:outputs[1]; ch2 = $script:outputs[2]
} | ConvertTo-Json -Depth 8 -Compress))
'''.replace("ANSWER", answer).replace("RECIPE", recipe))
    assert result["pass"] is (answer == "Yes"), result["case"]
    assert result["ch1"] == result["ch2"] == "off"
    assert result["mainOffAtSyncCleanup"] is True
    assert result["outputOnCalls"] == (0 if answer == "skip" else 1)
    case = result["case"]
    assert case["cleanup_status"] == "PASS"
    assert case["manual_observation"]["response"] == (
        answer.lower() if answer in {"Yes", "No"} else "not-completed"
    )
    assert case["evidence_level"] == ("live-functional" if answer == "Yes" else None)


@pytest.mark.parametrize("failure", [
    "none", "initial-off", "after-on", "termination-unconfirmed", "case-off",
    "unexpected-main-on", "final-off",
])
def test_full_sync_case_attempts_off_and_fails_if_off_is_unconfirmed(artifact_root, failure):
    result = _run_powershell_test(artifact_root, r'''
$failure = 'FAILURE'
$cases = [System.Collections.ArrayList]::new()
$channelsTested = [System.Collections.ArrayList]::new()
$script:syncState = 'on'
$script:resetCalls = 0
$script:syncOffAttempts = @()
$script:syncOnAttempts = 0
$script:mainOffAttempts = @()
$script:caseOffOrder = @()
$channels = if ($failure -eq 'unexpected-main-on') { @(1, 2) } else { @(1) }
$fullInventory = @(Get-FullValidationInventory -Channels $channels -Modes output | Where-Object family -eq 'sync-control')
function Invoke-FullValidationReset {
    param($Name)
    $script:resetCalls++
}
function Invoke-FullCommand {
    param($Name, $Arguments, [int]$Channel)
    switch ($Arguments[0]) {
        'sync-output' {
            if ($Channel -ne 0) { throw 'Sync is global.' }
            if ($Arguments[2] -eq 'off') { $script:syncOffAttempts += $Name }
            else { $script:syncOnAttempts++ }
            if ($failure -eq 'unexpected-main-on' -and $Name -like '*/cleanup-sync') {
                $script:caseOffOrder += 'sync'
                throw [TimeoutException]::new('Sync OFF timed out during case cleanup.')
            }
            if (($failure -eq 'initial-off' -and $Name -eq 'sync/initial-safe-off') -or
                ($failure -eq 'case-off' -and $Name -like '*/cleanup-sync') -or
                ($failure -eq 'final-off' -and $Name -eq 'sync/final-safe-off')) {
                return [pscustomobject]@{ sync_state = 'on'; readback_verified = $true }
            }
            $script:syncState = $Arguments[2]
            if ($failure -eq 'after-on' -and $script:syncState -eq 'on') { throw 'Lost connection after ON.' }
            if ($failure -eq 'termination-unconfirmed' -and $script:syncState -eq 'on') {
                $timeout = [TimeoutException]::new('Sync ON timed out. Subprocess termination could not be confirmed.')
                $timeout.Data['TerminationUnconfirmed'] = $true
                throw $timeout
            }
            return [pscustomobject]@{ sync_state = $script:syncState; readback_verified = $true }
        }
        'output' {
            if ($Arguments[2] -ne 'off') { throw 'Main output must remain off.' }
            $script:mainOffAttempts += $Name
            if ($Name -like '*/cleanup*-command') {
                $script:caseOffOrder += "ch$Channel"
                if ($failure -eq 'unexpected-main-on' -and $Channel -eq 2) {
                    throw [TimeoutException]::new('CH2 OFF timed out during case cleanup.')
                }
            }
            return [pscustomobject]@{ output_state = 'off' }
        }
        'status' {
            $state = if ($failure -eq 'unexpected-main-on' -and $Name -like '*/step1-other-ch2') {
                'on'
            } else { 'off' }
            return [pscustomobject]@{ output_state = $state }
        }
        'read-errors' { return [pscustomobject]@{ has_errors = $false; empty_confirmed = $true; limit_reached = $false } }
        default { throw "Unexpected command: $($Arguments[0])" }
    }
}
Invoke-FullLiveCases -Channels $channels -CanRun $true
$pass = Test-FullValidationPass -Cases @($cases) -RequiredNames @('sync/initial-safe-off', 'ch1/sync-control', 'sync/final-safe-off')
$summary = Get-ValidationSummary -Report ([pscustomobject]@{
    cases = @($cases); status = $(if ($pass) { 'PASS' } else { 'FAIL' })
    hardware_touched = $false; visa_io_performed = $false
    suite = 'full'; selected_modes = @('output'); applicable_modes = @('output')
    validation_mode = 'live'; expected_model = 'keysight-33521b'; git_head = ('a' * 40)
    connection = 'usb'; backend = 'system'; resource = '<redacted-resource>'
})
Write-Output ('RESULT:' + (@{
    pass = $pass; syncState = $script:syncState; resetCalls = $script:resetCalls
    syncOffAttempts = $script:syncOffAttempts; syncOnAttempts = $script:syncOnAttempts
    mainOffAttempts = $script:mainOffAttempts
    caseOffOrder = $script:caseOffOrder
    initial = ($cases | Where-Object name -eq 'sync/initial-safe-off')
    case = ($cases | Where-Object name -eq 'ch1/sync-control')
    final = ($cases | Where-Object name -eq 'sync/final-safe-off')
    mainFinal = ($cases | Where-Object name -eq 'ch1/final-safe-off')
    otherMainFinal = ($cases | Where-Object name -eq 'ch2/final-safe-off')
    summary = $summary
} | ConvertTo-Json -Depth 8 -Compress))
'''.replace("FAILURE", failure))
    assert result["pass"] is (failure == "none")
    assert result["syncState"] == "off"
    assert "sync/final-safe-off" in result["syncOffAttempts"]
    assert "ch1/final-safe-off-command" in result["mainOffAttempts"]
    if failure == "initial-off":
        assert result["initial"]["status"] == "FAIL"
        assert result["case"]["status"] == "N/A"
        assert result["resetCalls"] == result["syncOnAttempts"] == 0
        assert result["final"]["status"] == "PASS"
        return
    assert result["resetCalls"] == result["syncOnAttempts"] == 1
    assert "ch1/sync-control/cleanup-sync" in result["syncOffAttempts"]
    assert "ch1/sync-control/cleanup-command" in result["mainOffAttempts"]
    unconfirmed = failure in {"case-off", "termination-unconfirmed", "unexpected-main-on"}
    assert result["case"]["sync_cleanup_status"] == ("unconfirmed" if unconfirmed else "confirmed")
    assert result["case"]["cleanup_status"] == ("FAIL" if unconfirmed else "PASS")
    assert result["final"]["status"] == (
        "FAIL" if failure in {"final-off", "termination-unconfirmed"} else "PASS"
    )
    if failure == "termination-unconfirmed":
        assert result["mainFinal"]["status"] == "PASS"
        assert "Case Sync OFF cleanup: unconfirmed." in result["summary"]
        for case_name in ("case", "final"):
            assert any(
                "Sync OFF remains unconfirmed" in reason
                for reason in result[case_name]["failure_reasons"]
            )
    if failure == "unexpected-main-on":
        assert result["caseOffOrder"] == ["ch2", "sync", "ch1"]
        assert result["case"]["status"] == "FAIL"
        assert result["otherMainFinal"]["status"] == "PASS"
        assert any("CH2 OFF timed out" in reason for reason in result["case"]["failure_reasons"])
        assert any("Sync OFF timed out" in reason for reason in result["case"]["failure_reasons"])


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
foreach ($pair in @(@('SIN', 'SINUSOID'), @('SQU', 'SQUARE'), @('PULS', 'PULSE'), @('NOIS', 'NOISE'), @('TRI', 'TRIANGLE'), @('ARB', 'ARB'))) {
    foreach ($spelling in $pair) {
        Assert-FullReadback -Payload ([pscustomobject]@{ function = $spelling }) -Expected @{ function = $pair[0] }
    }
}
$rejected = $false
try { Assert-FullReadback -Payload ([pscustomobject]@{ function = 'RAMP' }) -Expected @{ function = 'SQU' } } catch { $rejected = $true }
Write-Output ('RESULT:' + (@{ rejected = $rejected } | ConvertTo-Json -Compress))
''')
    assert result['rejected'] is True


def test_full_builtin_readback_requires_catalog_entry_and_reuse(artifact_root):
    result = _run_powershell_test(artifact_root, r'''
$path = 'INT:\BUILTIN\SINC.ARB'
$expected = @{ names = @($path); builtin_loaded = $false }
Assert-FullReadback -Payload ([pscustomobject]@{ names = @('WAVE', $path.ToLowerInvariant()); builtin_loaded = $false }) -Expected $expected
$rejected = 0
foreach ($payload in @(
    [pscustomobject]@{ names = @('SINC'); builtin_loaded = $false },
    [pscustomobject]@{ names = @($path); builtin_loaded = $true }
)) {
    try { Assert-FullReadback -Payload $payload -Expected $expected } catch { $rejected++ }
}
Write-Output ('RESULT:' + (@{ rejected = $rejected } | ConvertTo-Json -Compress))
''')
    assert result["rejected"] == 2


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


def test_arbitrary_clear_refusal_never_executes_clear(artifact_root):
    result = _run_powershell_test(artifact_root, r'''
$cases = [System.Collections.ArrayList]::new()
$channelsTested = [System.Collections.ArrayList]::new()
$targetCapabilityModel = '33521B'
$script:clearCalls = 0
$fullInventory = @([pscustomobject]@{
    name = 'ch1/arbitrary-clear'; family = 'arbitrary'; channel = 1; required = $true
    observation = $null
    steps = @([pscustomobject]@{
        arguments = @('clear-arbitrary-memory', '--confirm-clear')
        readback = @{}; result = @{}
    })
})
function Set-FullSafeOff { param($Name, $Channel) }
function Assert-FullErrorQueue { param($Name) }
function Invoke-FullValidationReset { param($Name) }
function Invoke-FullCommand {
    param($Name, $Arguments, [int]$Channel)
    if ($Arguments[0] -eq 'sync-output') {
        return [pscustomobject]@{ sync_state = 'off'; readback_verified = $true }
    }
    if ($Arguments[0] -eq 'clear-arbitrary-memory') { $script:clearCalls++ }
    return [pscustomobject]@{ output_state = 'off' }
}
Invoke-FullLiveCases -Channels @(1) -CanRun $true
$entry = $cases | Where-Object name -eq 'ch1/arbitrary-clear'
Write-Output ('RESULT:' + (@{
    clear_calls = $script:clearCalls; status = $entry.status
    cleanup_status = $entry.cleanup_status
} | ConvertTo-Json -Compress))
''')
    assert result["clear_calls"] == 0
    assert result["status"] == "FAIL"
    assert result["cleanup_status"] == "PASS"


@pytest.mark.parametrize("observation", ["confirmed", "declined", "missing"])
def test_full_software_sequence_requires_observation_for_nested_output(artifact_root, observation):
    result = _run_powershell_test(artifact_root, r'''
Set-StrictMode -Version Latest
$script:observation = 'OBSERVATION'
$script:sequenceCalls = 0
$script:cleanupCalled = $false
$cases = [System.Collections.ArrayList]::new()
$channelsTested = [System.Collections.ArrayList]::new()
$targetCapabilityModel = '33521B'
$recipeName = 'ch1/software-sequence-basic'
$fullInventory = @(Get-FullValidationInventory -Channels @(1) -Modes software-sequence -SequenceFixtureDirectory $PSScriptRoot | Where-Object name -eq $recipeName)
if ($script:observation -eq 'missing') { $fullInventory[0].observation = $null }
function Read-LiveConfirmation { return $script:observation -eq 'confirmed' }
function Read-Host { return 'Yes' }
function Invoke-FullValidationReset {
    param($Name, $SyncOff, $Channels)
    $SyncOff.before_reset = 'confirmed'
    $SyncOff.after_reset = 'confirmed'
}
function Invoke-FullCommand {
    param($Name, $Arguments, [int]$Channel)
    if ($Arguments[0] -eq 'run-software-sequence') {
        if ($Channel -ne 0) { throw 'Sequence gets its channel from the document' }
        $script:sequenceCalls++
        return [pscustomobject]@{
            executed = $true; status = 'completed'; channels = @(1); step_count = 4
            loop_count = 1; execution_units = 4; completed_loops = 1
            completed_step_executions = 4; total_wait_seconds = 3
            results = @('configure-sine', 'output', 'wait', 'output' | ForEach-Object {
                [pscustomobject]@{ action = $_ }
            })
            cleanup = @{ attempted_channels = @(1); confirmed_off_channels = @(1); errors = @() }
        }
    }
    if ($Name -like '*/cleanup-command') {
        $script:cleanupCalled = $Arguments[0] -eq 'output' -and $Arguments[2] -eq 'off'
    }
    return [pscustomobject]@{
        output_state = 'off'; function = 'SIN'; sync_state = 'off'; readback_verified = $true
        has_errors = $false; empty_confirmed = $true; limit_reached = $false
    }
}
Invoke-FullLiveCases -Channels @(1) -CanRun $true
$case = $cases | Where-Object name -eq $recipeName
Write-Output ('RESULT:' + (@{
    case = $case; sequenceCalls = $script:sequenceCalls; cleanupCalled = $script:cleanupCalled
} | ConvertTo-Json -Depth 8 -Compress))
'''.replace("OBSERVATION", observation))
    assert result["cleanupCalled"] is True
    assert result["sequenceCalls"] == (1 if observation == "confirmed" else 0)
    assert result["case"]["status"] == ("PASS" if observation == "confirmed" else "FAIL")
