[CmdletBinding()]
param([string]$Python = 'python', [Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destination = [IO.Path]::GetFullPath($OutputDirectory)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $projectDirectory 'build')).TrimEnd('\') + '\'
if (-not $destination.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Private build output must be a new directory inside this project build directory.'
}
if (Test-Path -LiteralPath $destination) { throw 'Build output already exists; select a new directory.' }
$dependencies = Join-Path $projectDirectory '.runtime\offline-build-deps'
$priorPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (@($dependencies, $priorPath) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
    & $Python -c "import PyInstaller, cryptography, zstandard"
    if ($LASTEXITCODE -ne 0) { throw 'Install runtime and build requirements with the selected Python first.' }
    & $Python -c "import tkinter; from PIL import Image, ImageTk; tkinter.Tcl().eval('info patchlevel')"
    if ($LASTEXITCODE -ne 0) { throw 'Tcl/Tk build preflight failed.' }
    foreach ($entry in @(
        @{Name='local-chat-desktop'; Script='local_chat_bootstrap.py'; Window='--windowed'},
        @{Name='private-chat-profile'; Script='private_chat.py'; Window='--console'}
    )) {
        & $Python -m PyInstaller --noconfirm --onefile $entry.Window --name $entry.Name `
            --paths (Join-Path $projectDirectory 'web_mvp') --paths $PSScriptRoot `
            --paths (Join-Path $projectDirectory '..\poc') `
            --hidden-import zstandard --collect-all zstandard `
            --exclude-module container_bridge --exclude-module numpy --exclude-module PIL.ImageQt `
            --distpath $destination `
            --workpath (Join-Path $projectDirectory '.runtime\private-pyinstaller') `
            --specpath (Join-Path $projectDirectory '.runtime\private-pyinstaller') `
            (Join-Path $PSScriptRoot $entry.Script)
        if ($LASTEXITCODE -ne 0) { throw ('Build failed: ' + $entry.Name) }
    }
} finally {
    $env:PYTHONPATH = $priorPath
}
