[CmdletBinding()]
param(
    [string]$Target = "all",
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$OutputRoot = ".tmp_tests\cli_preflight"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
. (Join-Path $PSScriptRoot "_validation_helpers.ps1")
. (Join-Path $PSScriptRoot "_artifact_privacy.ps1")

function Invoke-WavegenJson {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$CliArguments,
        [Parameter(Mandatory = $true)][string]$ArtifactDirectory
    )

    New-Item -ItemType Directory -Force -Path $ArtifactDirectory | Out-Null
    $stdoutPath = Join-Path $ArtifactDirectory "stdout.json"
    $stderrPath = Join-Path $ArtifactDirectory "stderr.txt"
    $arguments = @(
        "-c",
        "from wavegen_tool_cli.cli import main; raise SystemExit(main())"
    ) + $CliArguments
    $process = Invoke-CapturedCommand `
        -Name $Name `
        -FilePath $Python `
        -Arguments $arguments `
        -StdOutPath $stdoutPath `
        -StdErrPath $stderrPath `
        -WorkingDirectory $repoRoot

    $payload = $null
    $parseError = $null
    if ($process.exit_code -eq 0) {
        try {
            $raw = Get-Content -LiteralPath $stdoutPath -Raw
            if ([string]::IsNullOrWhiteSpace($raw)) {
                throw "CLI returned empty JSON output."
            }
            $payload = $raw | ConvertFrom-Json -ErrorAction Stop
        } catch {
            $parseError = $_.Exception.Message
        }
    }
    return [pscustomobject]@{
        Process = $process
        Payload = $payload
        ParseError = $parseError
    }
}

function Add-ValidationCase {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$Cases,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet("PASS", "FAIL", "N/A")][string]$Status,
        [AllowEmptyCollection()][string[]]$FailureReasons = @(),
        $Process = $null
    )

    $case = [ordered]@{
        name = $Name
        status = $Status
        failure_reasons = @($FailureReasons)
    }
    if ($null -ne $Process) {
        $case.process = $Process
    }
    [void]$Cases.Add([pscustomobject]$case)
    Write-CaseStatus `
        -Status $Status `
        -Context "[preflight][cli]" `
        -Name $Name `
        -FailureReasons $FailureReasons
}

function Get-SummaryCounts {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Cases
    )

    return [ordered]@{
        passed = @($Cases | Where-Object { $_.status -eq "PASS" }).Count
        failed = @($Cases | Where-Object { $_.status -eq "FAIL" }).Count
        not_applicable = @($Cases | Where-Object { $_.status -eq "N/A" }).Count
        total = $Cases.Count
    }
}

function Test-CloseNumber {
    param(
        [Parameter(Mandatory = $true)][double]$Actual,
        [Parameter(Mandatory = $true)][double]$Expected,
        [double]$Tolerance = 0.000001
    )

    return [math]::Abs($Actual - $Expected) -le $Tolerance
}

function Write-PrivateSummary {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Report
    )

    $lines = @(
        "# CLI Preflight Summary",
        "",
        "- Result: $($Report.status)",
        "- Validation mode: $($Report.validation_mode)",
        "- Target selection: $($Report.target)",
        "- Hardware touched: false",
        "- VISA I/O performed: false",
        "",
        "## Cases",
        ""
    )
    foreach ($case in @($Report.cases)) {
        $lines += "- $($case.status): $($case.name)"
        foreach ($reason in @($case.failure_reasons)) {
            $lines += "  - $reason"
        }
    }
    Write-Utf8NoBomLines -LiteralPath $Path -Lines $lines
}

try {
    $targets = @(Resolve-ValidationTargets -Target $Target)
    $run = New-ValidationRunDirectory -RepoRoot $repoRoot -BaseRoot $OutputRoot
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}

$Python = Get-FullPath -Path $Python -BaseRoot $repoRoot
$cases = [System.Collections.ArrayList]::new()
$targetResults = [System.Collections.ArrayList]::new()
$targetIds = @($targets | ForEach-Object { [string]$_.model_id })
Write-Host "[preflight][cli] target(s): $($targetIds -join ', ')"
Write-Host ""

foreach ($targetProfile in $targets) {
    $modelId = [string]$targetProfile.model_id
    $capabilityModel = [string]$targetProfile.model
    $targetDirectory = Join-Path $run.Private $modelId
    $channelsTested = [System.Collections.ArrayList]::new()
    $channelCount = $null

    $capabilityName = "${modelId}/capabilities"
    $capabilityInvocation = $null
    $capabilityReasons = [System.Collections.ArrayList]::new()
    try {
        $capabilityInvocation = Invoke-WavegenJson `
            -Name $capabilityName `
            -CliArguments @("capabilities", "--model", $modelId, "--json") `
            -ArtifactDirectory (Join-Path $targetDirectory "capabilities")
        if ($capabilityInvocation.Process.exit_code -ne 0) {
            [void]$capabilityReasons.Add(
                "Command exited with code $($capabilityInvocation.Process.exit_code)."
            )
        } elseif ($null -ne $capabilityInvocation.ParseError) {
            [void]$capabilityReasons.Add("Invalid JSON output: $($capabilityInvocation.ParseError)")
        } else {
            $payload = $capabilityInvocation.Payload
            if ($payload.event -cne "capabilities") {
                [void]$capabilityReasons.Add("Unexpected capabilities event.")
            }
            if ($payload.selection.requested_model -cne $modelId) {
                [void]$capabilityReasons.Add("Requested model did not match the target.")
            }
            if ($payload.model.model_id -cne $modelId) {
                [void]$capabilityReasons.Add("Capability model ID did not match the target.")
            }
            if ($payload.model.canonical_model -cne $capabilityModel) {
                [void]$capabilityReasons.Add("Capability profile model was unexpected.")
            }
            $resolvedCount = 0
            if (
                -not [int]::TryParse(
                    [string]$payload.capabilities.channel_count,
                    [ref]$resolvedCount
                ) -or
                $resolvedCount -lt 1 -or
                $resolvedCount -gt 64
            ) {
                [void]$capabilityReasons.Add("Capability channel_count was missing or unreasonable.")
            } else {
                $channelCount = $resolvedCount
            }
        }
    } catch {
        [void]$capabilityReasons.Add($_.Exception.Message)
    }

    if ($capabilityReasons.Count -eq 0) {
        Add-ValidationCase `
            -Cases $cases `
            -Name $capabilityName `
            -Status PASS `
            -Process $capabilityInvocation.Process
    } else {
        Add-ValidationCase `
            -Cases $cases `
            -Name $capabilityName `
            -Status FAIL `
            -FailureReasons @($capabilityReasons) `
            -Process $(if ($null -ne $capabilityInvocation) { $capabilityInvocation.Process } else { $null })
        Add-ValidationCase `
            -Cases $cases `
            -Name "${modelId}/channel-sine-dry-run" `
            -Status "N/A" `
            -FailureReasons "Authoritative channel_count is unavailable."
        Add-ValidationCase `
            -Cases $cases `
            -Name "${modelId}/channel-sine-simulate" `
            -Status "N/A" `
            -FailureReasons "Authoritative channel_count is unavailable."
        [void]$targetResults.Add([pscustomobject][ordered]@{
            expected_model = $modelId
            target_capability_model = $capabilityModel
            channel_count = $null
            channels_tested = @()
        })
        Write-Host ""
        continue
    }

    foreach ($channel in 1..$channelCount) {
        [void]$channelsTested.Add($channel)
        $commonArguments = @(
            "--model", $modelId,
            "--channel", [string]$channel,
            "--frequency-hz", "1000",
            "--amplitude-vpp", "0.1",
            "--offset-v", "0",
            "--phase-deg", "0",
            "--load", "50",
            "--json"
        )

        $dryRunName = "${modelId}/ch${channel}-sine-dry-run"
        $dryRunReasons = [System.Collections.ArrayList]::new()
        $dryRunInvocation = $null
        try {
            $dryRunInvocation = Invoke-WavegenJson `
                -Name $dryRunName `
                -CliArguments (@("configure-sine", "--dry-run") + $commonArguments) `
                -ArtifactDirectory (Join-Path $targetDirectory "ch${channel}-sine-dry-run")
            if ($dryRunInvocation.Process.exit_code -ne 0) {
                [void]$dryRunReasons.Add(
                    "Command exited with code $($dryRunInvocation.Process.exit_code)."
                )
            } elseif ($null -ne $dryRunInvocation.ParseError) {
                [void]$dryRunReasons.Add("Invalid JSON output: $($dryRunInvocation.ParseError)")
            } else {
                $payload = $dryRunInvocation.Payload
                if ($payload.success -ne $true) { [void]$dryRunReasons.Add("Command did not succeed.") }
                if ($payload.action -cne "configure-sine") { [void]$dryRunReasons.Add("Unexpected action.") }
                if ($payload.mode -cne "dry-run") { [void]$dryRunReasons.Add("Unexpected mode.") }
                if ($payload.canonical_model_id -cne $modelId) { [void]$dryRunReasons.Add("Model ID did not match the target.") }
                if ([int]$payload.channel -ne $channel) { [void]$dryRunReasons.Add("Channel did not match the plan.") }
                if (@($payload.commands).Count -eq 0) { [void]$dryRunReasons.Add("SCPI plan was empty.") }
                if ($payload.executed -ne $false) { [void]$dryRunReasons.Add("Dry-run reported hardware execution.") }
                if ([string]$payload.output_state -cne "off") { [void]$dryRunReasons.Add("Output was not left off.") }
            }
        } catch {
            [void]$dryRunReasons.Add($_.Exception.Message)
        }
        Add-ValidationCase `
            -Cases $cases `
            -Name $dryRunName `
            -Status $(if ($dryRunReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
            -FailureReasons @($dryRunReasons) `
            -Process $(if ($null -ne $dryRunInvocation) { $dryRunInvocation.Process } else { $null })

        $simulateName = "${modelId}/ch${channel}-sine-simulate"
        $simulateReasons = [System.Collections.ArrayList]::new()
        $simulateInvocation = $null
        try {
            $simulateInvocation = Invoke-WavegenJson `
                -Name $simulateName `
                -CliArguments (@("configure-sine", "--simulate") + $commonArguments) `
                -ArtifactDirectory (Join-Path $targetDirectory "ch${channel}-sine-simulate")
            if ($simulateInvocation.Process.exit_code -ne 0) {
                [void]$simulateReasons.Add(
                    "Command exited with code $($simulateInvocation.Process.exit_code)."
                )
            } elseif ($null -ne $simulateInvocation.ParseError) {
                [void]$simulateReasons.Add("Invalid JSON output: $($simulateInvocation.ParseError)")
            } else {
                $payload = $simulateInvocation.Payload
                if ($payload.success -ne $true) { [void]$simulateReasons.Add("Command did not succeed.") }
                if ($payload.action -cne "configure-sine") { [void]$simulateReasons.Add("Unexpected action.") }
                if ($payload.mode -cne "simulate" -or $payload.simulated -ne $true) { [void]$simulateReasons.Add("Simulator path was not reported.") }
                if ([string]$payload.model -cne $capabilityModel) { [void]$simulateReasons.Add("Simulator model did not match the target profile.") }
                if ([int]$payload.channel -ne $channel) { [void]$simulateReasons.Add("Channel did not match the plan.") }
                if (-not (Test-CloseNumber -Actual ([double]$payload.frequency_hz) -Expected 1000.0)) { [void]$simulateReasons.Add("Frequency result was unexpected.") }
                if (-not (Test-CloseNumber -Actual ([double]$payload.amplitude_vpp) -Expected 0.1)) { [void]$simulateReasons.Add("Amplitude result was unexpected.") }
                if (-not (Test-CloseNumber -Actual ([double]$payload.offset_v) -Expected 0.0)) { [void]$simulateReasons.Add("Offset result was unexpected.") }
                if (-not (Test-CloseNumber -Actual ([double]$payload.phase_deg) -Expected 0.0)) { [void]$simulateReasons.Add("Phase result was unexpected.") }
                if ([string]$payload.load -cne "50") { [void]$simulateReasons.Add("Load result was unexpected.") }
                if ([string]$payload.output_state -cne "off") { [void]$simulateReasons.Add("Output was not left off.") }
            }
        } catch {
            [void]$simulateReasons.Add($_.Exception.Message)
        }
        Add-ValidationCase `
            -Cases $cases `
            -Name $simulateName `
            -Status $(if ($simulateReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
            -FailureReasons @($simulateReasons) `
            -Process $(if ($null -ne $simulateInvocation) { $simulateInvocation.Process } else { $null })
    }

    [void]$targetResults.Add([pscustomobject][ordered]@{
        expected_model = $modelId
        target_capability_model = $capabilityModel
        channel_count = $channelCount
        channels_tested = @($channelsTested)
    })
    Write-Host ""
}

$summaryCounts = Get-SummaryCounts -Cases $cases
$status = if ($summaryCounts.failed -eq 0) { "PASS" } else { "FAIL" }
$privateReportPath = Join-Path $run.Private "report.json"
$privateSummaryPath = Join-Path $run.Private "summary.md"
$report = [pscustomobject][ordered]@{
    schema_version = 1
    kind = "cli-preflight"
    status = $status
    target = $Target
    targets = $targetIds
    target_results = @($targetResults)
    package_version = Get-PackageVersion -ProjectRoot $repoRoot
    git_head = Get-GitHead -ProjectRoot $repoRoot
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    validation_mode = "no-hardware-cli-preflight"
    hardware_touched = $false
    visa_io_performed = $false
    resource_scan_performed = $false
    resource_guess_performed = $false
    cases = @($cases)
    summary_counts = $summaryCounts
    artifact_paths = [ordered]@{
        private_report = $privateReportPath
        private_summary = $privateSummaryPath
        shareable_report = Join-Path $run.Shareable "report.json"
        shareable_summary = Join-Path $run.Shareable "summary.md"
    }
}
Write-JsonReport -LiteralPath $privateReportPath -Report $report
Write-PrivateSummary -Path $privateSummaryPath -Report $report

try {
    $null = New-ShareableArtifactSet `
        -PrivateReport $report `
        -PrivateSummaryPath $privateSummaryPath `
        -RunRoot $run.Root `
        -PrivateRoot $run.Private `
        -ShareableRoot $run.Shareable `
        -RepoRoot $repoRoot
} catch {
    $report.status = "FAIL"
    $report | Add-Member -NotePropertyName artifact_error -NotePropertyValue $_.Exception.Message -Force
    Write-JsonReport -LiteralPath $privateReportPath -Report $report
    Write-PrivateSummary -Path $privateSummaryPath -Report $report
    Write-CaseStatus `
        -Status FAIL `
        -Context "[preflight][cli]" `
        -Name "shareable-artifacts" `
        -FailureReasons $_.Exception.Message
    Write-Host "Result: FAIL"
    Write-Host "Private artifacts: $($run.Private)"
    exit 1
}

Write-Host "Result: $($report.status)"
Write-Host "Private artifacts: $($run.Private)"
Write-Host "Shareable artifacts: $($run.Shareable)"
if ($report.status -eq "PASS") {
    exit 0
}
exit 1
