param(
    [Parameter(Mandatory = $false)]
    [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"

Write-Host "Deploying Media Bot from git ref: $Ref"

git fetch --all --tags
git checkout $Ref
git pull --ff-only

Write-Host "Starting production stack using docker-compose.prod.yml"
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

docker compose -f docker-compose.prod.yml ps
