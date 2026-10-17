<#
.SYNOPSIS
Run the ShipOS dashboard offline on http://127.0.0.1:<Port>/ in the foreground (Ctrl+C stops it).

.DESCRIPTION
Uses an existing uv (on PATH, `py -m uv`, or .tools\uv.exe in the repository) and only the
locked dependencies already in uv's cache: `uv run --locked --offline`. It never downloads,
upgrades, edits project files, changes PATH or execution policy, or stops other processes.

-Prepare is the separate one-time ONLINE step: `uv sync --locked` fills the cache and .venv.

.EXAMPLE
.\tools\demo.ps1
.\tools\demo.ps1 -Port 8765 -OpenBrowser
.\tools\demo.ps1 -Prepare
#>
param(
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [switch]$OpenBrowser,
    [switch]$Prepare
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot  # repository root, whatever the caller's directory

function Fail([string]$Message) {
    Write-Host "shipos demo: $Message" -ForegroundColor Red
    exit 1
}

function Test-Uv([string[]]$Candidate) {
    # A failed discovery probe only means this candidate is unavailable. Under 'Stop', Windows
    # PowerShell 5.1 turns a native command's stderr (e.g. "No module named uv" from `py -m uv`
    # in an active venv) into a terminating error, so relax it for the probe and restore it.
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $probeArgs = @($Candidate | Select-Object -Skip 1) + '--version'
        & $Candidate[0] @probeArgs *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $saved
    }
}

function Find-Uv {
    # Documented order: uv on PATH, then `py -m uv`, then the repository's .tools\uv.exe.
    $candidates = @()
    if (Get-Command uv -ErrorAction SilentlyContinue) { $candidates += , @('uv') }
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @('py', '-m', 'uv') }
    $local = Join-Path $Root '.tools\uv.exe'
    if (Test-Path $local) { $candidates += , @($local) }
    foreach ($candidate in $candidates) {
        if (Test-Uv $candidate) { return , $candidate }
    }
    return $null
}

$uv = Find-Uv
if ($null -eq $uv) {
    Fail ("no uv found (checked: uv on PATH, 'py -m uv', $Root\.tools\uv.exe). " +
        "Install it once while online, e.g. 'py -m pip install --user uv', then run " +
        "'.\tools\demo.ps1 -Prepare'.")
}
$uvExe = $uv[0]
$uvArgs = @($uv | Select-Object -Skip 1)

if ($Prepare) {
    Write-Host "shipos demo: one-time online preparation (uv sync --locked) in $Root"
    & $uvExe @uvArgs sync --locked --directory $Root
    if ($LASTEXITCODE -ne 0) { Fail "uv sync --locked failed (exit $LASTEXITCODE); see the output above." }
    Write-Host "shipos demo: prepared. Launch offline with: .\tools\demo.ps1"
    exit 0
}

# Offline from here on: fail clearly instead of falling back to the network.
& $uvExe @uvArgs sync --locked --offline --directory $Root
if ($LASTEXITCODE -ne 0) {
    Fail ("locked dependencies are not available offline (uv exit $LASTEXITCODE). " +
        "Run once while online: .\tools\demo.ps1 -Prepare")
}

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $listener.Start() } catch {
    Fail "port $Port on 127.0.0.1 is already in use. Choose another: .\tools\demo.ps1 -Port <n>"
} finally { $listener.Stop() }

$url = "http://127.0.0.1:$Port/"
if ($OpenBrowser) {
    # Open once the server answers; the server itself stays in the foreground.
    Start-Job -ArgumentList $url -ScriptBlock {
        param($u)
        foreach ($i in 1..60) {
            try { Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 1 | Out-Null; Start-Process $u; return }
            catch { Start-Sleep -Milliseconds 500 }
        }
    } | Out-Null
}

Write-Host "shipos demo: serving $url (offline, locked). Press Ctrl+C to stop."
& $uvExe @uvArgs run --locked --offline --directory $Root python -m uvicorn shipos.api.server:app `
    --host 127.0.0.1 --port $Port --ws websockets-sansio
exit $LASTEXITCODE
