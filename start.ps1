#Requires -Version 5.1
<#
    Serve the FPL Teamsheet on http://127.0.0.1:8765 in the background.

    The PowerShell counterpart to ./start.sh, for Windows without Git Bash or
    WSL. Same port, same .server.pid, same wording, so the two sides of the
    repository behave alike and either stop script can end either server.
#>
param(
    [int] $Port = 0
)

Set-Location -LiteralPath $PSScriptRoot

$PidFile = '.server.pid'
$OutLog  = 'server.log'
# Start-Process refuses to aim both streams at one file, so a traceback lands
# here instead of in server.log the way it does under bash. Both are read back
# below when a start fails, which is the only time the split matters.
$ErrLog  = 'server.err.log'

function Write-Err {
    param([string] $Message)
    [Console]::Error.WriteLine($Message)
}

# The Python side loads .env itself; this is only so PORT is known here, and so
# a key set in this session reaches the server process. Values already in the
# environment are left alone, matching fpl/env.py.
if (Test-Path -LiteralPath '.env') {
    foreach ($line in (Get-Content -LiteralPath '.env')) {
        $entry = $line.Trim()
        if (-not $entry -or $entry.StartsWith('#')) { continue }
        if ($entry.StartsWith('export ')) { $entry = $entry.Substring(7).TrimStart() }

        $split = $entry.IndexOf('=')
        if ($split -lt 1) { continue }

        $key   = $entry.Substring(0, $split).Trim()
        $value = $entry.Substring($split + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            $last  = $value[$value.Length - 1]
            if ($first -eq $last -and ($first -eq '"' -or $first -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if (-not [Environment]::GetEnvironmentVariable($key, 'Process')) {
            [Environment]::SetEnvironmentVariable($key, $value, 'Process')
        }
    }
}

if ($Port -le 0) {
    if ($env:PORT) { $Port = [int] $env:PORT } else { $Port = 8765 }
}
$Url = "http://127.0.0.1:$Port"

function Test-Healthz {
    param([string] $Address, [int] $TimeoutSec = 5)
    try {
        $reply = Invoke-WebRequest -Uri "$Address/api/healthz" -UseBasicParsing `
                                   -TimeoutSec $TimeoutSec -ErrorAction Stop
        return $reply.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (Test-Healthz -Address $Url) {
    Write-Output "Already running at $Url"
    exit 0
}

# A port that is occupied but not answering our health check is somebody
# else's process, not a failed start of ours. Saying so beats launching a
# server that cannot bind and then blaming the log it never wrote.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Err "Port $Port is in use, but nothing there answers /api/healthz."
    Write-Err "  what is on it:  Get-Process -Id (Get-NetTCPConnection -LocalPort $Port).OwningProcess"
    Write-Err "  or use another: .\start.ps1 -Port 8766"
    exit 1
}

# Windows has no python3.exe. The python.org installer ships python.exe and the
# py launcher, and the python3 that PATH does find is a Microsoft Store alias
# stub that only prints "Python was not found" and exits 49. A name proves
# nothing, so every candidate has to run and report back.
#
# What it reports is sys.executable rather than the name we called it by,
# because py.exe is a launcher holding the real interpreter as a child process.
# Stop the launcher afterwards and the child lives on, still holding the port.
# Starting the interpreter itself keeps .server.pid pointing at the process
# that actually has to die.
$Probe = 'import sys; sys.exit(1) if sys.version_info < (3, 10) else print(sys.executable)'

function Resolve-Python {
    param([string] $Source)

    $candidates = @()
    if ($env:PYTHON) { $candidates += ,@($env:PYTHON) }
    $candidates += ,@('py', '-3')
    $candidates += ,@('python')
    $candidates += ,@('python3')

    foreach ($candidate in $candidates) {
        $exe    = $candidate[0]
        $prefix = @()
        if ($candidate.Count -gt 1) { $prefix = $candidate[1..($candidate.Count - 1)] }

        $global:LASTEXITCODE = 0
        try {
            $reported = & $exe @prefix '-c' $Source 2>$null
        } catch {
            continue        # not installed, or a stub that cannot execute
        }
        if ($LASTEXITCODE -eq 0 -and $reported) {
            return ($reported | Select-Object -Last 1).ToString().Trim()
        }
    }
    return $null
}

$Python = Resolve-Python -Source $Probe
if (-not $Python) {
    Write-Err "No Python 3.10 or newer found (tried py -3, python, python3)."
    Write-Err "  install it:    https://www.python.org/downloads/"
    Write-Err "  or name yours: `$env:PYTHON = 'C:\path\to\python.exe'; .\start.ps1"
    exit 1
}

# WindowStyle Hidden rather than NoNewWindow: it gives the server its own
# console, so closing the terminal that started it does not take it down.
# That is what nohup buys the bash script.
$server = Start-Process -FilePath $Python `
                        -ArgumentList @('-m', 'fpl.server', '--port', "$Port") `
                        -WindowStyle Hidden -PassThru `
                        -RedirectStandardOutput $OutLog `
                        -RedirectStandardError $ErrLog
Set-Content -LiteralPath $PidFile -Value $server.Id -Encoding ascii

# Poll instead of sleeping a guess. The health check answers immediately --
# projections load in the background and report themselves as "starting" -- so
# this only has to cover process startup.
$deadline = (Get-Date).AddSeconds(20)
$running  = $false
while ((Get-Date) -lt $deadline) {
    if ($server.HasExited) { break }
    if (Test-Healthz -Address $Url -TimeoutSec 2) { $running = $true; break }
    Start-Sleep -Milliseconds 400
}

if ($running) {
    Write-Output "FPL Teamsheet running at $Url"
    Write-Output "Stop it with .\stop.ps1   -   request log in $OutLog"
    exit 0
}

Write-Err "Server failed to start. Last lines of ${OutLog}:"
if (Test-Path -LiteralPath $OutLog) {
    Get-Content -LiteralPath $OutLog -Tail 20 | ForEach-Object { Write-Err "  $_" }
}
if ((Test-Path -LiteralPath $ErrLog) -and (Get-Item -LiteralPath $ErrLog).Length -gt 0) {
    Write-Err "and of ${ErrLog}:"
    Get-Content -LiteralPath $ErrLog -Tail 20 | ForEach-Object { Write-Err "  $_" }
}
if (-not $server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
exit 1
