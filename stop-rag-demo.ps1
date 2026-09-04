param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$RuntimeRoot = if ($env:RAG_DEMO_RUNTIME) { $env:RAG_DEMO_RUNTIME } else { Join-Path $env:TEMP "rag-demo-runtime" }

if (Test-Path -LiteralPath (Join-Path $RuntimeRoot 'docker-compose.yml')) {
    Set-Location $RuntimeRoot
    docker compose --project-name rag-demo down
} else {
    Write-Host "No staged rag-demo runtime found at $RuntimeRoot"
}

if (-not $NoPause) {
    Read-Host "Press Enter to close this window"
}
