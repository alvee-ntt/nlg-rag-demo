# Start the local FlexLife agent stack: prompt database, agent API, Prompt Studio, and UI.
# Usage, from the repo root or from scripts/:
#   .\scripts\start.ps1
# Stop with .\scripts\stop.ps1. That leaves the database running.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $PSScriptRoot ".run"
$PidFile = Join-Path $RunDir "pids.json"
$Python = Join-Path $Root "backend\.venv\Scripts\python.exe"
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"

function Test-Command($Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-Alive([int]$ProcessId) {
    return $ProcessId -gt 0 -and $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-Listener([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
    return [pscustomobject]@{ Pid = [int]$conn.OwningProcess; Command = [string]$proc.CommandLine }
}

function Wait-Port([int]$Port, [string]$LogPath, [int]$Seconds = 45) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Get-Listener $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    Write-Host "Timed out waiting for port $Port." -ForegroundColor Red
    $outLog = $LogPath -replace '\.err\.log$', '.out.log'
    foreach ($path in @($LogPath, $outLog)) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        Write-Host "Last lines of $path"
        Get-Content -LiteralPath $path -Tail 20
    }
    return $false
}

function Start-Logged($FilePath, $ArgumentList, $WorkingDirectory, $InFile, $OutLog, $ErrLog) {
    # Do not let background services inherit this PowerShell console's input.
    # An inherited stdin handle can make the prompt appear ready while a hidden
    # child process consumes the user's keystrokes.
    New-Item -ItemType File -Path $InFile -Force | Out-Null
    return Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardInput $InFile `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Backend virtualenv is missing at $Python" -ForegroundColor Red
    Write-Host "From backend/: python -m venv .venv; .venv\Scripts\python -m pip install -e `".[dev]`""
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $Frontend "node_modules"))) {
    Write-Host "Frontend dependencies are missing. From frontend/: npm install" -ForegroundColor Red
    exit 1
}
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) {
    Write-Host "npm.cmd was not found. Install Node.js and reopen the terminal." -ForegroundColor Red
    exit 1
}
if (-not (Test-Command "docker")) {
    Write-Host "Docker was not found on PATH. Start Docker Desktop, then run this again." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
$saved = $null
if (Test-Path -LiteralPath $PidFile) {
    $saved = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
}

Write-Host "Starting prompt database..."
Push-Location $Root
try {
    docker compose up -d prompt-db
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $dbId = @(docker compose ps -q prompt-db | Where-Object { $_ }) | Select-Object -First 1
    if (-not $dbId) {
        Write-Host "Prompt database container was not created." -ForegroundColor Red
        exit 1
    }
    $healthy = $false
    for ($i = 0; $i -lt 40; $i++) {
        $status = (docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $dbId).Trim()
        if ($status -eq "healthy" -or $status -eq "running") { $healthy = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $healthy) {
        Write-Host "Prompt database did not become ready." -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}

$env:PYTHONUNBUFFERED = "1"
$pids = [ordered]@{}
$failed = $false

$services = @(
    @{
        Name = "agent"; Port = 8000; Match = "app\.main:app"
        File = $Python; Args = @("-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000")
        Dir = $Backend
    },
    @{
        Name = "studio"; Port = 8001; Match = "prompt_studio_api\.main:app"
        File = $Python; Args = @("-m", "uvicorn", "prompt_studio_api.main:app", "--reload", "--host", "127.0.0.1", "--port", "8001")
        Dir = $Backend
    },
    @{
        Name = "ui"; Port = 5173; Match = "vite"
        File = $npm.Source; Args = @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort")
        Dir = $Frontend
    }
)

foreach ($svc in $services) {
    $listener = Get-Listener $svc.Port
    $prior = 0
    if ($saved) {
        $prop = $saved.PSObject.Properties[$svc.Name]
        if ($prop) { $prior = [int]$prop.Value }
    }
    if ($listener -and $listener.Command -match $svc.Match) {
        Write-Host "$($svc.Name) is already running on port $($svc.Port)."
        $pids[$svc.Name] = $(if (Test-Alive $prior) { $prior } else { $listener.Pid })
        continue
    }
    if ($listener) {
        Write-Host "Port $($svc.Port) is already in use by another process, so $($svc.Name) was not started." -ForegroundColor Red
        Write-Host $listener.Command
        $failed = $true
        continue
    }
    $inFile = Join-Path $RunDir "$($svc.Name).in"
    $outLog = Join-Path $RunDir "$($svc.Name).out.log"
    $errLog = Join-Path $RunDir "$($svc.Name).err.log"
    $proc = Start-Logged $svc.File $svc.Args $svc.Dir $inFile $outLog $errLog
    if (-not (Wait-Port $svc.Port $errLog)) {
        & taskkill.exe /PID $proc.Id /T /F 2>$null | Out-Null
        $failed = $true
        continue
    }
    $pids[$svc.Name] = $proc.Id
    Write-Host "Started $($svc.Name) (pid $($proc.Id)) on port $($svc.Port)."
}

$pids | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding utf8

Write-Host ""
Write-Host "Agent API      http://127.0.0.1:8000"
Write-Host "Prompt Studio  http://127.0.0.1:8001"
Write-Host "UI             http://127.0.0.1:5173"
Write-Host "Database       localhost:5433"
Write-Host "Logs           $RunDir"
if ($failed) { exit 1 }
