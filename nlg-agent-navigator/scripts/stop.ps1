# Stop the agent API, Prompt Studio, and UI started by start.ps1.
# The prompt database keeps running so the next start is quick.
# Usage:
#   .\scripts\stop.ps1
#   .\scripts\stop.ps1 -Database    # also stop the Postgres container; data volume is kept

param(
    [switch]$Database
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $PSScriptRoot ".run\pids.json"

function Get-Listener([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
    return [pscustomobject]@{
        Pid = [int]$conn.OwningProcess
        Name = [string]$proc.Name
        Command = [string]$proc.CommandLine
        Parent = [int]$proc.ParentProcessId
    }
}

function Stop-Tree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

function Stop-Owned([int]$Port, [string]$Pattern) {
    $owner = Get-Listener $Port
    if (-not $owner) {
        Write-Host "Nothing is listening on port $Port."
        return
    }
    if ($owner.Command -notmatch $Pattern) {
        Write-Host "Port $Port is in use by another process; leaving it alone."
        Write-Host $owner.Command
        return
    }
    $target = $owner.Pid
    $current = $owner
    while ($current.Parent -gt 0) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($current.Parent)"
        if (-not $parent) { break }
        if ($parent.Name -match '^(Code|Cursor|explorer|WindowsTerminal|svchost|services|powershell|pwsh)$') { break }
        $parentCommand = [string]$parent.CommandLine
        if ($parentCommand -match $Pattern -or $parent.Name -match '^(python|node|cmd)$') {
            $target = [int]$parent.ProcessId
            $current = [pscustomobject]@{
                Pid = $target
                Name = [string]$parent.Name
                Command = $parentCommand
                Parent = [int]$parent.ParentProcessId
            }
            continue
        }
        break
    }
    Write-Host "Stopping port $Port (pid $target)."
    Stop-Tree $target
}

if (Test-Path -LiteralPath $PidFile) {
    $saved = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    foreach ($name in @("agent", "studio", "ui")) {
        $processId = [int]$saved.$name
        if ($processId -gt 0) { Stop-Tree $processId }
    }
}

Stop-Owned 8000 'app\.main:app'
Stop-Owned 8001 'prompt_studio_api\.main:app'
$uiPattern = [regex]::Escape((Join-Path $Root "frontend")) + ".*vite"
Stop-Owned 5173 $uiPattern

if (Test-Path -LiteralPath $PidFile) {
    Remove-Item -LiteralPath $PidFile -Force
}

if ($Database) {
    Write-Host "Stopping prompt database. The data volume is kept."
    Push-Location $Root
    try {
        docker compose stop prompt-db
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "Database left running on localhost:5433. Use -Database to stop that container too."
}
