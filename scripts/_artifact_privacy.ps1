Set-StrictMode -Version Latest

function Get-PortableRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $baseFull = [System.IO.Path]::GetFullPath($BasePath).TrimEnd('\', '/') +
        [System.IO.Path]::DirectorySeparatorChar
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    $baseUri = [Uri]::new($baseFull)
    $pathUri = [Uri]::new($pathFull)
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($pathUri).ToString()).Replace(
        '/',
        [System.IO.Path]::DirectorySeparatorChar
    )
}

function Test-ArtifactPathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $rootFull = [System.IO.Path]::GetFullPath($RootPath).TrimEnd('\', '/')
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    if ($pathFull.Equals($rootFull, $comparison)) {
        return $true
    }
    return $pathFull.StartsWith(
        $rootFull + [System.IO.Path]::DirectorySeparatorChar,
        $comparison
    )
}

function Get-DistinctiveSensitiveTokens {
    param([AllowNull()][AllowEmptyString()][string]$Resource)

    if ([string]::IsNullOrWhiteSpace($Resource)) {
        return @()
    }
    $parts = @($Resource -split '::')
    if ($Resource -match '^(?i)USB') {
        if ($parts.Count -lt 5) {
            return @()
        }
        $serial = $parts[3]
        if ($serial.Length -lt 6 -or $serial -match '^[0]+$') {
            return @()
        }
        return @($serial)
    }
    if ($Resource -match '^(?i)TCPIP') {
        if ($parts.Count -lt 2) {
            return @()
        }
        $hostValue = $parts[1].Trim()
        $reservedTokens = @(
            '0', 'localhost', 'localhost.localdomain', '0.0.0.0', '127.0.0.1', '::1',
            'inst0', 'instr', 'socket', 'hislip0', 'tcpip', 'tcpip0'
        )
        if (
            $hostValue.Length -lt 3 -or
            $hostValue -match '^[0]+$' -or
            $hostValue -in $reservedTokens -or
            $hostValue -notmatch '^[A-Za-z0-9][A-Za-z0-9.:%_-]*$'
        ) {
            return @()
        }
        return @($hostValue)
    }
    return @()
}

function Protect-ArtifactText {
    param(
        [AllowNull()][AllowEmptyString()][string]$Text,
        [AllowNull()][AllowEmptyString()][string]$Resource,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PrivateRoot,
        [string[]]$SensitiveValues = @()
    )

    if ($null -eq $Text) {
        return $null
    }
    $safe = [string]$Text
    if (-not [string]::IsNullOrWhiteSpace($Resource)) {
        $safe = $safe -replace [regex]::Escape($Resource), '<redacted-resource>'
    }
    foreach ($item in @($SensitiveValues)) {
        if (-not [string]::IsNullOrWhiteSpace($item)) {
            $pattern = "(?<![A-Za-z0-9_.-])$([regex]::Escape($item))(?![A-Za-z0-9_.-])"
            $safe = [regex]::Replace(
                $safe,
                $pattern,
                '<redacted>',
                [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
            )
        }
    }
    $safe = $safe -replace [regex]::Escape($PrivateRoot), '<private-local-path>'
    $safe = $safe -replace [regex]::Escape($RepoRoot), '<repository-root>'
    $safe = $safe -replace '(?im)(Keysight(?: Technologies)?|Agilent(?: Technologies)?),[^\r\n]+', '<redacted-idn>'
    $safe = $safe -replace '(?i)TCPIP\d*::[^\s"'',]+(?:::[^\s"'',]+)*', 'lan:<redacted-resource>'
    $safe = $safe -replace '(?i)USB\d*::[^\s"'',]+(?:::[^\s"'',]+)*', 'usb:<redacted-resource>'
    $octet = '(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)'
    $privateIpPattern = "(?<![\d.])(?:10\.$octet\.$octet\.$octet|172\.(?:1[6-9]|2\d|3[01])\.$octet\.$octet|192\.168\.$octet\.$octet|169\.254\.$octet\.$octet)(?![\d.])"
    $safe = $safe -replace $privateIpPattern, '<redacted-ip>'
    $safe = $safe -replace '(?i)(?:[A-Z]:\\[^\s"'']+)', '<redacted-path>'
    $safe = $safe -replace '(?i)(?:/(?:home|Users|mnt|tmp)/[^\s"'']+)', '<redacted-path>'
    return $safe
}

function ConvertTo-ShareableArtifactValue {
    param(
        [AllowNull()]$Value,
        [string]$FieldName = '',
        [Parameter(Mandatory = $true)][string]$PrivateRoot,
        [Parameter(Mandatory = $true)][string]$ShareableRoot,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [AllowNull()][AllowEmptyString()][string]$Resource,
        [string[]]$SensitiveValues = @()
    )

    if ($null -eq $Value) {
        return $null
    }
    $key = $FieldName.ToLowerInvariant()
    if ($key -in @('resource', 'resource_alias', 'visa_resource', 'resource_name', 'resource_id')) {
        return '<redacted-resource>'
    }
    if ($key -in @('serial', 'serial_number')) {
        return '<redacted>'
    }
    if ($key -in @('idn', 'raw_idn', 'idn.raw')) {
        return '<redacted-idn>'
    }

    if ($Value -is [string]) {
        $text = [string]$Value
        $isRootedPath = $false
        try {
            $isRootedPath = [System.IO.Path]::IsPathRooted($text)
        } catch {
            $isRootedPath = $false
        }
        if ($isRootedPath) {
            if (Test-ArtifactPathUnderRoot -RootPath $PrivateRoot -Path $text) {
                $relative = Get-PortableRelativePath -BasePath $PrivateRoot -Path $text
                return ('shareable/' + $relative.Replace('\', '/'))
            }
            if (Test-ArtifactPathUnderRoot -RootPath $ShareableRoot -Path $text) {
                return (Get-PortableRelativePath -BasePath $ShareableRoot -Path $text).Replace('\', '/')
            }
            if (Test-ArtifactPathUnderRoot -RootPath $RepoRoot -Path $text) {
                return (Get-PortableRelativePath -BasePath $RepoRoot -Path $text).Replace('\', '/')
            }
            return '<redacted-path>'
        }
        return Protect-ArtifactText `
            -Text $text `
            -Resource $Resource `
            -RepoRoot $RepoRoot `
            -PrivateRoot $PrivateRoot `
            -SensitiveValues $SensitiveValues
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}
        foreach ($entryKey in $Value.Keys) {
            $result[[string]$entryKey] = ConvertTo-ShareableArtifactValue `
                -Value $Value[$entryKey] `
                -FieldName ([string]$entryKey) `
                -PrivateRoot $PrivateRoot `
                -ShareableRoot $ShareableRoot `
                -RepoRoot $RepoRoot `
                -Resource $Resource `
                -SensitiveValues $SensitiveValues
        }
        return $result
    }
    if ($Value -is [pscustomobject]) {
        $result = [ordered]@{}
        foreach ($property in $Value.PSObject.Properties) {
            $result[$property.Name] = ConvertTo-ShareableArtifactValue `
                -Value $property.Value `
                -FieldName $property.Name `
                -PrivateRoot $PrivateRoot `
                -ShareableRoot $ShareableRoot `
                -RepoRoot $RepoRoot `
                -Resource $Resource `
                -SensitiveValues $SensitiveValues
        }
        return $result
    }
    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        $items = @(
            $Value | ForEach-Object {
                ConvertTo-ShareableArtifactValue `
                    -Value $_ `
                    -FieldName $FieldName `
                    -PrivateRoot $PrivateRoot `
                    -ShareableRoot $ShareableRoot `
                    -RepoRoot $RepoRoot `
                    -Resource $Resource `
                    -SensitiveValues $SensitiveValues
            }
        )
        return ,$items
    }
    return $Value
}

function New-SafeJsonPlaceholder {
    param(
        [Parameter(Mandatory = $true)][string]$ArtifactKind,
        [Parameter(Mandatory = $true)][string]$ParseStatus
    )

    return [ordered]@{
        artifact_available = $false
        artifact_kind = $ArtifactKind
        parse_status = $ParseStatus
        parse_error = if ($ParseStatus -eq 'failed') { "Could not parse ${ArtifactKind}." } else { $null }
        private_raw_artifact_retained = ($ParseStatus -ne 'missing')
    }
}

function Convert-PrivateJsonArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath,
        [Parameter(Mandatory = $true)][hashtable]$Context,
        [string]$ArtifactKind = 'json'
    )

    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        $payload = New-SafeJsonPlaceholder -ArtifactKind $ArtifactKind -ParseStatus 'missing'
    } else {
        try {
            $raw = Get-Content -LiteralPath $SourcePath -Raw
            if ([string]::IsNullOrWhiteSpace($raw)) {
                throw 'empty JSON artifact'
            }
            $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
            $payload = ConvertTo-ShareableArtifactValue `
                -Value $parsed `
                -PrivateRoot $Context.PrivateRoot `
                -ShareableRoot $Context.ShareableRoot `
                -RepoRoot $Context.RepoRoot `
                -Resource $Context.Resource `
                -SensitiveValues $Context.SensitiveValues
        } catch {
            $payload = New-SafeJsonPlaceholder -ArtifactKind $ArtifactKind -ParseStatus 'failed'
        }
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DestinationPath) | Out-Null
    Write-Utf8NoBomText -LiteralPath $DestinationPath -Text ($payload | ConvertTo-Json -Depth 20)
}

function Copy-ShareableArtifactTree {
    param([Parameter(Mandatory = $true)][hashtable]$Context)

    foreach ($file in Get-ChildItem -LiteralPath $Context.PrivateRoot -File -Recurse) {
        $relative = Get-PortableRelativePath -BasePath $Context.PrivateRoot -Path $file.FullName
        $destination = Join-Path $Context.ShareableRoot $relative
        switch ($file.Extension.ToLowerInvariant()) {
            '.json' {
                Convert-PrivateJsonArtifact `
                    -SourcePath $file.FullName `
                    -DestinationPath $destination `
                    -Context $Context
            }
            { $_ -in @('.txt', '.md', '.log') } {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
                $safe = Protect-ArtifactText `
                    -Text (Get-Content -LiteralPath $file.FullName -Raw) `
                    -Resource $Context.Resource `
                    -RepoRoot $Context.RepoRoot `
                    -PrivateRoot $Context.PrivateRoot `
                    -SensitiveValues $Context.SensitiveValues
                Write-Utf8NoBomText -LiteralPath $destination -Text $safe
            }
            default {
                # Unknown formats stay private.
            }
        }
    }
}

function New-ShareableArtifactSet {
    param(
        [Parameter(Mandatory = $true)]$PrivateReport,
        [Parameter(Mandatory = $true)][string]$PrivateSummaryPath,
        [Parameter(Mandatory = $true)][string]$RunRoot,
        [Parameter(Mandatory = $true)][string]$PrivateRoot,
        [Parameter(Mandatory = $true)][string]$ShareableRoot,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [AllowNull()][AllowEmptyString()][string]$Resource = "",
        [AllowNull()][AllowEmptyCollection()][string[]]$ExtraSensitiveValues = @()
    )

    $repoFull = Get-FullPath -Path $RepoRoot
    $artifactRoot = Get-FullPath -Path (Join-Path $repoFull '.tmp_tests')
    $runFull = Get-FullPath -Path $RunRoot
    $privateFull = Get-FullPath -Path $PrivateRoot
    $shareableFull = Get-FullPath -Path $ShareableRoot
    Assert-PathUnderRoot -RootPath $artifactRoot -Path $runFull -Message "Run path is outside .tmp_tests: '{0}'."
    Assert-PathUnderRoot -RootPath $runFull -Path $privateFull -Message "Private path is outside the run root: '{0}'."
    Assert-PathUnderRoot -RootPath $runFull -Path $shareableFull -Message "Shareable path is outside the run root: '{0}'."

    New-Item -ItemType Directory -Force -Path $shareableFull | Out-Null
    $context = @{
        PrivateRoot = $privateFull
        ShareableRoot = $shareableFull
        RepoRoot = $repoFull
        Resource = [string]$Resource
        SensitiveValues = @(
            @(Get-DistinctiveSensitiveTokens -Resource ([string]$Resource)) +
            @($ExtraSensitiveValues)
        )
    }
    Copy-ShareableArtifactTree -Context $context

    $report = ConvertTo-ShareableArtifactValue `
        -Value $PrivateReport `
        -PrivateRoot $context.PrivateRoot `
        -ShareableRoot $context.ShareableRoot `
        -RepoRoot $context.RepoRoot `
        -Resource $context.Resource `
        -SensitiveValues $context.SensitiveValues
    $report.artifact_visibility = 'shareable'
    $report.candidate_evidence_only = $true
    $report.promotes_live_support = $false
    $report.private_raw_artifacts_retained = $true
    $report.redaction_applied = $true
    $report.redaction_version = 1
    Write-Utf8NoBomText `
        -LiteralPath (Join-Path $context.ShareableRoot 'report.json') `
        -Text ($report | ConvertTo-Json -Depth 20)

    if (Test-Path -LiteralPath $PrivateSummaryPath -PathType Leaf) {
        $summarySafe = Protect-ArtifactText `
            -Text (Get-Content -LiteralPath $PrivateSummaryPath -Raw) `
            -Resource $context.Resource `
            -RepoRoot $context.RepoRoot `
            -PrivateRoot $context.PrivateRoot `
            -SensitiveValues $context.SensitiveValues
    } else {
        $summarySafe = "# Validation Summary`n`n- Private summary unavailable; raw private artifacts retained."
    }
    Write-Utf8NoBomText `
        -LiteralPath (Join-Path $context.ShareableRoot 'summary.md') `
        -Text $summarySafe
    return $report
}
