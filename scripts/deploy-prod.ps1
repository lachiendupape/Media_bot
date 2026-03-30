param(
    [Parameter(Mandatory = $false)]
    [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

Write-Host "Deploying Media Bot from git ref: $Ref"

Invoke-CheckedCommand -Command { git fetch --all --tags } -FailureMessage "git fetch failed"
Invoke-CheckedCommand -Command { git checkout $Ref } -FailureMessage "git checkout failed for ref '$Ref'"
Invoke-CheckedCommand -Command { git pull --ff-only } -FailureMessage "git pull failed"

$imageTag = if ($Ref -match '^v\d') { $Ref } else { "latest" }
$env:MEDIA_BOT_VERSION = $imageTag
$imageName = "ghcr.io/lachiendupape/media-bot:$imageTag"

Write-Host "Starting production stack using docker-compose.prod.yml"
Write-Host "Target image: $imageName"

docker compose -f docker-compose.prod.yml pull
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Failed to pull $imageName from GHCR. Falling back to a local docker build."
    Invoke-CheckedCommand -Command { docker build -t $imageName . } -FailureMessage "Local docker build failed"
}

Invoke-CheckedCommand -Command { docker compose -f docker-compose.prod.yml up -d } -FailureMessage "docker compose up failed"

Invoke-CheckedCommand -Command { docker compose -f docker-compose.prod.yml ps } -FailureMessage "docker compose ps failed"
