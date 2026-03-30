param(
    [ValidateSet("prod", "dev", "both")]
    [string]$Profile = "prod",

    [switch]$SkipZap,
    [switch]$SkipNuclei
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$reportsRoot = Join-Path $root "security-reports"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $reportsRoot $timestamp
$zapDir = Join-Path $runDir "zap"
$nucleiDir = Join-Path $runDir "nuclei"

New-Item -ItemType Directory -Force -Path $zapDir | Out-Null
New-Item -ItemType Directory -Force -Path $nucleiDir | Out-Null

function Get-TargetsFromFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return @()
    }

    return Get-Content $Path |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
}

function Resolve-TargetFile {
    param(
        [string]$LocalPath,
        [string]$ExamplePath
    )

    if (Test-Path $LocalPath) {
        return $LocalPath
    }
    return $ExamplePath
}

$prodFile = Resolve-TargetFile `
    -LocalPath (Join-Path $PSScriptRoot "targets-prod.txt") `
    -ExamplePath (Join-Path $PSScriptRoot "targets-prod.example.txt")
$devFile = Resolve-TargetFile `
    -LocalPath (Join-Path $PSScriptRoot "targets-dev.txt") `
    -ExamplePath (Join-Path $PSScriptRoot "targets-dev.example.txt")

$prodTargets = Get-TargetsFromFile $prodFile
$devTargets = Get-TargetsFromFile $devFile

$targets = @()
switch ($Profile) {
    "prod" { $targets += $prodTargets }
    "dev"  { $targets += $devTargets }
    "both" {
        $targets += $prodTargets
        $targets += $devTargets
    }
}

$targets = $targets | Select-Object -Unique

if (-not $targets -or $targets.Count -eq 0) {
    throw "No targets found for profile '$Profile'."
}

Write-Host "Running baseline scan for profile '$Profile' against $($targets.Count) targets."
Write-Host "Reports folder: $runDir"

if (-not $SkipZap) {
    foreach ($url in $targets) {
        $safeName = ($url -replace '^https?://', '') -replace '[^a-zA-Z0-9._-]', '_'
        $htmlOut = "$safeName-zap.html"
        $jsonOut = "$safeName-zap.json"

        Write-Host "[ZAP] Scanning $url"
        docker run --rm `
            -v "${zapDir}:/zap/wrk" `
            ghcr.io/zaproxy/zaproxy:stable `
            zap-baseline.py -t "$url" -m 3 -r "$htmlOut" -J "$jsonOut" -I
    }
}
else {
    Write-Host "Skipping ZAP scans (SkipZap)."
}

if (-not $SkipNuclei) {
    foreach ($url in $targets) {
        $safeName = ($url -replace '^https?://', '') -replace '[^a-zA-Z0-9._-]', '_'
        $txtOut = Join-Path $nucleiDir "$safeName-nuclei.txt"

        Write-Host "[Nuclei] Scanning $url"
        nuclei -u "$url" -severity low,medium,high,critical -o "$txtOut"
    }
}
else {
    Write-Host "Skipping Nuclei scans (SkipNuclei)."
}

Write-Host "Baseline scan complete."
Write-Host "ZAP reports:    $zapDir"
Write-Host "Nuclei reports: $nucleiDir"
