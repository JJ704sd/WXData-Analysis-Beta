[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('wechat','wecom')][string]$Client,
    [Parameter(Mandatory=$true)][ValidateSet('group','direct')][string]$ConversationMode,
    [string]$Python,
    [string]$DataDirectory,
    [string[]]$ScanRoot,
    [switch]$Capabilities
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $localPython) { $localPython } else { 'python' }
}
$arguments = @((Join-Path $PSScriptRoot 'WeBridge\scripts\local_chat_bootstrap.py'),
    '--client', $Client, '--conversation-mode', $ConversationMode)
if ($DataDirectory) { $arguments += @('--data-dir', $DataDirectory) }
foreach ($root in $ScanRoot) { $arguments += @('--scan-root', $root) }
if ($Capabilities) { $arguments += '--capabilities' }
& $Python @arguments
exit $LASTEXITCODE
