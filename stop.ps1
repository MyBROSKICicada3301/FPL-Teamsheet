#Requires -Version 5.1
<#
    Stop the server started by .\start.ps1 -- or by ./start.sh, which records a
    pid from the MSYS namespace that is not a Windows pid. Hence the fallback
    to whoever is holding the port.
#>
param(
    [int] $Port = 0
)

Set-Location -LiteralPath $PSScriptRoot

$PidFile = '.server.pid'

function Write-Err {
    param([string] $Message)
    [Console]::Error.WriteLine($Message)
}

if ($Port -le 0) {
    if ($env:PORT) {
        $Port = [int] $env:PORT
    } elseif (Test-Path -LiteralPath '.env') {
        $setting = Get-Content -LiteralPath '.env' |
                   Where-Object { $_ -match '^\s*(export\s+)?PORT\s*=' } |
                   Select-Object -Last 1
        if ($setting) { $Port = [int] ($setting -replace '^[^=]*=', '').Trim().Trim('"', "'") }
    }
}
if ($Port -le 0) { $Port = 8765 }

# A pid file outlives the process it names, and Windows hands pids out again.
# Kill by PID, not by name, and only once the command line says this really is
# our server: the alternative is stopping whatever inherited the number.
function Get-FplServer {
    param([int] $Candidate)
    if ($Candidate -le 0) { return $null }
    $found = Get-CimInstance Win32_Process -Filter "ProcessId = $Candidate" -ErrorAction SilentlyContinue
    if ($found -and $found.CommandLine -and $found.CommandLine -match 'fpl\.server') { return $found }
    return $null
}

$target = $null

if (Test-Path -LiteralPath $PidFile) {
    $recorded = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    $number = 0
    if ([int]::TryParse($recorded, [ref] $number)) {
        $target = Get-FplServer -Candidate $number
    }
}

if (-not $target) {
    $holder = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
              Select-Object -First 1
    if ($holder) { $target = Get-FplServer -Candidate $holder.OwningProcess }
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue

if (-not $target) {
    Write-Output "Not running (nothing on port $Port)."
    exit 0
}

$stopped = $target.ProcessId
Stop-Process -Id $stopped -Force -ErrorAction SilentlyContinue

# Saying "Stopped" without looking is how ./stop.sh used to report success over
# a server that was still serving. Confirm it, then say so.
Start-Sleep -Milliseconds 500
if (Get-Process -Id $stopped -ErrorAction SilentlyContinue) {
    Write-Err "Could not stop pid $stopped -- it is still running."
    exit 1
}

Write-Output "Stopped (pid $stopped)."
exit 0
