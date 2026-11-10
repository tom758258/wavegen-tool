Set-StrictMode -Version Latest

$script:ValidationTargetProfiles = @(
    [pscustomobject]@{ model_id = "keysight-33510b"; model = "33510B" },
    [pscustomobject]@{ model_id = "keysight-33512b"; model = "33512B" },
    [pscustomobject]@{ model_id = "keysight-33521b"; model = "33521B" }
)

function Resolve-ValidationTargets {
    param([AllowNull()][AllowEmptyString()][string]$Target = "all")

    $profiles = @($script:ValidationTargetProfiles)
    $seen = @{}
    foreach ($profile in $profiles) {
        $modelId = [string]$profile.model_id
        if (
            [string]::IsNullOrWhiteSpace($modelId) -or
            $modelId -cne $modelId.ToLowerInvariant() -or
            [string]::IsNullOrWhiteSpace([string]$profile.model) -or
            $seen.ContainsKey($modelId)
        ) {
            throw "Invalid validation target profile '$modelId'."
        }
        $seen[$modelId] = $true
    }

    if ([string]::IsNullOrWhiteSpace($Target)) {
        throw "Missing target. Use 'all' or one of: $(@($seen.Keys) -join ', ')."
    }
    $normalized = $Target.Trim().ToLowerInvariant()
    if ($normalized -eq "all") {
        return $profiles
    }
    $matches = @($profiles | Where-Object { $_.model_id -eq $normalized })
    if ($matches.Count -ne 1) {
        throw "Unsupported target '$Target'. Use 'all' or one of: $(@($seen.Keys) -join ', ')."
    }
    return $matches[0]
}

function Resolve-ValidationTarget {
    param([AllowNull()][AllowEmptyString()][string]$Target)

    $targets = @(Resolve-ValidationTargets -Target $Target)
    if ($targets.Count -ne 1) {
        throw "A single canonical target is required."
    }
    return $targets[0]
}

function Get-FullPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$BaseRoot
    )

    if ([System.IO.Path]::IsPathRooted($Path) -or [string]::IsNullOrWhiteSpace($BaseRoot)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BaseRoot $Path))
}

function Assert-PathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Message = "Path is outside the allowed root: '{0}'."
    )

    $rootFull = (Get-FullPath -Path $RootPath).TrimEnd('\', '/')
    $pathFull = Get-FullPath -Path $Path
    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    if ($pathFull.Equals($rootFull, $comparison)) {
        return
    }
    $prefix = $rootFull + [System.IO.Path]::DirectorySeparatorChar
    if (-not $pathFull.StartsWith($prefix, $comparison)) {
        throw ($Message -f $pathFull)
    }
}

function Get-PackageVersion {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $pyproject = Join-Path $ProjectRoot "pyproject.toml"
    $match = Select-String -LiteralPath $pyproject -Pattern '^version\s*=\s*"([^"]+)"' |
        Select-Object -First 1
    if ($null -eq $match) {
        return $null
    }
    return $match.Matches[0].Groups[1].Value
}

function Get-GitHead {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    try {
        $head = & git -C $ProjectRoot rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $head.Trim()
        }
    } catch {
    }
    return $null
}

function Assert-FullGitRevision {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [string]$ExpectedHead
    )

    $head = Get-GitHead -ProjectRoot $ProjectRoot
    if ([string]::IsNullOrWhiteSpace($head)) {
        throw 'Full requires a readable Git HEAD.'
    }
    try {
        $status = @(& git -C $ProjectRoot status --porcelain 2>$null)
        if ($LASTEXITCODE -ne 0) { throw 'Git status failed.' }
    } catch {
        throw 'Full requires a successful Git working-tree status check.'
    }
    if ($status.Count -ne 0) {
        throw 'Full requires a clean Git working tree.'
    }
    if ($ExpectedHead -and $head -cne $ExpectedHead) {
        throw 'Full requires the Git HEAD to remain unchanged during validation.'
    }
    return $head
}

function Write-Utf8NoBomText {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )

    [System.IO.File]::WriteAllText(
        $LiteralPath,
        $Text,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Write-Utf8NoBomLines {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines
    )

    [System.IO.File]::WriteAllLines(
        $LiteralPath,
        $Lines,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Write-JsonReport {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)]$Report,
        [int]$Depth = 20
    )

    Write-Utf8NoBomText -LiteralPath $LiteralPath -Text ($Report | ConvertTo-Json -Depth $Depth)
}

function New-SafeCaseName {
    param([Parameter(Mandatory = $true)][string]$Name)

    return ($Name -replace '[^A-Za-z0-9_.-]', '_')
}

function Write-CaseStatus {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("PASS", "FAIL", "N/A")][string]$Status,
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Context = "",
        [AllowEmptyCollection()][string[]]$FailureReasons = @()
    )

    $prefix = if ([string]::IsNullOrWhiteSpace($Context)) { "" } else { "${Context} " }
    Write-Host ("{0,-5} {1}{2}" -f $Status, $prefix, $Name)
    foreach ($reason in @($FailureReasons)) {
        Write-Host "      failure reason: ${reason}"
    }
}

function ConvertTo-ProcessArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Argument)

    if ($Argument -notmatch '[\s"]' -and $Argument.Length -gt 0) {
        return $Argument
    }
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($char in $Argument.ToCharArray()) {
        if ($char -eq '\') {
            $backslashes += 1
            continue
        }
        if ($char -eq '"') {
            [void]$builder.Append(('\' * ($backslashes * 2 + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($char)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Join-ProcessArguments {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    return (($Arguments | ForEach-Object { ConvertTo-ProcessArgument -Argument $_ }) -join " ")
}

function Stop-CapturedProcess {
    param([System.Diagnostics.Process]$Process, [switch]$KillProcessTree)

    $treeStopped = -not $KillProcessTree
    if ($KillProcessTree) {
        try {
            if ($env:OS -eq 'Windows_NT') {
                # Windows PowerShell lacks Process.Kill(entireProcessTree).
                $stopInfo = [System.Diagnostics.ProcessStartInfo]::new()
                $stopInfo.FileName = Join-Path ([Environment]::SystemDirectory) 'taskkill.exe'
                $stopInfo.Arguments = "/PID $($Process.Id) /T /F"
                $stopInfo.UseShellExecute = $false
                $stopInfo.CreateNoWindow = $true
                $stopInfo.RedirectStandardOutput = $true
                $stopInfo.RedirectStandardError = $true
                $stopper = [System.Diagnostics.Process]::Start($stopInfo)
                try {
                    $stopper.BeginOutputReadLine()
                    $stopper.BeginErrorReadLine()
                    if ($stopper.WaitForExit(5000)) { $treeStopped = $stopper.ExitCode -eq 0 }
                } finally {
                    try { if (-not $stopper.HasExited) { $stopper.Kill() } }
                    finally { $stopper.Dispose() }
                }
            } else {
                $Process.Kill($true)
                $treeStopped = $true
            }
        } catch { $treeStopped = $false }
    }

    # Attempt to stop the direct child even when tree termination fails.
    try { if (-not $Process.HasExited) { $Process.Kill() } } catch { }
    return ($Process.WaitForExit(1000) -and $treeStopped)
}

function Invoke-CapturedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$StdOutPath,
        [Parameter(Mandatory = $true)][string]$StdErrPath,
        [string]$WorkingDirectory = (Get-Location).Path,
        [switch]$CaptureInMemory,
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 30,
        [switch]$KillProcessTree
    )

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $FilePath
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.Arguments = Join-ProcessArguments -Arguments $Arguments

    $process = [System.Diagnostics.Process]::Start($psi)
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $timeoutDetail = $null
    $terminationConfirmed = $true
    try {
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $timeoutMs = $TimeoutSeconds * 1000
        if (-not $process.WaitForExit($timeoutMs)) {
            $timeoutDetail = 'waiting for process exit'
        } else {
            $remainingMs = [int][math]::Max(0, $timeoutMs - $stopwatch.ElapsedMilliseconds)
            $streams = [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask)
            if (-not [System.Threading.Tasks.Task]::WaitAll($streams, $remainingMs)) {
                $timeoutDetail = 'waiting for stdout/stderr to close'
                $terminationConfirmed = $false
            } else {
                $stdout = $stdoutTask.GetAwaiter().GetResult()
                $stderr = $stderrTask.GetAwaiter().GetResult()
                $exitCode = $process.ExitCode
            }
        }
    } finally {
        try {
            if (-not $process.HasExited) {
                $terminationConfirmed = Stop-CapturedProcess -Process $process -KillProcessTree:$KillProcessTree
            }
        } catch {
            $terminationConfirmed = $false
        } finally {
            try {
                $process.StandardOutput.Dispose()
                $process.StandardError.Dispose()
            } finally {
                $process.Dispose()
            }
        }
    }
    if ($timeoutDetail) {
        $message = "$Name timed out after $TimeoutSeconds seconds $timeoutDetail."
        if (-not $terminationConfirmed) { $message += ' Subprocess termination could not be confirmed.' }
        $timeout = [TimeoutException]::new($message)
        $timeout.Data['TerminationUnconfirmed'] = -not $terminationConfirmed
        throw $timeout
    }
    $stopwatch.Stop()

    $result = [ordered]@{
        name = $Name
        exit_code = $exitCode
        duration_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 3)
        success = ($exitCode -eq 0)
    }
    if ($CaptureInMemory) {
        $result.stdout_text = $stdout
        $result.stderr_text = $stderr
    } else {
        foreach ($directory in @((Split-Path -Parent $StdOutPath), (Split-Path -Parent $StdErrPath))) {
            if ($directory) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
        }
        Write-Utf8NoBomText -LiteralPath $StdOutPath -Text $stdout
        Write-Utf8NoBomText -LiteralPath $StdErrPath -Text $stderr
        $result.command = $FilePath
        $result.arguments = @($Arguments)
        $result.stdout = $StdOutPath
        $result.stderr = $StdErrPath
    }
    return $result
}

function Invoke-WavegenJson {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$CliArguments,
        [Parameter(Mandatory = $true)][string]$ArtifactDirectory
    )

    $stdoutPath = Join-Path $ArtifactDirectory 'stdout.json'
    $arguments = @('-c', 'from wavegen_tool_cli.cli import main; raise SystemExit(main())') + $CliArguments
    # A bounded error drain can perform up to 20 queries with the VISA timeout.
    $timeoutSeconds = if ($CliArguments[0] -eq 'read-errors') { 120 } else { 30 }
    $process = Invoke-CapturedCommand -Name $Name -FilePath $Python -Arguments $arguments `
        -StdOutPath $stdoutPath -StdErrPath (Join-Path $ArtifactDirectory 'stderr.txt') `
        -WorkingDirectory $repoRoot -CaptureInMemory:(-not $DebugArtifacts) -TimeoutSeconds $timeoutSeconds
    $raw = if ($DebugArtifacts) { [IO.File]::ReadAllText($stdoutPath) } else { $process.stdout_text }
    $process.Remove('stdout_text')
    $process.Remove('stderr_text')

    $payload = $null
    $parseError = $null
    try {
        if ([string]::IsNullOrWhiteSpace($raw)) { throw 'CLI returned empty JSON output.' }
        $payload = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        $parseError = 'CLI returned invalid or empty JSON.'
    }
    if ($process.exit_code -ne 0 -and $null -ne $payload -and 'error' -in $payload.PSObject.Properties.Name) {
        $process.cli_error = [string]$payload.error
    }
    return [pscustomobject]@{ Process = $process; Payload = $payload; ParseError = $parseError }
}

function New-ValidationRunDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$BaseRoot
    )

    $repoFull = Get-FullPath -Path $RepoRoot
    $artifactRoot = Get-FullPath -Path (Join-Path $repoFull ".tmp_tests")
    $baseFull = Get-FullPath -Path $BaseRoot -BaseRoot $repoFull
    Assert-PathUnderRoot `
        -RootPath $artifactRoot `
        -Path $baseFull `
        -Message "Validation artifact path is outside .tmp_tests: '{0}'."

    New-Item -ItemType Directory -Force -Path $baseFull | Out-Null
    $suffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
    $root = Join-Path $baseFull ("run_" + (Get-Date -Format "yyyyMMdd_HHmmss_fff") + "_${suffix}")
    $private = Join-Path $root "private"
    $shareable = Join-Path $root "shareable"
    New-Item -ItemType Directory -Path $private, $shareable | Out-Null
    return [pscustomobject]@{
        Root = $root
        Private = $private
        Shareable = $shareable
    }
}

function Get-FullValidationInventory {
    param([Parameter(Mandatory = $true)][int[]]$Channels)

    # Recipes contain public CLI arguments only; Core owns admission and SCPI.
    $voltage = @('--amplitude-vpp', '0.1', '--offset-v', '0', '--load', '50')
    $sine = @('configure-sine', '--frequency-hz', '1000') + $voltage
    $pulseSharedEdge = @('configure-pulse', '--frequency-hz', '1000', '--pulse-width-s', '0.0001', '--edge-time-s', '0.00000005') + $voltage
    $pulseIndependentEdges = @('configure-pulse', '--frequency-hz', '1000', '--pulse-width-s', '0.0001', '--leading-edge-s', '0.00000004', '--trailing-edge-s', '0.00000006') + $voltage
    $recipes = [System.Collections.ArrayList]::new()
    function Add-Recipe {
        param($Name, $Family, $Steps, [bool]$Parity = $false, $Observation = $null)
        [void]$recipes.Add([pscustomobject]@{
            name = $Name; family = $Family; steps = @($Steps); parity = $Parity; observation = $Observation
        })
    }
    function New-Step {
        param([string[]]$Arguments, [hashtable]$Readback = @{}, [hashtable]$Result = @{})
        return [pscustomobject]@{ arguments = $Arguments; readback = $Readback; result = $Result }
    }
    Add-Recipe 'status' 'inspection' @((New-Step @('status'))) $true
    $outputOn = New-Step @('output', '--state', 'on') @{ output_state = 'on' }
    $functions = @{ sine = 'SIN'; square = 'SQU'; ramp = 'RAMP'; triangle = 'TRI'; pulse = 'PULS'; dc = 'DC'; noise = 'NOIS'; prbs = 'PRBS' }
    foreach ($wave in @('sine', 'square', 'ramp', 'triangle', 'pulse', 'dc', 'noise', 'prbs')) {
        $args = @("configure-$wave") + $voltage
        $readback = @{ function = $functions[$wave]; offset_v = 0; load = '50' }
        if ($wave -eq 'dc') {
            $args = @('configure-dc', '--voltage-v', '0', '--load', '50')
        } else {
            $readback.amplitude = 0.1
            $readback.amplitude_unit = 'VPP'
            if ($wave -eq 'noise') {
                $args += @('--bandwidth-hz', '10000'); $readback.bandwidth_hz = 10000
            } elseif ($wave -eq 'prbs') {
                $args += @('--bit-rate-bps', '10000'); $readback.bit_rate_bps = 10000
            } else {
                $args += @('--frequency-hz', '1000'); $readback.frequency_hz = 1000
                if ($wave -eq 'pulse') { $args = $pulseIndependentEdges }
            }
        }
        $result = @{}
        switch ($wave) {
            'sine' { $args += @('--phase-deg', '45'); $result.phase_deg = 45 }
            'square' { $args += @('--duty-cycle-percent', '30'); $result.duty_cycle_percent = 30 }
            'ramp' { $args += @('--symmetry-percent', '30'); $result.symmetry_percent = 30 }
            'triangle' {
                $args = @('configure-triangle', '--frequency-hz', '1000', '--high-level-v', '0.075', '--low-level-v', '-0.025', '--load', '50')
                $readback.offset_v = 0.025
            }
            'prbs' { $args += @('--pattern', 'PN9'); $result.pattern = 'PN9' }
        }
        Add-Recipe "static-$wave" 'static' @((New-Step $args $readback $result)) ($wave -eq 'sine')
    }
    $modes = [ordered]@{
        am = @('--am-frequency', '10', '--am-depth', '20')
        'am-dssc' = @('--am-frequency', '10', '--am-depth', '20', '--am-type', 'dssc')
        fm = @('--fm-frequency', '10', '--fm-deviation', '100')
        pm = @('--pm-frequency', '10', '--pm-deviation-deg', '30')
        fsk = @('--fsk-hop-frequency', '2000', '--fsk-rate', '10')
        bpsk = @('--bpsk-phase-shift-deg', '90', '--bpsk-rate', '10')
        pwm = @('--pwm-frequency', '10', '--pwm-deviation-s', '0.00002')
        sum = @('--sum-frequency', '10', '--sum-amplitude-percent', '10')
    }
    foreach ($mode in $modes.Keys) {
        $carrier = if ($mode -eq 'pwm') { $pulseSharedEdge } else { $sine }
        $modeName = ($mode -split '-')[0]
        $result = @{ "${modeName}_enabled" = $true }
        if ($modeName -eq 'am') { $result.am_type = if ($mode -eq 'am-dssc') { 'dssc' } else { 'normal' } }
        $readback = @{ function = $(if ($mode -eq 'pwm') { 'PULS' } else { 'SIN' }); frequency_hz = 1000; amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50' }
        Add-Recipe "modulation-$mode" 'modulation' @((New-Step ($carrier + $modes[$mode]) $readback $result)) ($mode -eq 'am')
    }

    Add-Recipe 'configure-output' 'output-config' @(
        (New-Step @('configure-output', '--load', 'high-z', '--polarity', 'inverted', '--voltage-limits', 'off', '--autorange', 'off') @{ load = 'high-z' } @{ polarity = 'inverted'; voltage_limits_enabled = $false; autorange_enabled = $false }),
        (New-Step @('configure-output', '--load', '50', '--polarity', 'normal', '--voltage-limit-low', '-1', '--voltage-limit-high', '1', '--voltage-limits', 'on', '--autorange', 'on') @{ load = '50' } @{ polarity = 'normal'; voltage_limit_low = -1; voltage_limit_high = 1; voltage_limits_enabled = $true; autorange_enabled = $true })
    ) $true

    $burstPulse = @('configure-pulse', '--frequency-hz', '1', '--pulse-width-s', '0.2') + $voltage
    $pulseReadback = @{ function = 'PULS'; frequency_hz = 1; amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50' }
    $sineReadback = @{ function = 'SIN'; frequency_hz = 1000; amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50' }
    foreach ($source in @('immediate', 'bus', 'timer', 'external')) {
        $carrier = if ($source -eq 'immediate') { $sine } else { $burstPulse }
        $readback = if ($source -eq 'immediate') { $sineReadback } else { $pulseReadback }
        $args = $carrier + @('--burst-count', '3', '--burst-trigger-source', $source)
        if ($source -eq 'immediate') { $args += @('--burst-period-s', '0.1') }
        if ($source -eq 'timer') { $args += @('--burst-trigger-timer-s', '5') }
        if ($source -eq 'external') { $args += @('--burst-trigger-slope', 'positive') }
        $result = @{ burst_mode = 'counted'; burst_count = 3; burst_trigger_source = $source }
        if ($source -eq 'external') { $result.burst_trigger_slope = 'positive' }
        $steps = @((New-Step $args $readback $result))
        $observation = switch ($source) {
            'bus' { @{ expected = 'No pulses before the Bus trigger, then exactly three pulses followed by idle.'; seconds = 5 } }
            'timer' { @{ expected = 'Repeated groups of exactly three pulses with idle gaps between groups.'; seconds = 14 } }
            'external' { @{ input = 'external-trigger'; idle = 'low'; action = 'Send one rising-edge trigger, then wait for the burst to finish.'; expected = 'No pulses before the external edge, then exactly three pulses followed by idle.'; seconds = 20 } }
        }
        if ($null -ne $observation) { $steps += $outputOn }
        if ($source -eq 'bus') { $steps += New-Step @('trigger') @{ output_state = 'on' } }
        Add-Recipe "burst-$source" 'burst' $steps ($source -eq 'immediate') -Observation $observation
    }
    $fallingEdge = $burstPulse + @('--burst-count', '3', '--burst-trigger-source', 'external', '--burst-trigger-slope', 'negative')
    Add-Recipe 'burst-external-negative' 'burst' @(
        (New-Step $fallingEdge $pulseReadback @{ burst_mode = 'counted'; burst_count = 3; burst_trigger_slope = 'negative' }),
        $outputOn
    ) -Observation @{
        input = 'external-trigger'
        idle = 'high'
        action = 'Send one falling-edge trigger, then wait for the burst to finish.'
        expected = 'No pulses before the external edge, then exactly three pulses followed by idle.'
        seconds = 20
    }
    foreach ($polarity in @('normal', 'inverted')) {
        $name = if ($polarity -eq 'normal') { 'burst-gated' } else { 'burst-gated-inverted' }
        $levels = if ($polarity -eq 'normal') { 'low to high to low' } else { 'high to low to high' }
        Add-Recipe $name 'burst' @(
            (New-Step ($burstPulse + @('--gated-burst', '--gate-polarity', $polarity)) $pulseReadback @{ burst_mode = 'gated'; burst_gate_polarity = $polarity }),
            $outputOn
        ) -Observation @{
            input = 'external-gate'
            idle = if ($polarity -eq 'normal') { 'low' } else { 'high' }
            action = "Switch the gate $levels, holding the active level for several seconds."
            expected = 'Pulses continue while the gate is active and stop after the current cycle when it becomes inactive.'
            seconds = 20
        }
    }
    foreach ($variant in @('sine-linear', 'sine-logarithmic', 'sine-bus', 'sine-timer', 'square-linear', 'ramp-linear', 'triangle-linear')) {
        $wave, $option = $variant.Split('-')
        $spacing = if ($option -eq 'logarithmic') { 'logarithmic' } else { 'linear' }
        $triggered = $option -in @('bus', 'timer')
        $sweepTime = if ($triggered) { '1' } else { '0.1' }
        $args = @("configure-$wave-sweep", '--start-frequency-hz', '1000', '--stop-frequency-hz', '2000', '--spacing', $spacing, '--sweep-time-s', $sweepTime) + $voltage
        if ($triggered) { $args += @('--trigger-source', $option, '--hold-time-s', '0.2', '--return-time-s', '0.2') }
        if ($option -eq 'timer') { $args += @('--trigger-timer-s', '2') }
        $readback = @{ function = $functions[$wave]; amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50' }
        $result = @{ spacing = $spacing }
        if ($triggered) { $result += @{ trigger_source = $option; hold_time_s = 0.2; return_time_s = 0.2 } }
        $steps = @((New-Step $args $readback $result))
        $observation = $null
        if ($triggered) {
            $steps += $outputOn
            $expected = if ($option -eq 'bus') {
                $steps += New-Step @('trigger') @{ output_state = 'on' }
                'The frequency holds at the start until the Bus trigger, then makes one sweep, holds at the stop frequency, and returns.'
            } else { 'The Timer repeatedly starts sweeps, with a hold at the stop frequency and a return each time.' }
            $observation = @{ expected = $expected; seconds = 7 }
        }
        Add-Recipe "sweep-$variant" 'sweep' $steps ($variant -eq 'sine-linear') -Observation $observation
    }
    $steps = @()
    foreach ($wave in @('sine', 'square', 'ramp', 'triangle')) {
        $steps += New-Step (@("configure-$wave-list-sweep", '--frequencies-hz', '1000,1500,2000', '--dwell-s', '0.01') + $voltage) @{ function = $functions[$wave]; amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50' }
    }
    $steps += New-Step $sine @{ function = 'SIN'; frequency_hz = 1000; amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50' }
    Add-Recipe 'list-transition-cw-recovery' 'list-sweep' $steps $true
    $listTrigger = New-Step @('trigger') @{ output_state = 'on' }
    Add-Recipe 'list-bus-trigger' 'list-sweep' @(
        (New-Step (@('configure-sine-list-sweep', '--frequencies-hz', '1000,1500,2000', '--trigger-source', 'bus') + $voltage) @{} @{ trigger_source = 'bus' }),
        $outputOn, $listTrigger, $listTrigger, $listTrigger
    ) -Observation @{
        expected = 'The output advances through distinct frequency plateaus after the individual Bus triggers.'
        seconds = 4
    }
    Add-Recipe 'output-control' 'output-control' @(
        (New-Step $sine @{ function = 'SIN'; frequency_hz = 1000; amplitude = 0.1; amplitude_unit = 'VPP'; offset_v = 0; load = '50' }),
        (New-Step @('output', '--state', 'on') @{ output_state = 'on' }),
        (New-Step @('output', '--state', 'off') @{ output_state = 'off' })
    ) $true
    foreach ($channel in $Channels) {
        foreach ($recipe in $recipes) {
            if ($channel -ne 1 -and -not $recipe.parity) { continue }
            [pscustomobject]@{
                name = "ch${channel}/$($recipe.name)"; family = $recipe.family
                channel = $channel; required = $true; steps = $recipe.steps
                observation = $recipe.observation
            }
        }
    }
}

function Test-FullValidationPass {
    param([object[]]$Cases, [string[]]$RequiredNames)
    if (@($Cases | Where-Object { $_.status -ne 'PASS' }).Count -gt 0) { return $false }
    foreach ($name in $RequiredNames) {
        $matching = @($Cases | Where-Object { $_.name -ceq $name })
        if ($matching.Count -ne 1 -or $matching[0].status -cne 'PASS') { return $false }
    }
    return $true
}
