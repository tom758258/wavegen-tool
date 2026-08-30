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

function Invoke-CapturedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$StdOutPath,
        [Parameter(Mandatory = $true)][string]$StdErrPath,
        [string]$WorkingDirectory = (Get-Location).Path
    )

    foreach ($requiredDir in @((Split-Path -Parent $StdOutPath), (Split-Path -Parent $StdErrPath))) {
        if (-not [string]::IsNullOrWhiteSpace($requiredDir)) {
            New-Item -ItemType Directory -Force -Path $requiredDir | Out-Null
        }
    }

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $FilePath
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.Arguments = Join-ProcessArguments -Arguments $Arguments

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $stopwatch.Stop()

    Write-Utf8NoBomText -LiteralPath $StdOutPath -Text $stdout
    Write-Utf8NoBomText -LiteralPath $StdErrPath -Text $stderr

    return [ordered]@{
        name = $Name
        command = $FilePath
        arguments = @($Arguments)
        exit_code = $process.ExitCode
        duration_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 3)
        stdout = $StdOutPath
        stderr = $StdErrPath
        success = ($process.ExitCode -eq 0)
    }
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
