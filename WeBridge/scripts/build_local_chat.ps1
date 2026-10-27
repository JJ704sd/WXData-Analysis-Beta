# Build-only dependencies live in .runtime/offline-build-deps. No automatic install.
[CmdletBinding()]
param([string]$Python = 'python', [switch]$OneDirectory, [switch]$Desktop, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$projectDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dependencyDir = Join-Path $projectDir '.runtime\offline-build-deps'
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (@($dependencyDir, $previousPythonPath) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
    & $Python -c "import PyInstaller, cryptography, zstandard"
    if ($LASTEXITCODE -ne 0) { throw 'Install runtime and build requirements with the selected Python first.' }
    $bundleMode = '--onefile'
    if ($OneDirectory) { $bundleMode = '--onedir' }
    $entryName = 'local-chat'
    $entryScript = 'scripts\local_chat.py'
    $windowMode = '--console'
    if ($Desktop) {
        $entryName = 'local-chat-desktop'
        $entryScript = 'scripts\local_chat_bootstrap.py'
        $windowMode = '--windowed'
        & $Python -c "import tkinter; from PIL import Image, ImageTk; tkinter.Tcl().eval('info patchlevel')"
        if ($LASTEXITCODE -ne 0) { throw 'Tcl/Tk build resources are not readable. Desktop build stopped before producing an incomplete EXE.' }
    }
    $distDirectory = Join-Path $projectDir ('build\' + $entryName)
    if ($OutputDirectory) {
        $distDirectory = if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) } else { [IO.Path]::GetFullPath((Join-Path $projectDir $OutputDirectory)) }
    }
    & $Python -m PyInstaller --noconfirm $bundleMode $windowMode `
        --name $entryName --paths (Join-Path $projectDir 'web_mvp') `
        --paths (Join-Path $projectDir '..\poc') `
        --hidden-import zstandard --collect-all zstandard `
        --exclude-module container_bridge `
        --exclude-module numpy --exclude-module PIL.ImageQt `
        --distpath $distDirectory `
        --workpath (Join-Path $projectDir '.runtime\offline-pyinstaller') `
        --specpath (Join-Path $projectDir '.runtime\offline-pyinstaller') `
        (Join-Path $projectDir $entryScript)
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
