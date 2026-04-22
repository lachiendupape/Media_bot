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

Write-Host "Deploying Media Bot DEV from git ref: $Ref"

Invoke-CheckedCommand -Command { git fetch --all --tags } -FailureMessage "git fetch failed"
Invoke-CheckedCommand -Command { git checkout $Ref } -FailureMessage "git checkout failed for ref '$Ref'"
# Only pull when checked out on a branch; tags leave HEAD detached
$null = git symbolic-ref --quiet HEAD 2>$null
if ($LASTEXITCODE -eq 0) {
    Invoke-CheckedCommand -Command { git pull --ff-only } -FailureMessage "git pull failed"
}

Write-Host "Starting development stack using docker-compose.dev.yml"

Invoke-CheckedCommand -Command { docker compose -f docker-compose.dev.yml up -d --build } -FailureMessage "docker compose up failed"
Invoke-CheckedCommand -Command { docker compose -f docker-compose.dev.yml ps } -FailureMessage "docker compose ps failed"

Write-Host "Running DEV health check"
Invoke-CheckedCommand -Command {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:5001/health" -UseBasicParsing -TimeoutSec 15
} -FailureMessage "DEV health check failed"

Write-Host "DEV deploy complete"
