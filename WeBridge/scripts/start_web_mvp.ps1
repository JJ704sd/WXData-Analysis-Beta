#requires -Version 5.1
<#
.SYNOPSIS
Start the Windows workbench using local database copies. Demo is available with -Mode Demo.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_web_mvp.ps1
.EXAMPLE
.\scripts\start_web_mvp.ps1 -Mode Live -InstallBridge
#>
[CmdletBinding()]
param(
    [ValidateSet('Database', 'Demo', 'Live')][string]$Mode = 'Database',
    [ValidateRange(1024, 65535)][int]$Port = 8787,
    [string]$Python = '',
    [string]$RuntimeDir = '',
    [switch]$NoBrowser,
    [switch]$CheckOnly,
    [switch]$InstallBridge
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$rootDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$serverPath = Join-Path $rootDir 'web_mvp\server.py'
$modeName = $Mode.ToLowerInvariant()
$child = $null
$ownershipPath = $null

function ConvertTo-ProcessArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Find-WorkbenchPython {
    $requested = $Python
    if (-not $requested) { $requested = $env:WECHAT_MVP_PYTHON }
    $candidates = @()
    if ($requested) {
        $candidates += @{ Name = $requested; Prefix = @() }
    } else {
        $candidates += @{ Name = (Join-Path $rootDir '.venv\Scripts\python.exe'); Prefix = @() }
        $candidates += @{ Name = 'py.exe'; Prefix = @('-3') }
        $candidates += @{ Name = 'python.exe'; Prefix = @() }
    }
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { continue }
        $prefix = @($candidate.Prefix)
        $version = @(& $command.Source @prefix -c 'import json,sys; print(json.dumps(dict(version=sys.version.split()[0], executable=sys.executable))); sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>&1)
        if ($LASTEXITCODE -eq 0) {
            $details = ($version[-1].ToString() | ConvertFrom-Json)
            return @{ Path = $details.executable; Prefix = @(); Version = $details.version }
        }
    }
    throw 'Python 3.10+ was not found. Install Python, or pass -Python C:\path\python.exe. Demo does not need pip packages.'
}

function Assert-RuntimeWritable([string]$Directory) {
    $probePath = Join-Path $Directory ('launcher-write-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $targetPath = $Directory
    $probeStream = $null
    try {
        $null = New-Item -ItemType Directory -Path $Directory -Force
        $probeStream = [IO.File]::Open($probePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $probeStream.Dispose()
        $probeStream = $null
        Remove-Item -LiteralPath $probePath
        $databaseName = if ($Mode -eq 'Demo') { 'state-demo.sqlite' } else { 'state.sqlite' }
        foreach ($name in @('server.lock', 'server.pid', $databaseName, 'server.log', 'server-error.log')) {
            $targetPath = Join-Path $Directory $name
            if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
                $probeStream = [IO.File]::Open($targetPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
                $probeStream.Dispose()
                $probeStream = $null
            }
        }
    } catch {
        throw "Runtime path is not writable: $targetPath. Use the same Windows account that created it, or choose a new -RuntimeDir. Existing permissions were not changed."
    } finally {
        if ($null -ne $probeStream) { $probeStream.Dispose() }
        if (Test-Path -LiteralPath $probePath -ErrorAction SilentlyContinue) {
            Remove-Item -LiteralPath $probePath -ErrorAction SilentlyContinue
        }
    }
}

try {
    if ($InstallBridge -and $Mode -ne 'Live') { throw '-InstallBridge is only valid with -Mode Live.' }
    if (-not $RuntimeDir) {
        $folder = if ($Mode -eq 'Database') { '.runtime\web_mvp-database' } elseif ($Mode -eq 'Demo') { '.runtime\web_mvp-demo' } else { '.runtime\web_mvp' }
        $RuntimeDir = Join-Path $rootDir $folder
    } elseif (-not [IO.Path]::IsPathRooted($RuntimeDir)) {
        $RuntimeDir = Join-Path $rootDir $RuntimeDir
    }
    $RuntimeDir = [IO.Path]::GetFullPath($RuntimeDir)
    $existingOwnershipPath = Join-Path $RuntimeDir 'launcher-process.json'
    if (Test-Path -LiteralPath $existingOwnershipPath -PathType Leaf) {
        $existingOwnership = Get-Content -LiteralPath $existingOwnershipPath -Raw | ConvertFrom-Json
        $existingProcess = Get-Process -Id $existingOwnership.processId -ErrorAction SilentlyContinue
        if ($existingProcess -and
            $existingOwnership.startTimeUtcTicks -eq $existingProcess.StartTime.ToUniversalTime().Ticks.ToString() -and
            $existingOwnership.executable -eq $existingProcess.Path -and
            $existingOwnership.server -eq $serverPath -and
            $existingOwnership.runtimeDirectory -eq $RuntimeDir) {
            throw 'This runtime directory already has a running workbench. Stop it in its original window, or use another -RuntimeDir.'
        }
    }
    $runtimePython = Find-WorkbenchPython
    $pythonPrefix = @($runtimePython.Prefix)
    Write-Host ('Python: {0} ({1})' -f $runtimePython.Version, $runtimePython.Path)
    Write-Host ('Mode: {0}; URL: http://127.0.0.1:{1}' -f $Mode, $Port)
    Write-Host ('Runtime: {0}' -f $RuntimeDir)

    $identityCheckPath = Join-Path $rootDir 'scripts\check_runtime_identity.py'
    & $runtimePython.Path @pythonPrefix $identityCheckPath --root $rootDir
    if ($LASTEXITCODE -ne 0) {
        throw 'Runtime source identity check failed. Review the reported source locations; no service was started.'
    }

    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
    try {
        $listener.ExclusiveAddressUse = $true
        $listener.Start()
    } catch {
        throw "Port $Port is unavailable. Stop the existing service, or choose -Port 8788. No process was stopped."
    } finally {
        $listener.Stop()
    }

    if ($Mode -eq 'Live') {
        & $runtimePython.Path @pythonPrefix -c 'import importlib.metadata as m; v=int(m.version("websockets").split(".")[0]); assert 14 <= v < 18, "websockets must be >=14,<18"'
        if ($LASTEXITCODE -ne 0) { throw 'Live mode requires websockets >=14,<18. Create a local .venv and install web_mvp/requirements.txt; see WINDOWS.md.' }
        if (-not (Get-Command docker -CommandType Application -ErrorAction SilentlyContinue)) { throw 'Docker CLI was not found. Demo mode works without Docker.' }
        $dockerPlatform = @(& docker version --format '{{.Server.Os}}' 2>$null)
        if ($LASTEXITCODE -ne 0 -or ($dockerPlatform -join '').Trim() -ne 'linux') { throw 'An already running Docker engine using Linux containers is required. This script does not start Docker.' }
        $running = @(& docker inspect --format '{{.State.Running}}' wechat-lab-linux 2>$null)
        if ($LASTEXITCODE -ne 0 -or ($running -join '').Trim() -ne 'true') { throw 'The prepared wechat-lab-linux container is not running. This script does not create or start a WeChat container.' }
        if (-not (Test-Path -LiteralPath (Join-Path $rootDir '.secrets\linux-token') -PathType Leaf)) { throw 'Missing .secrets/linux-token for the existing test container. Follow its setup contract; never put the token in the command line.' }
        if (-not $InstallBridge) {
            & docker exec wechat-lab-linux test -f /opt/wechat-lab-mvp/bridge-manifest.json
            if ($LASTEXITCODE -ne 0) { throw 'The existing container has no bridge manifest. To install the project bridge explicitly, run with -Mode Live -InstallBridge.' }
        }
        Write-Host 'Live mode uses the existing Linux ARM64 test client. Native Windows read-only preview is a separate entry.'
    } elseif ($Mode -eq 'Database') {
        Write-Host 'Database mode receives from validated private copies. Select a conversation included for reading and choose Connect Send to start the Windows Hook bridge. Real send acceptance currently covers File Transfer Assistant only. Unknown versions remain blocked. Startup never attaches, sends, or extracts keys.'
    } else {
        Write-Host 'Demo uses synthetic local data. No Docker, WeChat login, network install, or real messages.'
    }

    Assert-RuntimeWritable $RuntimeDir
    if ($CheckOnly) {
        Write-Host 'Preflight passed. No server started and no bridge installed.'
        exit 0
    }
    if ($InstallBridge) {
        & $runtimePython.Path @pythonPrefix (Join-Path $rootDir 'web_mvp\install_bridge.py')
        if ($LASTEXITCODE -ne 0) { throw 'Bridge installation failed. The web server was not started.' }
    }

    $null = New-Item -ItemType Directory -Path $RuntimeDir -Force
    $stdoutPath = Join-Path $RuntimeDir 'server.log'
    $stderrPath = Join-Path $RuntimeDir 'server-error.log'
    $serverArgs = @($pythonPrefix) + @('-u', $serverPath, '--mode', $modeName, '--port', "$Port", '--runtime-dir', $RuntimeDir)
    $argumentLine = ($serverArgs | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' '
    $child = Start-Process -FilePath $runtimePython.Path -ArgumentList $argumentLine -WorkingDirectory $rootDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $url = "http://127.0.0.1:$Port"
    $ready = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ([DateTime]::UtcNow -lt $deadline) {
        $child.Refresh()
        if ($child.HasExited) {
            if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
                throw "Server exited during startup. Read $stderrPath"
            }
            throw "Server exited during startup without creating a log. Check the Python interpreter and runtime directory permissions: $RuntimeDir"
        }
        try {
            $environment = Invoke-RestMethod -Uri "$url/api/environment" -TimeoutSec 2
            if ($environment.mode -eq $modeName -and $environment.runtimeDirectory -eq $RuntimeDir) { $ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw "Server was not ready within 20 seconds. Read $stderrPath" }
    $ownershipPath = Join-Path $RuntimeDir 'launcher-process.json'
    @{
        processId = $child.Id
        startTimeUtcTicks = $child.StartTime.ToUniversalTime().Ticks.ToString()
        executable = $runtimePython.Path
        server = $serverPath
        mode = $modeName
        runtimeDirectory = $RuntimeDir
        stopRequested = $false
    } | ConvertTo-Json | Set-Content -LiteralPath $ownershipPath -Encoding UTF8
    Write-Host "Workbench ready: $url"
    Write-Host 'Keep this window open. Press Ctrl+C to stop the web workbench.'
    Write-Host "Logs: $stdoutPath and $stderrPath"
    if (-not $NoBrowser) {
        try { Start-Process $url } catch { Write-Warning "Could not open the browser. Open $url manually." }
    }
    while (-not $child.HasExited) {
        Start-Sleep -Milliseconds 500
        $child.Refresh()
    }
    $child.WaitForExit()
    $ownership = Get-Content -LiteralPath $ownershipPath -Raw | ConvertFrom-Json
    if (-not $ownership.stopRequested -and $null -ne $child.ExitCode -and $child.ExitCode -ne 0) {
        throw "Server stopped with exit code $($child.ExitCode). Read $stderrPath"
    }
    Write-Host 'Workbench stopped.'
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($null -ne $child) {
        $child.Refresh()
        if (-not $child.HasExited) { $child.Kill(); $child.WaitForExit(5000) | Out-Null }
        if ($ownershipPath -and (Test-Path -LiteralPath $ownershipPath)) {
            $ownership = Get-Content -LiteralPath $ownershipPath -Raw | ConvertFrom-Json
            if ($ownership.processId -eq $child.Id) { Remove-Item -LiteralPath $ownershipPath }
        }
        $child.Dispose()
    }
}
