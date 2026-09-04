param(
    [switch]$NoBrowser,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoot = if ($env:RAG_DEMO_RUNTIME) { $env:RAG_DEMO_RUNTIME } else { Join-Path $env:TEMP "rag-demo-runtime" }

function Test-Command($Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Wait-ForExit() {
    if (-not $NoPause) {
        Read-Host "Press Enter to close this window"
    }
}

function Copy-RagRuntime() {
    Write-Host "Preparing Docker build context at $RuntimeRoot..."
    New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null

    foreach ($file in @('Dockerfile', 'docker-compose.yml', 'requirements.txt', '.dockerignore', '.env', 'sitecustomize.py')) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $file) -Destination (Join-Path $RuntimeRoot $file) -Force
    }

    $runtimeSrc = Join-Path $RuntimeRoot 'src'
    if (Test-Path -LiteralPath $runtimeSrc) {
        Remove-Item -LiteralPath $runtimeSrc -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'src') -Destination $runtimeSrc -Recurse -Force
}

function Test-DockerEngine() {
    # Relax ErrorActionPreference so harmless stderr warnings from `docker info`
    # (e.g. "No blkio throttle... support") are not turned into terminating errors.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        docker info 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $old
    }
}

if (-not (Test-Command "docker")) {
    Write-Host "Docker Desktop is required but docker was not found on PATH." -ForegroundColor Red
    Write-Host "Install/start Docker Desktop, then run this launcher again."
    Wait-ForExit
    exit 1
}

if (-not (Test-DockerEngine)) {
    Write-Host "Docker engine is not running. Starting Docker Desktop..." -ForegroundColor Yellow
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path -LiteralPath $dockerDesktop)) {
        Write-Host "Could not find Docker Desktop at $dockerDesktop." -ForegroundColor Red
        Write-Host "Start Docker Desktop manually, wait until it is ready, then run this launcher again."
        Wait-ForExit
        exit 1
    }
    Start-Process -FilePath $dockerDesktop | Out-Null

    $engineReady = $false
    for ($i = 1; $i -le 60; $i++) {
        if (Test-DockerEngine) { $engineReady = $true; break }
        Write-Host "Waiting for Docker engine... ($i)"
        Start-Sleep -Seconds 3
    }
    if (-not $engineReady) {
        Write-Host "Docker engine did not become ready within ~3 minutes." -ForegroundColor Red
        Write-Host "Make sure Docker Desktop finishes starting, then run this launcher again."
        Wait-ForExit
        exit 1
    }
    Write-Host "Docker engine is ready." -ForegroundColor Green
}

Copy-RagRuntime
Set-Location $RuntimeRoot

Write-Host "Starting RAG API container..."
# Relax ErrorActionPreference: docker compose streams build progress to stderr,
# which would otherwise be treated as a terminating error under "Stop".
$composeErrPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& docker compose --project-name rag-demo up --build -d 2>&1 | ForEach-Object { Write-Host $_ }
$composeExit = $LASTEXITCODE
$ErrorActionPreference = $composeErrPref
if ($composeExit -ne 0) {
    Write-Host "Docker Compose could not start the RAG API." -ForegroundColor Red
    Write-Host "Make sure Docker Desktop says Engine running, then try again."
    Write-Host "If it still fails, run this from PowerShell and share the output:"
    Write-Host "  docker info"
    Wait-ForExit
    exit $composeExit
}

$healthUrl = "http://localhost:8000/health"
$docsUrl = "http://localhost:8000/docs"
$ready = $false

for ($i = 1; $i -le 30; $i++) {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($response.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $ready) {
    Write-Host "The container started, but the API did not become healthy within 60 seconds." -ForegroundColor Yellow
    Write-Host "Run this to inspect logs:"
    Write-Host "  cd `"$RuntimeRoot`""
    Write-Host "  docker compose --project-name rag-demo logs -f rag-api"
    Wait-ForExit
    exit 1
}

Write-Host "RAG API is running:" -ForegroundColor Green
Write-Host "  $healthUrl"
Write-Host "  $docsUrl"
Write-Host ""
Write-Host "Endpoints:"
Write-Host "  POST http://localhost:8000/v1/search"
Write-Host "  POST http://localhost:8000/v1/answer"
Write-Host "  POST http://localhost:8000/v1/fact-check"

# The schema is created automatically on API startup; check whether any documents exist.
$docCountPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $docCountRaw = & docker compose --project-name rag-demo exec -T rag-api python -B -m src.rag_layer.db count 2>$null
    $docCount = 0
    if ($docCountRaw) {
        [int]::TryParse(($docCountRaw | Select-Object -Last 1).ToString().Trim(), [ref]$docCount) | Out-Null
    }
    Write-Host ""
    if ($docCount -eq 0) {
        Write-Host "Database is empty. Ingesting the full document corpus from Azure Blob..." -ForegroundColor Yellow
        Write-Host "This can take several minutes (downloads + embeds every document). Leave this window open."
        & docker compose --project-name rag-demo exec -T rag-api python -B -m src.rag_layer.ingest 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Ingestion did not finish cleanly." -ForegroundColor Red
            Write-Host "Check the Azure Blob SAS + Azure OpenAI credentials in .env, then retry with:"
            Write-Host "  docker compose --project-name rag-demo exec rag-api python -B -m src.rag_layer.ingest"
        } else {
            $afterRaw = & docker compose --project-name rag-demo exec -T rag-api python -B -m src.rag_layer.db count 2>$null
            $afterCount = if ($afterRaw) { ($afterRaw | Select-Object -Last 1).ToString().Trim() } else { "?" }
            Write-Host "Ingestion complete. Documents indexed: $afterCount" -ForegroundColor Green
        }
    } else {
        Write-Host "Documents indexed: $docCount" -ForegroundColor Green
        Write-Host "(To re-pull the corpus from Azure Blob later, run:"
        Write-Host "  docker compose --project-name rag-demo exec rag-api python -B -m src.rag_layer.ingest )"
    }
} catch {
    Write-Host "Could not check or ingest documents (non-fatal): $_" -ForegroundColor Yellow
} finally {
    $ErrorActionPreference = $docCountPref
}

if (-not $NoBrowser) {
    Start-Process $docsUrl
}

Write-Host ""
Write-Host "Leave Docker Desktop running while your app uses the API."
Wait-ForExit

