[CmdletBinding()]
param(
    [ValidateSet("smoke", "full")][string]$Suite = "smoke",
    [Parameter(Mandatory = $true)]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [string]$Connection,

    [Parameter(Mandatory = $true)]
    [string]$Resource,

    [string]$Backend = "system",

    [switch]$PlanOnly,

    [string]$Python = ".\.venv\Scripts\python.exe",

    [string]$OutputRoot = ".tmp_tests\cli_live"
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

function Add-LiveCase {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Cases,
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
        -Context "[live][cli]" `
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
        [double]$AbsoluteTolerance = 0.000001,
        [double]$RelativeTolerance = 0.000001
    )

    $limit = [math]::Max($AbsoluteTolerance, [math]::Abs($Expected) * $RelativeTolerance)
    return [math]::Abs($Actual - $Expected) -le $limit
}

function Get-PlannedLiveCases {
    param([Parameter(Mandatory = $true)][int[]]$Channels)

    if ($Suite -eq 'full') {
        return @('identity', 'baseline-error-drain') + @($Channels | ForEach-Object { "ch$_/initial-safe-off" }) + @((Get-FullValidationInventory -Channels $Channels).name) + @($Channels | ForEach-Object { "ch$_/final-safe-off" }) + @('final-error-queue')
    }
    $planned = [System.Collections.ArrayList]::new()
    [void]$planned.Add("identity")
    [void]$planned.Add("baseline-error-drain")
    foreach ($channel in $Channels) {
        [void]$planned.Add("ch${channel}/output-off-before")
        [void]$planned.Add("ch${channel}/sine-config")
        [void]$planned.Add("ch${channel}/sine-readback")
        [void]$planned.Add("ch${channel}/output-off-after")
    }
    [void]$planned.Add("final-error-queue")
    return @($planned)
}

function Write-CompletionSummary {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("PLANNED", "PASS", "FAIL", "CANCELLED")]
        [string]$Result,
        [Parameter(Mandatory = $true)]$Run,
        [bool]$ShareableAvailable = $true
    )

    Write-Host ""
    Write-Host "Result: $Result"
    Write-Host ""
    Write-Host "Private artifacts:"
    Write-Host "  $($Run.Private)"
    Write-Host ""
    Write-Host "Shareable report:"
    Write-Host $(if ($ShareableAvailable) { "  $(Join-Path $Run.Shareable 'report.json')" } else { "  unavailable" })
    Write-Host "Shareable summary:"
    Write-Host $(if ($ShareableAvailable) { "  $(Join-Path $Run.Shareable 'summary.md')" } else { "  unavailable" })
}

function Write-PrivateSummary {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Report
    )

    $lines = @(
        "# Live CLI Validation Summary",
        "",
        "- Result: $($Report.status)",
        "- Suite: $($Report.suite)",
        "- Validation mode: $($Report.validation_mode)",
        "- Expected model: $($Report.expected_model)",
        "- Connection: $($Report.connection)",
        "- Backend: $($Report.backend)",
        "- Resource: $($Report.resource)",
        "- Hardware touched: $($Report.hardware_touched.ToString().ToLowerInvariant())",
        "- VISA I/O performed: $($Report.visa_io_performed.ToString().ToLowerInvariant())",
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

function Publish-LiveArtifacts {
    param(
        [Parameter(Mandatory = $true)]$Report,
        [Parameter(Mandatory = $true)]$Run,
        [Parameter(Mandatory = $true)][string]$PrivateReportPath,
        [Parameter(Mandatory = $true)][string]$PrivateSummaryPath,
        [AllowEmptyCollection()][string[]]$ExtraSensitiveValues = @()
    )

    if ($Report.suite -eq 'full' -and $Report.status -eq 'PASS') {
        try { $null = Assert-FullGitRevision -ProjectRoot $repoRoot -ExpectedHead $Report.git_head } catch {
            $Report.status = 'FAIL'
            $Report.git_clean = $false
            $reportCases = [System.Collections.ArrayList]::new()
            $reportCases.AddRange(@($Report.cases))
            Add-LiveCase -Cases $reportCases -Name 'git-provenance' -Status FAIL -FailureReasons $_.Exception.Message
            $Report.cases = @($reportCases)
            $Report.summary_counts = Get-SummaryCounts -Cases $reportCases
        }
    }
    Write-JsonReport -LiteralPath $PrivateReportPath -Report $Report
    Write-PrivateSummary -Path $PrivateSummaryPath -Report $Report
    try {
        $null = New-ShareableArtifactSet `
            -PrivateReport $Report `
            -PrivateSummaryPath $PrivateSummaryPath `
            -RunRoot $Run.Root `
            -PrivateRoot $Run.Private `
            -ShareableRoot $Run.Shareable `
            -RepoRoot $repoRoot `
            -Resource $Resource `
            -ExtraSensitiveValues $ExtraSensitiveValues
        return $true
    } catch {
        $Report.status = "FAIL"
        $Report | Add-Member `
            -NotePropertyName artifact_error `
            -NotePropertyValue $_.Exception.Message `
            -Force
        Write-JsonReport -LiteralPath $PrivateReportPath -Report $Report
        Write-PrivateSummary -Path $PrivateSummaryPath -Report $Report
        Write-CaseStatus `
            -Status FAIL `
            -Context "[live][cli]" `
            -Name "shareable-artifacts" `
            -FailureReasons $_.Exception.Message
        return $false
    }
}

function Add-RemainingChannelCases {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Cases,
        [Parameter(Mandatory = $true)][int[]]$Channels,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    if ($Suite -eq 'full') {
        foreach ($recipe in @(Get-FullValidationInventory -Channels $Channels)) {
            Add-LiveCase -Cases $Cases -Name $recipe.name -Status 'N/A' -FailureReasons $Reason
        }
        return
    }
    foreach ($channel in $Channels) {
        foreach ($suffix in @("output-off-before", "sine-config", "sine-readback", "output-off-after")) {
            Add-LiveCase `
                -Cases $Cases `
                -Name "ch${channel}/${suffix}" `
                -Status "N/A" `
                -FailureReasons $Reason
        }
    }
}

function Invoke-FullCommand {
    param([string]$Name, [string[]]$Arguments, [int]$Channel = 0)

    $args = @($Arguments) + $liveCommon
    if ($Channel -gt 0) { $args += @('--channel', [string]$Channel) }
    $invocation = Invoke-WavegenJson -Name $Name -CliArguments $args -ArtifactDirectory (Join-Path $run.Private (New-SafeCaseName $Name))
    if ($invocation.Process.exit_code -ne 0) { throw "$Name exited with code $($invocation.Process.exit_code); see command diagnostics." }
    if ($null -ne $invocation.ParseError) { throw "$Name returned invalid JSON." }
    $payload = $invocation.Payload
    if ($payload.success -ne $true -or $payload.action -cne $Arguments[0] -or $payload.model -cne $targetCapabilityModel) { throw "$Name returned an unsuccessful or mismatched result." }
    if ($Channel -gt 0 -and [int]$payload.channel -ne $Channel) { throw "$Name returned a different channel." }
    return $payload
}

function Assert-FullReadback {
    param($Payload, [hashtable]$Expected)
    foreach ($key in $Expected.Keys) {
        if ($key -notin $Payload.PSObject.Properties.Name -or $null -eq $Payload.$key) { throw "Missing readback: $key." }
        $value = $Expected[$key]
        if ($key -eq 'function') {
            $aliases = @{ SIN = @('SIN', 'SINUSOID'); SQU = @('SQU', 'SQUARE'); RAMP = @('RAMP'); TRI = @('TRI', 'TRIANGLE'); PULS = @('PULS', 'PULSE'); DC = @('DC'); NOIS = @('NOIS', 'NOISE'); PRBS = @('PRBS') }
            if ([string]$Payload.$key -cnotin $aliases[$value]) { throw "Unexpected readback: $key." }
        } elseif ($value -is [string]) {
            if ([string]$Payload.$key -cne $value) { throw "Unexpected readback: $key." }
        } elseif (-not (Test-CloseNumber -Actual ([double]$Payload.$key) -Expected ([double]$value) -AbsoluteTolerance 0.000001 -RelativeTolerance 0.0001)) {
            throw "Readback outside tolerance: $key."
        }
    }
}

function Assert-FullErrorQueue {
    param([string]$Name)
    $payload = Invoke-FullCommand -Name $Name -Arguments @('read-errors', '--max-reads', '20')
    if ($payload.has_errors -ne $false -or $payload.empty_confirmed -ne $true -or $payload.limit_reached -ne $false) {
        throw "$Name was dirty or did not reach code 0 within bounded reads."
    }
}

function Set-FullSafeOff {
    param([string]$Name, [int]$Channel)
    $payload = Invoke-FullCommand -Name "$Name-command" -Arguments @('output', '--state', 'off') -Channel $Channel
    Assert-FullReadback -Payload $payload -Expected @{ output_state = 'off' }
    $payload = Invoke-FullCommand -Name "$Name-status" -Arguments @('status') -Channel $Channel
    Assert-FullReadback -Payload $payload -Expected @{ output_state = 'off' }
}

function Invoke-FullLiveCases {
    param([int[]]$Channels, [bool]$CanRun)

    try {
        foreach ($channel in $Channels) {
            $name = "ch${channel}/initial-safe-off"
            $reasons = @()
            try { Set-FullSafeOff -Name $name -Channel $channel; Assert-FullErrorQueue -Name "$name-errors" }
            catch { $reasons += $_.Exception.Message; $CanRun = $false }
            Add-LiveCase -Cases $cases -Name $name -Status $(if ($reasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $reasons
        }
        foreach ($recipe in @(Get-FullValidationInventory -Channels $Channels)) {
            if (-not $CanRun) {
                Add-LiveCase -Cases $cases -Name $recipe.name -Status 'N/A' -FailureReasons 'A required prerequisite or prior case failed.'
                continue
            }
            $channel = $recipe.channel
            if ($channel -notin $channelsTested) { [void]$channelsTested.Add($channel) }
            $reasons = @()
            try {
                Set-FullSafeOff -Name "$($recipe.name)/before" -Channel $channel
                Assert-FullErrorQueue -Name "$($recipe.name)/before-errors"
                $index = 0
                foreach ($step in $recipe.steps) {
                    $index += 1
                    $name = "$($recipe.name)/step$index"
                    $command = $step.arguments[0]
                    if ($command -eq 'output' -and $step.arguments[2] -eq 'on' -and $recipe.family -cne 'output-control') { throw 'Output ON is restricted to the dedicated case.' }
                    $commandChannel = if ($command -eq 'trigger') { 0 } else { $channel }
                    $null = Invoke-FullCommand -Name $name -Arguments $step.arguments -Channel $commandChannel
                    $expected = @{ output_state = 'off' }
                    foreach ($key in $step.readback.Keys) { $expected[$key] = $step.readback[$key] }
                    $payload = Invoke-FullCommand -Name "$name-readback" -Arguments @('status') -Channel $channel
                    Assert-FullReadback -Payload $payload -Expected $expected
                    foreach ($other in @($Channels | Where-Object { $_ -ne $channel })) {
                        $payload = Invoke-FullCommand -Name "$name-other-ch$other" -Arguments @('status') -Channel $other
                        Assert-FullReadback -Payload $payload -Expected @{ output_state = 'off' }
                    }
                    Assert-FullErrorQueue -Name "$name-errors"
                }
            } catch { $reasons += $_.Exception.Message }
            finally {
                # Drain even after command/readback failure to retain case-local errors.
                try { Assert-FullErrorQueue -Name "$($recipe.name)/case-errors" }
                catch { $reasons += $_.Exception.Message }
                try { Set-FullSafeOff -Name "$($recipe.name)/cleanup" -Channel $channel }
                catch { $reasons += "Cleanup: $($_.Exception.Message)" }
                try { Assert-FullErrorQueue -Name "$($recipe.name)/cleanup-errors" }
                catch { $reasons += $_.Exception.Message }
            }
            Add-LiveCase -Cases $cases -Name $recipe.name -Status $(if ($reasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $reasons
            if ($reasons.Count) { $CanRun = $false }
        }
    } finally {
        foreach ($channel in $Channels) {
            $name = "ch${channel}/final-safe-off"
            $reasons = @()
            try { Set-FullSafeOff -Name $name -Channel $channel }
            catch { $reasons += $_.Exception.Message }
            Add-LiveCase -Cases $cases -Name $name -Status $(if ($reasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $reasons
        }
    }
}

try {
    $targetProfile = Resolve-ValidationTarget -Target $Target
    $connectionName = $Connection.Trim().ToLowerInvariant()
    if ($connectionName -notin @("usb", "tcpip")) {
        throw "Unsupported connection '$Connection'. Use 'usb' or 'tcpip'."
    }
    if ([string]::IsNullOrWhiteSpace($Resource)) {
        throw "Resource is required and must be explicit."
    }
    $resourceMatches = if ($connectionName -eq "usb") {
        $Resource -match '^(?i)USB\d*::'
    } else {
        $Resource -match '^(?i)TCPIP\d*::'
    }
    if (-not $resourceMatches) {
        throw "Connection '$connectionName' does not match the explicit Resource prefix."
    }
    if ([string]::IsNullOrWhiteSpace($Backend)) {
        $backendName = "system"
    } else {
        $backendName = $Backend.Trim()
        if ($backendName.ToLowerInvariant() -eq "system") {
            $backendName = "system"
        }
    }
    if ($Suite -eq 'full' -and ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSEdition -ne 'Desktop' -or $connectionName -cne 'usb' -or $backendName -cne 'system')) {
        throw 'Full requires Windows PowerShell, Connection usb, and Backend system.'
    }
    if ($Suite -eq 'full' -and (Get-FullPath -Path $Python -BaseRoot $repoRoot) -ine (Join-Path $repoRoot '.venv\Scripts\python.exe')) {
        throw 'Full requires the project Python environment (.venv/Scripts/python.exe).'
    }
    if ($Suite -eq 'full') { $gitHead = Assert-FullGitRevision -ProjectRoot $repoRoot }
    $run = New-ValidationRunDirectory -RepoRoot $repoRoot -BaseRoot $OutputRoot
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}

$Python = Get-FullPath -Path $Python -BaseRoot $repoRoot
$cases = [System.Collections.ArrayList]::new()
$channelsTested = [System.Collections.ArrayList]::new()
$expectedModel = [string]$targetProfile.model_id
$targetCapabilityModel = [string]$targetProfile.model
$privateReportPath = Join-Path $run.Private "report.json"
$privateSummaryPath = Join-Path $run.Private "summary.md"
$report = [pscustomobject][ordered]@{
    suite = $Suite
    schema_version = 1
    kind = "cli-live-check"
    status = "FAIL"
    target = $expectedModel
    expected_model = $expectedModel
    target_capability_model = $targetCapabilityModel
    reference_environment = if ($Suite -eq 'full') { 'Windows PowerShell; project Python; explicit USB resource; system VISA' } else { $null }
    connection = $connectionName
    backend = $backendName
    resource = $Resource
    channel_count = $null
    channels_planned = @()
    channels_tested = @()
    package_version = Get-PackageVersion -ProjectRoot $repoRoot
    git_head = if ($Suite -eq 'full') { $gitHead } else { Get-GitHead -ProjectRoot $repoRoot }
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    validation_mode = if ($PlanOnly) { "live-plan-only" } else { "live" }
    hardware_touched = $false
    visa_io_performed = $false
    resource_scan_performed = $false
    resource_guess_performed = $false
    planned_cases = @()
    cases = @()
    summary_counts = [ordered]@{ passed = 0; failed = 0; not_applicable = 0; total = 0 }
    artifact_paths = [ordered]@{
        private_report = $privateReportPath
        private_summary = $privateSummaryPath
        shareable_report = Join-Path $run.Shareable "report.json"
        shareable_summary = Join-Path $run.Shareable "summary.md"
    }
}

if ($Suite -eq 'full') { $report | Add-Member -NotePropertyName git_clean -NotePropertyValue $true }
$powershellPath = (Get-Process -Id $PID).Path
$preflightDirectory = Join-Path $run.Private "preflight"
New-Item -ItemType Directory -Force -Path $preflightDirectory | Out-Null
$preflightProcess = $null
try {
    $preflightProcess = Invoke-CapturedCommand `
        -Name "preflight" `
        -FilePath $powershellPath `
        -Arguments @(
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", (Join-Path $PSScriptRoot "preflight-cli.ps1"),
            "-Target", $expectedModel,
            "-Suite", $Suite,
            "-Python", $Python,
            "-OutputRoot", (Join-Path $OutputRoot "_preflight")
        ) `
        -StdOutPath (Join-Path $preflightDirectory "stdout.txt") `
        -StdErrPath (Join-Path $preflightDirectory "stderr.txt") `
        -WorkingDirectory $repoRoot
    if ($preflightProcess.exit_code -eq 0) {
        Add-LiveCase -Cases $cases -Name "preflight" -Status PASS -Process $preflightProcess
    } else {
        Add-LiveCase `
            -Cases $cases `
            -Name "preflight" `
            -Status FAIL `
            -FailureReasons "Preflight exited with code $($preflightProcess.exit_code)." `
            -Process $preflightProcess
    }
} catch {
    Add-LiveCase `
        -Cases $cases `
        -Name "preflight" `
        -Status FAIL `
        -FailureReasons $_.Exception.Message
}

if (@($cases | Where-Object { $_.name -eq "preflight" -and $_.status -eq "FAIL" }).Count -gt 0) {
    $report.cases = @($cases)
    $report.summary_counts = Get-SummaryCounts -Cases $cases
    $published = Publish-LiveArtifacts -Report $report -Run $run -PrivateReportPath $privateReportPath -PrivateSummaryPath $privateSummaryPath
    Write-CompletionSummary -Result FAIL -Run $run -ShareableAvailable $published
    exit 1
}

$capabilityInvocation = $null
$capabilityReasons = [System.Collections.ArrayList]::new()
$channelCount = $null
try {
    $capabilityInvocation = Invoke-WavegenJson `
        -Name "capabilities" `
        -CliArguments @("capabilities", "--model", $expectedModel, "--json") `
        -ArtifactDirectory (Join-Path $run.Private "capabilities")
    if ($capabilityInvocation.Process.exit_code -ne 0) {
        [void]$capabilityReasons.Add(
            "Command exited with code $($capabilityInvocation.Process.exit_code)."
        )
    } elseif ($null -ne $capabilityInvocation.ParseError) {
        [void]$capabilityReasons.Add("Invalid JSON output: $($capabilityInvocation.ParseError)")
    } else {
        $payload = $capabilityInvocation.Payload
        if ($payload.event -cne "capabilities") { [void]$capabilityReasons.Add("Unexpected capabilities event.") }
        if ($payload.selection.requested_model -cne $expectedModel) { [void]$capabilityReasons.Add("Requested model did not match the target.") }
        if ($payload.model.model_id -cne $expectedModel) { [void]$capabilityReasons.Add("Capability model ID did not match the target.") }
        if ($payload.model.canonical_model -cne $targetCapabilityModel) { [void]$capabilityReasons.Add("Capability profile model was unexpected.") }
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
    Add-LiveCase -Cases $cases -Name "capabilities" -Status PASS -Process $capabilityInvocation.Process
} else {
    Add-LiveCase `
        -Cases $cases `
        -Name "capabilities" `
        -Status FAIL `
        -FailureReasons @($capabilityReasons) `
        -Process $(if ($null -ne $capabilityInvocation) { $capabilityInvocation.Process } else { $null })
    Add-LiveCase `
        -Cases $cases `
        -Name "channel-live-validation" `
        -Status "N/A" `
        -FailureReasons "Authoritative channel_count is unavailable."
    $report.cases = @($cases)
    $report.summary_counts = Get-SummaryCounts -Cases $cases
    $published = Publish-LiveArtifacts `
        -Report $report `
        -Run $run `
        -PrivateReportPath $privateReportPath `
        -PrivateSummaryPath $privateSummaryPath
    Write-CompletionSummary -Result FAIL -Run $run -ShareableAvailable $published
    exit 1
}

$channels = @(1..$channelCount)
$plannedCases = @(Get-PlannedLiveCases -Channels $channels)
$report.channel_count = $channelCount
$report.channels_planned = $channels
$report.planned_cases = $plannedCases
if ($Suite -eq "full") { $report | Add-Member -NotePropertyName full_inventory -NotePropertyValue @(Get-FullValidationInventory -Channels $channels) }
$resourceDisplay = if ($PlanOnly) {
    Protect-ArtifactText `
        -Text $Resource `
        -Resource $Resource `
        -RepoRoot $repoRoot `
        -PrivateRoot $run.Private
} else {
    $Resource
}

Write-Host ""
Write-Host "Wavegen Tool Live CLI Validation"
Write-Host ""
Write-Host "Target: $expectedModel"
Write-Host "Suite: $Suite"
if ($Suite -eq "full") { Write-Host "Reference environment: $($report.reference_environment)" }
Write-Host "Connection: $connectionName"
Write-Host "Backend: $backendName"
Write-Host "Resource: $resourceDisplay"
Write-Host "Channels: $($channels -join ', ')"
Write-Host ""
Write-Host "Validation cases:"
foreach ($caseName in $plannedCases) {
    Write-Host "  $caseName"
}
Write-Host ""
Write-Host "Representative waveform:"
Write-Host "  Function: Sine"
Write-Host "  Frequency: 1000 Hz"
Write-Host "  Amplitude: 0.1 Vpp"
Write-Host "  Offset: 0 V"
Write-Host "  Phase: 0 deg"
Write-Host "  Load: 50 ohm"
Write-Host ""
Write-Host "Safety:"
if ($Suite -eq 'full') {
    Write-Host "  - Only dedicated output-control cases briefly use Output ON in Real Full."
} else {
    Write-Host "  - Output ON will not be used."
}
Write-Host "  - Each exercised channel is forced OFF before configuration."
Write-Host "  - The runner commands each exercised channel OFF after validation."
Write-Host "  - Best-effort OFF cleanup is attempted after failures."
Write-Host "  - No resource scanning or guessing is performed."
Write-Host "  - No reset, preset or recall is performed."
Write-Host "  - Coupling or tracking is not changed automatically."

if ($PlanOnly) {
    Write-Host ""
    Write-Host "Plan-only status:"
    Write-Host "  hardware_touched=false"
    Write-Host "  visa_io_performed=false"
    Write-Host "  No VISA session is opened and no Live SCPI is sent."
    $report.status = "PASS"
    $report.cases = @($cases)
    $report.summary_counts = Get-SummaryCounts -Cases $cases
    $published = Publish-LiveArtifacts `
        -Report $report `
        -Run $run `
        -PrivateReportPath $privateReportPath `
        -PrivateSummaryPath $privateSummaryPath
    if (-not $published -or $report.status -ne 'PASS') {
        Write-CompletionSummary -Result FAIL -Run $run -ShareableAvailable $published
        exit 1
    }
    Write-CompletionSummary -Result PLANNED -Run $run
    exit 0
}

Write-Host ""
$confirmation = $null
if ([Console]::IsInputRedirected) {
    Write-Host "Live validation requires interactive confirmation; redirected stdin is rejected."
} else {
    $confirmation = Read-Host "Type YES to begin Live validation, or Ctrl+C to cancel"
}
if ($confirmation -cne "YES") {
    $report.status = "CANCELLED"
    $report.cases = @($cases)
    $report.summary_counts = Get-SummaryCounts -Cases $cases
    $published = Publish-LiveArtifacts `
        -Report $report `
        -Run $run `
        -PrivateReportPath $privateReportPath `
        -PrivateSummaryPath $privateSummaryPath
    if (-not $published) {
        Write-CompletionSummary -Result FAIL -Run $run -ShareableAvailable $false
        exit 1
    }
    Write-CompletionSummary -Result CANCELLED -Run $run
    exit 2
}

$report.hardware_touched = $true
$report.visa_io_performed = $true
$liveCommon = @(
    "--validation-allow-pending-live-support",
    "--model", $expectedModel,
    "--resource", $Resource,
    "--backend", $backendName,
    "--json"
)
$identityPassed = $false
$stopChannels = $false
$detectedSensitiveValues = @()

$identityReasons = [System.Collections.ArrayList]::new()
$identityInvocation = $null
try {
    $identityInvocation = Invoke-WavegenJson `
        -Name "identity" `
        -CliArguments (@("identify") + $liveCommon) `
        -ArtifactDirectory (Join-Path $run.Private "identity")
    if ($identityInvocation.Process.exit_code -ne 0) {
        [void]$identityReasons.Add("Command exited with code $($identityInvocation.Process.exit_code).")
    } elseif ($null -ne $identityInvocation.ParseError) {
        [void]$identityReasons.Add("Invalid JSON output: $($identityInvocation.ParseError)")
    } else {
        $payload = $identityInvocation.Payload
        if ($payload.success -ne $true) { [void]$identityReasons.Add("Identity command did not succeed.") }
        if ($payload.model_supported -ne $true) { [void]$identityReasons.Add("Detected model was not admitted for validation.") }
        if ($payload.canonical_model_id -cne $expectedModel) { [void]$identityReasons.Add("Detected model did not exactly match the expected target.") }
        if ([string]$payload.model -cne $targetCapabilityModel) { [void]$identityReasons.Add("Detected canonical model was unexpected.") }
    }
} catch {
    [void]$identityReasons.Add($_.Exception.Message)
}

if ($identityReasons.Count -eq 0) {
    $identityPassed = $true
    Add-LiveCase -Cases $cases -Name "identity" -Status PASS -Process $identityInvocation.Process
    $report | Add-Member -NotePropertyName detected_model -NotePropertyValue $identityInvocation.Payload.model -Force
    $report | Add-Member -NotePropertyName detected_model_id -NotePropertyValue $identityInvocation.Payload.canonical_model_id -Force
    $report | Add-Member -NotePropertyName detected_identity -NotePropertyValue ([ordered]@{
        manufacturer = $identityInvocation.Payload.manufacturer
        model = $identityInvocation.Payload.model
        serial = $identityInvocation.Payload.serial
        firmware = $identityInvocation.Payload.firmware
    }) -Force
    if (-not [string]::IsNullOrWhiteSpace([string]$identityInvocation.Payload.serial)) {
        $detectedSensitiveValues = @([string]$identityInvocation.Payload.serial)
    }
} else {
    Add-LiveCase `
        -Cases $cases `
        -Name "identity" `
        -Status FAIL `
        -FailureReasons @($identityReasons) `
        -Process $(if ($null -ne $identityInvocation) { $identityInvocation.Process } else { $null })
    Add-LiveCase -Cases $cases -Name "baseline-error-drain" -Status "N/A" -FailureReasons "Identity did not pass."
    Add-RemainingChannelCases -Cases $cases -Channels $channels -Reason "Identity did not pass."
    if ($Suite -eq 'full') {
        foreach ($channel in $channels) {
            foreach ($stage in @('initial-safe-off', 'final-safe-off')) {
                Add-LiveCase -Cases $cases -Name "ch${channel}/$stage" -Status 'N/A' -FailureReasons 'Identity did not pass; writes are not admitted.'
            }
        }
    }
    Add-LiveCase -Cases $cases -Name "final-error-queue" -Status "N/A" -FailureReasons "Identity did not pass."
}

if ($identityPassed) {
    $baselineReasons = [System.Collections.ArrayList]::new()
    $baselineInvocation = $null
    try {
        $baselineInvocation = Invoke-WavegenJson `
            -Name "baseline-error-drain" `
            -CliArguments (@("read-errors") + $liveCommon) `
            -ArtifactDirectory (Join-Path $run.Private "baseline-error-drain")
        if ($baselineInvocation.Process.exit_code -ne 0) {
            [void]$baselineReasons.Add("Command exited with code $($baselineInvocation.Process.exit_code).")
        } elseif ($null -ne $baselineInvocation.ParseError) {
            [void]$baselineReasons.Add("Invalid JSON output: $($baselineInvocation.ParseError)")
        } else {
            $payload = $baselineInvocation.Payload
            if ($payload.success -ne $true -or $payload.action -cne "read-errors") { [void]$baselineReasons.Add("Error drain command did not succeed.") }
            if ([string]$payload.model -cne $targetCapabilityModel) { [void]$baselineReasons.Add("Detected model changed during baseline drain.") }
            if ($payload.empty_confirmed -ne $true -or $payload.limit_reached -ne $false) { [void]$baselineReasons.Add("Error queue was not drained to a confirmed empty state.") }
        }
    } catch {
        [void]$baselineReasons.Add($_.Exception.Message)
    }
    if ($baselineReasons.Count -eq 0) {
        Add-LiveCase -Cases $cases -Name "baseline-error-drain" -Status PASS -Process $baselineInvocation.Process
    } else {
        Add-LiveCase `
            -Cases $cases `
            -Name "baseline-error-drain" `
            -Status FAIL `
            -FailureReasons @($baselineReasons) `
            -Process $(if ($null -ne $baselineInvocation) { $baselineInvocation.Process } else { $null })
        if ($Suite -eq "smoke") { Add-RemainingChannelCases -Cases $cases -Channels $channels -Reason "Baseline error drain did not pass." }
        $stopChannels = $true
    }

    if ($Suite -eq 'full') {
        try { Invoke-FullLiveCases -Channels $channels -CanRun (-not $stopChannels) }
        catch { Add-LiveCase -Cases $cases -Name 'full-runner' -Status FAIL -FailureReasons $_.Exception.Message }
    }
    if (-not $stopChannels -and $Suite -eq 'smoke') {
        for ($channelIndex = 0; $channelIndex -lt $channels.Count; $channelIndex += 1) {
            $channel = $channels[$channelIndex]
            [void]$channelsTested.Add($channel)
            $channelFailed = $false
            $channelArguments = @("--channel", [string]$channel) + $liveCommon

            $beforeReasons = [System.Collections.ArrayList]::new()
            $beforeInvocation = $null
            try {
                $beforeInvocation = Invoke-WavegenJson `
                    -Name "ch${channel}/output-off-before" `
                    -CliArguments (@("output", "--state", "off") + $channelArguments) `
                    -ArtifactDirectory (Join-Path $run.Private "ch${channel}-output-off-before")
                if ($beforeInvocation.Process.exit_code -ne 0) { [void]$beforeReasons.Add("Command exited with code $($beforeInvocation.Process.exit_code).") }
                elseif ($null -ne $beforeInvocation.ParseError) { [void]$beforeReasons.Add("Invalid JSON output: $($beforeInvocation.ParseError)") }
                else {
                    $payload = $beforeInvocation.Payload
                    if ($payload.success -ne $true -or $payload.action -cne "output") { [void]$beforeReasons.Add("Output-off command did not succeed.") }
                    if ([string]$payload.model -cne $targetCapabilityModel) { [void]$beforeReasons.Add("Detected model changed.") }
                    if ([int]$payload.channel -ne $channel -or [string]$payload.output_state -cne "off") { [void]$beforeReasons.Add("Selected channel was not confirmed off.") }
                }
            } catch { [void]$beforeReasons.Add($_.Exception.Message) }
            Add-LiveCase `
                -Cases $cases `
                -Name "ch${channel}/output-off-before" `
                -Status $(if ($beforeReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
                -FailureReasons @($beforeReasons) `
                -Process $(if ($null -ne $beforeInvocation) { $beforeInvocation.Process } else { $null })
            if ($beforeReasons.Count -gt 0) { $channelFailed = $true }

            if (-not $channelFailed) {
                $configReasons = [System.Collections.ArrayList]::new()
                $configInvocation = $null
                try {
                    $configInvocation = Invoke-WavegenJson `
                        -Name "ch${channel}/sine-config" `
                        -CliArguments (@(
                            "configure-sine",
                            "--frequency-hz", "1000",
                            "--amplitude-vpp", "0.1",
                            "--offset-v", "0",
                            "--phase-deg", "0",
                            "--load", "50"
                        ) + $channelArguments) `
                        -ArtifactDirectory (Join-Path $run.Private "ch${channel}-sine-config")
                    if ($configInvocation.Process.exit_code -ne 0) { [void]$configReasons.Add("Command exited with code $($configInvocation.Process.exit_code).") }
                    elseif ($null -ne $configInvocation.ParseError) { [void]$configReasons.Add("Invalid JSON output: $($configInvocation.ParseError)") }
                    else {
                        $payload = $configInvocation.Payload
                        if ($payload.success -ne $true -or $payload.action -cne "configure-sine") { [void]$configReasons.Add("Sine configuration did not succeed.") }
                        if ([string]$payload.model -cne $targetCapabilityModel) { [void]$configReasons.Add("Detected model changed.") }
                        if ([int]$payload.channel -ne $channel -or [string]$payload.output_state -cne "off") { [void]$configReasons.Add("Configuration did not leave the selected channel off.") }
                        if (-not (Test-CloseNumber -Actual ([double]$payload.frequency_hz) -Expected 1000.0)) { [void]$configReasons.Add("Configured frequency was unexpected.") }
                        if (-not (Test-CloseNumber -Actual ([double]$payload.amplitude_vpp) -Expected 0.1)) { [void]$configReasons.Add("Configured amplitude was unexpected.") }
                        if (-not (Test-CloseNumber -Actual ([double]$payload.offset_v) -Expected 0.0)) { [void]$configReasons.Add("Configured offset was unexpected.") }
                        if (-not (Test-CloseNumber -Actual ([double]$payload.phase_deg) -Expected 0.0)) { [void]$configReasons.Add("Configured phase was unexpected.") }
                        if ([string]$payload.load -cne "50") { [void]$configReasons.Add("Configured load was unexpected.") }
                    }
                } catch { [void]$configReasons.Add($_.Exception.Message) }
                Add-LiveCase `
                    -Cases $cases `
                    -Name "ch${channel}/sine-config" `
                    -Status $(if ($configReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
                    -FailureReasons @($configReasons) `
                    -Process $(if ($null -ne $configInvocation) { $configInvocation.Process } else { $null })
                if ($configReasons.Count -gt 0) { $channelFailed = $true }
            } else {
                Add-LiveCase -Cases $cases -Name "ch${channel}/sine-config" -Status "N/A" -FailureReasons "Output-off-before did not pass."
            }

            if (-not $channelFailed) {
                $readbackReasons = [System.Collections.ArrayList]::new()
                $readbackInvocation = $null
                try {
                    $readbackInvocation = Invoke-WavegenJson `
                        -Name "ch${channel}/sine-readback" `
                        -CliArguments (@("status") + $channelArguments) `
                        -ArtifactDirectory (Join-Path $run.Private "ch${channel}-sine-readback")
                    if ($readbackInvocation.Process.exit_code -ne 0) { [void]$readbackReasons.Add("Command exited with code $($readbackInvocation.Process.exit_code).") }
                    elseif ($null -ne $readbackInvocation.ParseError) { [void]$readbackReasons.Add("Invalid JSON output: $($readbackInvocation.ParseError)") }
                    else {
                        $payload = $readbackInvocation.Payload
                        if ($payload.success -ne $true -or $payload.action -cne "status") { [void]$readbackReasons.Add("Status command did not succeed.") }
                        if ([string]$payload.model -cne $targetCapabilityModel) { [void]$readbackReasons.Add("Detected model changed.") }
                        if ([int]$payload.channel -ne $channel) { [void]$readbackReasons.Add("Status channel did not match.") }
                        if ([string]$payload.output_state -cne "off") { [void]$readbackReasons.Add("Status did not report output off.") }
                        if ([string]$payload.function -cne "SIN") { [void]$readbackReasons.Add("Status did not report sine.") }
                        if (-not (Test-CloseNumber -Actual ([double]$payload.frequency_hz) -Expected 1000.0 -AbsoluteTolerance 0.001 -RelativeTolerance 0.000001)) { [void]$readbackReasons.Add("Frequency readback was outside tolerance.") }
                        if (-not (Test-CloseNumber -Actual ([double]$payload.amplitude) -Expected 0.1 -AbsoluteTolerance 0.000001 -RelativeTolerance 0.0001)) { [void]$readbackReasons.Add("Amplitude readback was outside tolerance.") }
                        if ([string]$payload.amplitude_unit -cne "VPP") { [void]$readbackReasons.Add("Amplitude unit was not VPP.") }
                        if (-not (Test-CloseNumber -Actual ([double]$payload.offset_v) -Expected 0.0 -AbsoluteTolerance 0.000001)) { [void]$readbackReasons.Add("Offset readback was outside tolerance.") }
                        if ([string]$payload.load -cne "50") { [void]$readbackReasons.Add("Load readback did not match 50 ohm.") }
                    }
                } catch { [void]$readbackReasons.Add($_.Exception.Message) }
                Add-LiveCase `
                    -Cases $cases `
                    -Name "ch${channel}/sine-readback" `
                    -Status $(if ($readbackReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
                    -FailureReasons @($readbackReasons) `
                    -Process $(if ($null -ne $readbackInvocation) { $readbackInvocation.Process } else { $null })
                if ($readbackReasons.Count -gt 0) { $channelFailed = $true }
            } else {
                Add-LiveCase -Cases $cases -Name "ch${channel}/sine-readback" -Status "N/A" -FailureReasons "Sine configuration did not pass."
            }

            $afterReasons = [System.Collections.ArrayList]::new()
            $afterInvocation = $null
            try {
                $afterInvocation = Invoke-WavegenJson `
                    -Name "ch${channel}/output-off-after" `
                    -CliArguments (@("output", "--state", "off") + $channelArguments) `
                    -ArtifactDirectory (Join-Path $run.Private "ch${channel}-output-off-after")
                if ($afterInvocation.Process.exit_code -ne 0) { [void]$afterReasons.Add("Command exited with code $($afterInvocation.Process.exit_code).") }
                elseif ($null -ne $afterInvocation.ParseError) { [void]$afterReasons.Add("Invalid JSON output: $($afterInvocation.ParseError)") }
                else {
                    $payload = $afterInvocation.Payload
                    if ($payload.success -ne $true -or $payload.action -cne "output") { [void]$afterReasons.Add("Final output-off command did not succeed.") }
                    if ([int]$payload.channel -ne $channel -or [string]$payload.output_state -cne "off") { [void]$afterReasons.Add("Selected channel was not confirmed off.") }
                }
            } catch { [void]$afterReasons.Add($_.Exception.Message) }
            Add-LiveCase `
                -Cases $cases `
                -Name "ch${channel}/output-off-after" `
                -Status $(if ($afterReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
                -FailureReasons @($afterReasons) `
                -Process $(if ($null -ne $afterInvocation) { $afterInvocation.Process } else { $null })
            if ($afterReasons.Count -gt 0) { $channelFailed = $true }

            if ($channelFailed) {
                foreach ($cleanupChannel in @($channelsTested)) {
                    $cleanupReasons = [System.Collections.ArrayList]::new()
                    $cleanupInvocation = $null
                    try {
                        $cleanupInvocation = Invoke-WavegenJson `
                            -Name "ch${cleanupChannel}/cleanup-output-off" `
                            -CliArguments (@(
                                "output", "--state", "off", "--channel", [string]$cleanupChannel
                            ) + $liveCommon) `
                            -ArtifactDirectory (Join-Path $run.Private "ch${cleanupChannel}-cleanup-output-off")
                        if ($cleanupInvocation.Process.exit_code -ne 0) { [void]$cleanupReasons.Add("Cleanup exited with code $($cleanupInvocation.Process.exit_code).") }
                        elseif ($null -ne $cleanupInvocation.ParseError) { [void]$cleanupReasons.Add("Invalid cleanup JSON output: $($cleanupInvocation.ParseError)") }
                        elseif (
                            $cleanupInvocation.Payload.success -ne $true -or
                            $cleanupInvocation.Payload.action -cne "output" -or
                            [int]$cleanupInvocation.Payload.channel -ne $cleanupChannel -or
                            [string]$cleanupInvocation.Payload.output_state -cne "off"
                        ) { [void]$cleanupReasons.Add("Cleanup did not confirm the selected channel off.") }
                    } catch { [void]$cleanupReasons.Add($_.Exception.Message) }
                    Add-LiveCase `
                        -Cases $cases `
                        -Name "ch${cleanupChannel}/cleanup-output-off" `
                        -Status $(if ($cleanupReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
                        -FailureReasons @($cleanupReasons) `
                        -Process $(if ($null -ne $cleanupInvocation) { $cleanupInvocation.Process } else { $null })
                }
                $remainingChannels = @($channels | Where-Object { $_ -gt $channel })
                if ($remainingChannels.Count -gt 0) {
                    Add-RemainingChannelCases `
                        -Cases $cases `
                        -Channels $remainingChannels `
                        -Reason "A prior channel failed; unexercised channels were skipped."
                }
                break
            }
        }
    }

    $finalReasons = [System.Collections.ArrayList]::new()
    $finalInvocation = $null
    try {
        $finalInvocation = Invoke-WavegenJson `
            -Name "final-error-queue" `
            -CliArguments (@("read-errors") + $liveCommon) `
            -ArtifactDirectory (Join-Path $run.Private "final-error-queue")
        if ($finalInvocation.Process.exit_code -ne 0) { [void]$finalReasons.Add("Command exited with code $($finalInvocation.Process.exit_code).") }
        elseif ($null -ne $finalInvocation.ParseError) { [void]$finalReasons.Add("Invalid JSON output: $($finalInvocation.ParseError)") }
        else {
            $payload = $finalInvocation.Payload
            if ($payload.success -ne $true -or $payload.action -cne "read-errors") { [void]$finalReasons.Add("Final error queue command did not succeed.") }
            if ([string]$payload.model -cne $targetCapabilityModel) { [void]$finalReasons.Add("Detected model changed during final diagnostics.") }
            if ($payload.empty_confirmed -ne $true -or $payload.limit_reached -ne $false) { [void]$finalReasons.Add("Final error queue was not confirmed empty.") }
            if ($payload.has_errors -ne $false) { [void]$finalReasons.Add("Validation left instrument errors in the final queue result.") }
        }
    } catch { [void]$finalReasons.Add($_.Exception.Message) }
    Add-LiveCase `
        -Cases $cases `
        -Name "final-error-queue" `
        -Status $(if ($finalReasons.Count -eq 0) { "PASS" } else { "FAIL" }) `
        -FailureReasons @($finalReasons) `
        -Process $(if ($null -ne $finalInvocation) { $finalInvocation.Process } else { $null })
}

$report.channels_tested = @($channelsTested)
$report.cases = @($cases)
$report.summary_counts = Get-SummaryCounts -Cases $cases
$report.status = if ($Suite -eq 'full') {
    if (Test-FullValidationPass -Cases @($cases) -RequiredNames $plannedCases) { 'PASS' } else { 'FAIL' }
} elseif ($report.summary_counts.failed -eq 0) { 'PASS' } else { 'FAIL' }
$published = Publish-LiveArtifacts `
    -Report $report `
    -Run $run `
    -PrivateReportPath $privateReportPath `
    -PrivateSummaryPath $privateSummaryPath `
    -ExtraSensitiveValues $detectedSensitiveValues
if (-not $published) {
    $report.status = "FAIL"
}

Write-CompletionSummary `
    -Result $report.status `
    -Run $run `
    -ShareableAvailable $published
if ($report.status -eq "PASS") {
    exit 0
}
exit 1
