[CmdletBinding()]
param(
    [ValidateSet("smoke", "full")][string]$Suite = "smoke",
    [string[]]$Modes = @(),
    [string]$Target = "all",
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$DebugArtifacts,
    [string]$OutputRoot = ".tmp_tests\cli_preflight",
    [switch]$IncludeBurstStartPhase
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
. (Join-Path $PSScriptRoot "_validation_helpers.ps1")
. (Join-Path $PSScriptRoot "_artifact_privacy.ps1")

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

function Get-ValidationSummary {
    param(
        [Parameter(Mandatory = $true)]$Report
    )

    $lines = @(
        "# CLI Preflight Summary",
        "",
        "- Result: $($Report.status)",
        "- Suite: $($Report.suite)",
        "- Selected modes: $($Report.selected_modes -join ', ')",
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
    return ($lines -join "`n") + "`n"
}

try {
    $modesSpecified = $PSBoundParameters.ContainsKey("Modes")
    $selectedModes = @(Resolve-SelectedValidationModes -Suite $Suite -Modes $Modes -Specified $modesSpecified -IncludeBurstStartPhase:$IncludeBurstStartPhase)
    $targets = @(Resolve-ValidationTargets -Target $Target)
    if ($Suite -eq 'full' -and ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSEdition -ne 'Desktop')) {
        throw 'Full requires Windows PowerShell Desktop on Windows.'
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

    if ($Suite -eq 'full') {
        try {
            $applicableModes = @(Get-ApplicableValidationModes -Modes $selectedModes -Capabilities $payload.capabilities -RejectUnsupportedArbitrary:($modesSpecified -and $Target -ne 'all'))
        } catch {
            Add-ValidationCase -Cases $cases -Name "${modelId}/mode-admission" -Status FAIL -FailureReasons $_.Exception.Message
            Write-Host ""
            continue
        }
        $fixtureDirectory = Join-Path $targetDirectory 'fixtures'
        $fixtures = if (@($applicableModes | Where-Object { $_ -in @('arbitrary', 'sequence') }).Count -gt 0 -or
            ('software-sequence' -in $applicableModes -and $payload.capabilities.max_arbitrary_sample_rate_hz -gt 0)) {
            New-ArbitraryValidationFixtures -Directory $fixtureDirectory
        } else { $null }
        if ($IncludeBurstStartPhase) {
            $name = "${modelId}/ch1/burst-invalid-start-phase-dry-run"
            $reasons = @()
            $invocation = $null
            try {
                $invocation = Invoke-WavegenJson -Name $name -CliArguments @(
                    'configure-sine', '--frequency-hz', '1000', '--amplitude-vpp', '0.1',
                    '--gated-burst', '--burst-start-phase-deg', '45.15', '--dry-run', '--model', $modelId, '--json'
                ) -ArtifactDirectory (Join-Path $targetDirectory (New-SafeCaseName $name))
                if ($invocation.Process.exit_code -ne 2 -or $null -ne $invocation.ParseError -or
                    $invocation.Payload.success -ne $false -or $invocation.Payload.error -cnotlike 'waveform_parameter_error: Burst start phase*') {
                    throw 'Invalid Burst start phase was not rejected as a parameter error.'
                }
            } catch { $reasons += $_.Exception.Message }
            Add-ValidationCase -Cases $cases -Name $name -Status $(if ($reasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $reasons -Process $(if ($null -ne $invocation) { $invocation.Process } else { $null })
        }
        foreach ($recipe in @(Get-FullValidationInventory -Channels @(1..$channelCount) -Modes $applicableModes -ArbitraryFixtures $fixtures -SequenceFixtureDirectory $fixtureDirectory -IncludeBurstStartPhase:$IncludeBurstStartPhase)) {
            if ($recipe.channel -notin $channelsTested) { [void]$channelsTested.Add($recipe.channel) }
            $stepIndex = 0
            foreach ($step in $recipe.steps) {
                $stepIndex += 1
                $command = $step.arguments[0]
                $usesChannel = Test-ValidationCommandUsesChannel $command
                # Each simulator invocation starts fresh. Built-in configure can
                # load its own segment; ordinary Arbitrary configure needs a prior upload.
                $executionModes = if ($command -in @('configure-arbitrary', 'configure-arbitrary-sequence') -and
                    '--load-builtin' -notin $step.arguments) {
                    @('dry-run')
                } elseif ($recipe.family -eq 'basic-workflows' -or $command.StartsWith('configure-') -or $command -in @('upload-arbitrary', 'list-arbitrary-memory', 'clear-arbitrary-memory', 'run-software-sequence')) {
                    @('dry-run', 'simulate')
                } else { @('simulate') }
                foreach ($mode in $executionModes) {
                    $name = "${modelId}/$($recipe.name)/step${stepIndex}-$mode"
                    $reasons = @()
                    $invocation = $null
                    try {
                        $args = @($step.arguments) + @("--$mode", '--model', $modelId, '--json')
                        if ($usesChannel) { $args += @('--channel', [string]$recipe.channel) }
                        $invocation = Invoke-WavegenJson -Name $name -CliArguments $args -ArtifactDirectory (Join-Path $targetDirectory (New-SafeCaseName $name))
                        if ($invocation.Process.exit_code -ne 0) {
                            $detail = $invocation.Process['cli_error']
                            throw "Command exited with code $($invocation.Process.exit_code). $detail".TrimEnd()
                        }
                        if ($null -ne $invocation.ParseError) { throw $invocation.ParseError }
                        $payload = $invocation.Payload
                        if ($payload.success -ne $true -or $payload.action -cne $command -or $payload.mode -cne $mode) { throw 'Unexpected command result.' }
                        if ($usesChannel -and [int]$payload.channel -ne $recipe.channel) { throw 'Channel did not match the recipe.' }
                        if ($command -eq 'run-software-sequence') {
                            Assert-SoftwareSequenceResult -Payload $payload -Intent $step.sequence_intent -Channel $recipe.channel -DryRun:($mode -eq 'dry-run')
                        } elseif ($recipe.family -eq 'basic-workflows') {
                            Assert-BasicWorkflowResult -Payload $payload -Intent $step.workflow_intent -Channel $recipe.channel -DryRun:($mode -eq 'dry-run')
                        }
                        if ($mode -eq 'dry-run') {
                            if ($payload.executed -ne $false -or $payload.canonical_model_id -cne $modelId -or
                                ($command -ne 'run-software-sequence' -and $recipe.family -ne 'basic-workflows' -and @($payload.commands).Count -eq 0)) { throw 'Invalid hardware-free plan.' }
                        } elseif ($payload.simulated -ne $true -or $payload.model -cne $capabilityModel) { throw 'Unexpected simulator identity or mode.' }
                        if ($command -in @('configure-sync', 'sync-output')) {
                            foreach ($key in $step.result.Keys) {
                                if ($mode -eq 'dry-run' -and $key -eq 'readback_verified') { continue }
                                if ($key -notin $payload.PSObject.Properties.Name -or $payload.$key -cne $step.result[$key]) {
                                    throw "Unexpected Sync result for $key."
                                }
                            }
                        } elseif ($command.StartsWith('configure-') -and $payload.output_state -cne 'off') {
                            throw 'Configuration did not leave output off.'
                        }
                        if ($step.result.ContainsKey('burst_start_phase_deg') -and (
                            'burst_start_phase_deg' -notin $payload.PSObject.Properties.Name -or
                            $payload.burst_start_phase_deg -ne $step.result['burst_start_phase_deg']
                        )) {
                            throw 'Burst start phase did not match the recipe.'
                        }
                        if ($recipe.family -eq 'sweep' -and $step.result['trigger_source'] -eq 'external') {
                            if ($payload.trigger_source -cne 'external' -or $payload.trigger_slope -cne $step.result['trigger_slope']) {
                                throw 'External Sweep trigger source or slope did not match the recipe.'
                            }
                        }
                        if ('--verify-readback' -in $step.arguments -and $mode -eq 'simulate' -and $payload.readback_verified -ne $true) {
                            throw 'Advanced Mode simulator readback was not verified.'
                        }
                        if ($mode -eq 'dry-run' -and 'readback_verified' -in $payload.PSObject.Properties.Name) {
                            throw 'Dry-run cannot claim independent readback verification.'
                        }
                    } catch { $reasons += $_.Exception.Message }
                    Add-ValidationCase -Cases $cases -Name $name -Status $(if ($reasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $reasons -Process $(if ($null -ne $invocation) { $invocation.Process } else { $null })
                }
            }
        }
        if ('basic-workflows' -in $applicableModes) {
            $name = "${modelId}/basic-workflows-worker-lifecycle"
            $reasons = @()
            $process = $null
            try {
                $artifactDirectory = Join-Path $targetDirectory (New-SafeCaseName $name)
                $process = Invoke-CapturedCommand -Name $name -FilePath $Python `
                    -Arguments @((Join-Path $PSScriptRoot '_check_basic_worker.py'), $modelId) `
                    -StdOutPath (Join-Path $artifactDirectory 'stdout.jsonl') -StdErrPath (Join-Path $artifactDirectory 'stderr.txt') `
                    -WorkingDirectory $repoRoot -CaptureInMemory:(-not $DebugArtifacts) -TimeoutSeconds 20
                $process.Remove('stdout_text')
                $process.Remove('stderr_text')
                if ($process.exit_code -ne 0) { throw 'Simulator Worker CANCEL or STOP validation failed.' }
            } catch { $reasons += $_.Exception.Message }
            Add-ValidationCase -Cases $cases -Name $name -Status $(if ($reasons.Count) { 'FAIL' } else { 'PASS' }) -FailureReasons $reasons -Process $process
        }
    }

    $smokeChannels = if ($Suite -eq 'smoke') { @(1..$channelCount) } else { @() }
    foreach ($channel in $smokeChannels) {
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
        applicable_modes = $(if ($Suite -eq 'full') { @($applicableModes) } else { @() })
    })
    Write-Host ""
}

$gitClean = $true
if ($Suite -eq 'full') {
    try { $null = Assert-FullGitRevision -ProjectRoot $repoRoot -ExpectedHead $gitHead } catch {
        $gitClean = $false
        Add-ValidationCase -Cases $cases -Name 'git-provenance' -Status FAIL -FailureReasons $_.Exception.Message
    }
}
$summaryCounts = Get-SummaryCounts -Cases $cases
$status = if ($summaryCounts.failed -eq 0 -and ($Suite -ne "full" -or $summaryCounts.not_applicable -eq 0)) { "PASS" } else { "FAIL" }
$report = [pscustomobject][ordered]@{
    suite = $Suite
    selected_modes = if ($Suite -eq 'full') { @($selectedModes) } else { @() }
    complete_mode_selection = $Suite -eq 'full' -and $selectedModes.Count -eq $script:FullValidationModes.Count
    schema_version = 1
    kind = "cli-preflight"
    status = $status
    target = $Target
    targets = $targetIds
    target_results = @($targetResults)
    package_version = Get-PackageVersion -ProjectRoot $repoRoot
    git_head = if ($Suite -eq 'full') { $gitHead } else { Get-GitHead -ProjectRoot $repoRoot }
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    validation_mode = "no-hardware-cli-preflight"
    hardware_touched = $false
    visa_io_performed = $false
    resource_scan_performed = $false
    resource_guess_performed = $false
    cases = @($cases)
    summary_counts = $summaryCounts
    artifact_mode = if ($DebugArtifacts) { 'debug' } else { 'minimal' }
}
if ($Suite -eq 'full') { $report | Add-Member -NotePropertyName git_clean -NotePropertyValue $gitClean }
$published = Publish-ValidationArtifacts -Report $report -Run $run -RepoRoot $repoRoot `
    -SummaryText (Get-ValidationSummary -Report $report) -DebugArtifacts:$DebugArtifacts
Write-Host "Result: $($report.status)"
if (-not $published -or $DebugArtifacts) { Write-Host "Private artifacts: $($run.Private)" }
if ($published) { Write-Host "Shareable artifacts: $($run.Shareable)" }
if ($report.status -eq "PASS") {
    exit 0
}
exit 1
