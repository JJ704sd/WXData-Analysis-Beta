[CmdletBinding()]
param([string]$Python = 'python', [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $OutputDirectory) {
    $release = (& $Python (Join-Path $PSScriptRoot 'chat_release.py') | ConvertFrom-Json).desktopRelease
    if ($LASTEXITCODE -ne 0) { throw 'Could not read release matrix.' }
    $OutputDirectory = Join-Path $project ('build\' + $release)
}
$destination = [IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $project 'build')).TrimEnd('\') + '\'
if (-not $destination.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must stay inside WeBridge/build.'
}
if (Test-Path -LiteralPath $destination) { throw 'Choose a new build directory.' }
& $Python (Join-Path $PSScriptRoot 'sync_chat_release.py') --check
if ($LASTEXITCODE -ne 0) { throw 'Skill versions are stale.' }
$binaryDirectory = Join-Path $destination 'bin'
& (Join-Path $PSScriptRoot 'build_private_chat.ps1') -Python $Python -OutputDirectory $binaryDirectory
& $Python (Join-Path $PSScriptRoot 'package_four_chat.py') `
    --desktop-exe (Join-Path $binaryDirectory 'local-chat-desktop.exe') `
    --profile-cli-exe (Join-Path $binaryDirectory 'private-chat-profile.exe') `
    --output-root (Join-Path $destination 'packages')
if ($LASTEXITCODE -ne 0) { throw 'Four-product packaging failed.' }
