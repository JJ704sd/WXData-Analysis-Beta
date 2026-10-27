[CmdletBinding()]
param([string]$Python = 'python', [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$version = & $Python -c "import sys; sys.path.insert(0, sys.argv[1]); from chat_release import DAILY_REPORT_VERSION; print(DAILY_REPORT_VERSION)" $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not read the daily-report version.' }
$builderVersion = & $Python -m PyInstaller --version
if ($LASTEXITCODE -ne 0 -or $builderVersion -ne '6.22.3') { throw 'The build machine requires PyInstaller 6.22.3.' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $project ('build\daily-' + $version) }
$destination = [IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $project 'build')).TrimEnd('\') + '\'
if (-not $destination.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must stay inside WeBridge/build.'
}
if (Test-Path -LiteralPath $destination) { throw 'Choose a new build directory.' }
$dailyPreviousCache = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $destination 'cache'
try {
$pythonLicense = & $Python -c "import sys; from pathlib import Path; print(Path(sys.base_prefix) / 'LICENSE.txt')"
$builderLicense = & $Python -c "import importlib.metadata; print(importlib.metadata.distribution('pyinstaller').locate_file('pyinstaller-6.22.3.dist-info/licenses/COPYING.txt'))"
$standalone = Join-Path $destination 'standalone'
& $Python (Join-Path $PSScriptRoot 'build_daily_standalone.py') --output $standalone
if ($LASTEXITCODE -ne 0) { throw 'Standalone source generation failed.' }
foreach ($client in @('wechat', 'wecom')) {
    & $Python -m PyInstaller --onefile --console --noupx --clean --noconfirm `
        --name ($client + '-daily-report') `
        --distpath (Join-Path $destination 'bin') `
        --workpath (Join-Path $destination ('work\' + $client)) `
        --specpath (Join-Path $destination 'spec') `
        (Join-Path $standalone ($client + '_daily_report.py'))
    if ($LASTEXITCODE -ne 0) { throw ('The ' + $client + ' executable build failed.') }
}
& $Python (Join-Path $PSScriptRoot 'package_daily_reports.py') `
    --binary-dir (Join-Path $destination 'bin') --standalone-dir $standalone `
    --output-root (Join-Path $destination 'packages') `
    --python-license $pythonLicense --pyinstaller-license $builderLicense
if ($LASTEXITCODE -ne 0) { throw 'Daily-report packaging failed.' }
} finally {
    if ($null -eq $dailyPreviousCache) { Remove-Item Env:PYINSTALLER_CONFIG_DIR -ErrorAction SilentlyContinue }
    else { $env:PYINSTALLER_CONFIG_DIR = $dailyPreviousCache }
}
