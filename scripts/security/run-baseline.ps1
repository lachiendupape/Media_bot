param(
    [ValidateSet("prod", "dev", "both")]
    [string]$Profile = "prod",

    [switch]$SkipZap,
    [switch]$SkipNuclei,

    # When set, skips the LAN detection warning.
    # NOTE: Scans run from inside 192.168.x.x bypass reverse-proxy IP
    # restrictions and will NOT reflect what an internet attacker sees.
    # For true external testing, trigger the GitHub Actions workflow:
    #   gh workflow run security.yml -f profile=prod
    [switch]$IgnoreLanWarning
)

$ErrorActionPreference = "Stop"

# Warn if running from a private/LAN IP (results will bypass internet ACLs)
if (-not $IgnoreLanWarning) {
    $localIPs = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress
    $isLan = $localIPs | Where-Object {
        $_ -match '^192\.168\.' -or $_ -match '^10\.' -or $_ -match '^172\.(1[6-9]|2[0-9]|3[01])\.'
    }
    if ($isLan) {
        Write-Warning @"
This machine has a private/LAN IP ($($isLan -join ', ')).
Scans run from inside the home network bypass reverse-proxy IP restrictions,
so services protected by 'allow 192.168.0.0/16 only' rules will appear
accessible even though they are not reachable from the internet.

For a true EXTERNAL perspective, trigger the GitHub Actions workflow:
  gh workflow run security.yml -f profile=prod

To suppress this warning and continue the local scan anyway:
  .\run-baseline.ps1 -IgnoreLanWarning

"@
        exit 1
    }
}


$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$reportsRoot = Join-Path $root "security-reports"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $reportsRoot $timestamp
$zapDir = Join-Path $runDir "zap"
$nucleiDir = Join-Path $runDir "nuclei"

New-Item -ItemType Directory -Force -Path $zapDir | Out-Null
New-Item -ItemType Directory -Force -Path $nucleiDir | Out-Null

function Get-NucleiRunner {
    $native = Get-Command nuclei -ErrorAction SilentlyContinue
    if ($native) {
        return "native"
    }
    return "docker"
}

function Invoke-NucleiScan {
    param(
        [string]$TargetUrl,
        [string]$OutputFile,
        [string]$Runner,
        [string]$OutputDir
    )

    if ($Runner -eq "native") {
        nuclei -u "$TargetUrl" -severity low,medium,high,critical -no-color -o "$OutputFile"
        return
    }

    $outDirResolved = (Resolve-Path $OutputDir).Path
    $outFileName = Split-Path -Leaf $OutputFile

    docker run --rm `
        -v "${outDirResolved}:/work" `
        projectdiscovery/nuclei:latest `
        -u "$TargetUrl" `
        -severity low,medium,high,critical `
        -no-color `
        -o "/work/$outFileName"
}

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
    $nucleiRunner = Get-NucleiRunner
    Write-Host "[Nuclei] Runner: $nucleiRunner"

    foreach ($url in $targets) {
        $safeName = ($url -replace '^https?://', '') -replace '[^a-zA-Z0-9._-]', '_'
        $txtOut = Join-Path $nucleiDir "$safeName-nuclei.txt"

        Write-Host "[Nuclei] Scanning $url"
        Invoke-NucleiScan -TargetUrl $url -OutputFile $txtOut -Runner $nucleiRunner -OutputDir $nucleiDir
    }
}
else {
    Write-Host "Skipping Nuclei scans (SkipNuclei)."
}

Write-Host "Baseline scan complete."
Write-Host "ZAP reports:    $zapDir"
Write-Host "Nuclei reports: $nucleiDir"
