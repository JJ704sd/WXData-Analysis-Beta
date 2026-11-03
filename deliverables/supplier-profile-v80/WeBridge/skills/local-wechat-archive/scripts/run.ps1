[CmdletBinding()]
param(
    [ValidateSet('WeChat','WeCom')][string]$Client,
    [ValidateSet('Parse','Desktop','Discover','Groups','Export','RecoverKeys')][string]$Mode = 'Parse',
    [string]$Source,
    [string]$Keys,
    [string]$ImageKeys,
    [string]$SelfId,
    [string]$Chat,
    [string]$ChatName,
    [string]$StartDate,
    [string]$EndDate,
    [string]$DataDir,
    [string[]]$ScanRoot,
    [ValidateRange(5,600)][int]$WaitSeconds = 300,
    [switch]$NoMedia
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
if ($Mode -eq 'Export') {
    throw 'Direct export is disabled in Skill. Open -Mode Desktop, review the parsed group and confirm in the window.'
}
$skillDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
$runtimePath = Join-Path $skillDirectory 'runtime.json'
if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) { throw 'The runtime manifest is missing.' }
$runtime = [IO.File]::ReadAllText($runtimePath, [Text.Encoding]::UTF8) | ConvertFrom-Json
if ($runtime.PSObject.Properties['client']) {
    $bundledClient = [string]$runtime.client
    if ($bundledClient -notin @('wechat','wecom')) { throw 'Invalid bundled client.' }
    if ($Client -and $Client -ine $bundledClient) { throw 'This skill is bound to a different client. Use its matching WeChat or WeCom skill.' }
    $Client = $bundledClient
} elseif (-not $Client) { $Client = 'WeChat' }
$binaryRelativePath = 'bin\local-chat-desktop.exe'
if ($runtime.PSObject.Properties['exeRelativePath']) { $binaryRelativePath = [string]$runtime.exeRelativePath }
$binaryPath = [IO.Path]::GetFullPath((Join-Path $skillDirectory $binaryRelativePath))
$binaryPrefix = (Join-Path $skillDirectory 'bin').TrimEnd('\') + '\'
if (-not $binaryPath.StartsWith($binaryPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid bundled runtime path.' }
if (-not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) { throw 'The portable EXE is missing. Use the complete skill package.' }
$binaryStream = [IO.File]::OpenRead($binaryPath)
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $binaryHash = [BitConverter]::ToString($hasher.ComputeHash($binaryStream)).Replace('-','').ToLowerInvariant()
} finally {
    $binaryStream.Dispose()
    $hasher.Dispose()
}
if ($binaryHash -ne $runtime.exeSha256) {
    throw 'The EXE checksum does not match this skill package.'
}
if (-not $DataDir) {
    $profileName = if ($Client -eq 'WeCom') { 'LocalWeComArchive' } else { 'LocalWechatArchive' }
    $DataDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $profileName
}
$dataDirectory = [IO.Path]::GetFullPath($DataDir)
function Write-JsonResult($value) {
    $previousEncoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
        [Console]::Out.WriteLine(($value | ConvertTo-Json -Depth 12 -Compress))
    } finally { [Console]::OutputEncoding = $previousEncoding }
}
if ($Mode -in @('Parse','Desktop') -and ($Source -or $Keys -or $ImageKeys -or $SelfId -or $StartDate -or $EndDate -or $NoMedia)) {
    throw 'Select source/key paths and date/media filters in the Desktop window, then review before export.'
}
if ($Mode -in @('Parse','Desktop') -and $Chat -and $ChatName) { throw 'Select exactly one of -Chat or -ChatName.' }
if ($Mode -eq 'Export' -and ((-not $Chat -and -not $ChatName) -or ($Chat -and $ChatName))) {
    throw 'Export requires exactly one of -Chat or -ChatName.'
}
$task = @{version=1; action=$Mode.ToLowerInvariant(); client=$Client.ToLowerInvariant()}
if ($Mode -eq 'RecoverKeys') {
    if (-not $Source) { throw 'Key recovery requires the explicitly selected local live-account data directory.' }
    $task.action = 'recover_keys'
    $task.allowProcessRead = $true
}
$selectedConfig = $null
if ($Source) {
    $selectedConfig = @{sourceRoot=[IO.Path]::GetFullPath($Source); keyFile=''; imageKeyFile=''; selfId=''; includeMedia=(-not $NoMedia); includeVoice=$false}
} elseif ($Keys -or $ImageKeys -or $SelfId) {
    $settingsPath = Join-Path $dataDirectory 'settings.local.json'
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) { throw 'Select a data source first.' }
    $settings = [IO.File]::ReadAllText($settingsPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $selectedConfig = @{}
    foreach ($field in @('sourceRoot','keyFile','imageKeyFile','selfId','includeMedia','includeVoice')) {
        if ($settings.source.PSObject.Properties[$field]) { $selectedConfig[$field] = $settings.source.$field }
    }
}
if ($selectedConfig) {
    if ($Keys) { $selectedConfig.keyFile = [IO.Path]::GetFullPath($Keys) }
    if ($ImageKeys) { $selectedConfig.imageKeyFile = [IO.Path]::GetFullPath($ImageKeys) }
    if ($SelfId) { $selectedConfig.selfId = $SelfId }
    $task.source = $selectedConfig
}
if ($ScanRoot) { $task.roots = @($ScanRoot | ForEach-Object { [IO.Path]::GetFullPath($_) }) }
if ($Chat) { $task.chatId = $Chat }
if ($ChatName) { $task.chatName = $ChatName }
if ($StartDate) { $task.startDate = $StartDate }
if ($EndDate) { $task.endDate = $EndDate }
if ($NoMedia) { $task.copyMedia = $false }

# Reject an input-account directory before writing any task file there.
$guardConfig = $selectedConfig
if (-not $guardConfig) {
    $settingsPath = Join-Path $dataDirectory 'settings.local.json'
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        $guardConfig = ([IO.File]::ReadAllText($settingsPath, [Text.Encoding]::UTF8) | ConvertFrom-Json).source
    }
}
if ($guardConfig -and $guardConfig.sourceRoot) {
    $sourceDirectory = [IO.Path]::GetFullPath($guardConfig.sourceRoot)
    if (Test-Path -LiteralPath $sourceDirectory) { $sourceDirectory = (Resolve-Path -LiteralPath $sourceDirectory).Path }
    $accountDirectory = $sourceDirectory
    if ([IO.Path]::GetFileName($sourceDirectory.TrimEnd('\')) -in @('db_storage','Data')) { $accountDirectory = [IO.Path]::GetDirectoryName($sourceDirectory) }
    $accountPrefix = $accountDirectory.TrimEnd('\') + '\'
    if ($dataDirectory.Equals($accountDirectory, [StringComparison]::OrdinalIgnoreCase) -or $dataDirectory.StartsWith($accountPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Application data must stay outside the selected WeChat account directory.'
    }
}
[IO.Directory]::CreateDirectory($dataDirectory) | Out-Null
if ($Mode -in @('Parse','Desktop')) {
    $requestDirectory = Join-Path $dataDirectory 'requests'
    [IO.Directory]::CreateDirectory($requestDirectory) | Out-Null
    $launchId = [Guid]::NewGuid().ToString('N')
    $launchPath = Join-Path $requestDirectory ($launchId + '.desktop.json')
    $launchResult = Join-Path $requestDirectory ($launchId + '.launch.json')
    $launchTask = @{version=1;client=$Client.ToLowerInvariant()}
    if ($ScanRoot) { $launchTask.roots = $task.roots }
    $receiptPath = $null
    if ($Chat -or $ChatName) {
        $receiptPath = Join-Path $requestDirectory ($launchId + '.open.json')
        $launchTask.receipt = $receiptPath
        if ($Chat) { $launchTask.chatId = $Chat }
        if ($ChatName) { $launchTask.chatName = $ChatName }
    }
    [IO.File]::WriteAllText($launchPath, ($launchTask | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
    # Invoke the bundled helper directly. Explorer opens its standard shortcut
    # as a desktop app; no COM, WMI or host/security/proxy changes are needed.
    & $binaryPath --client $Client.ToLowerInvariant() --launch-desktop-json $launchPath --result-json $launchResult --data-dir $dataDirectory | Out-Null
    $launchExit = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath $launchResult -PathType Leaf)) { throw 'The desktop helper returned no result. Open Start.cmd and inspect startup logs.' }
    $launchReceipt = [IO.File]::ReadAllText($launchResult, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($launchExit -ne 0 -or -not $launchReceipt.ok) { throw $launchReceipt.error }
    if (-not $receiptPath) {
        Write-JsonResult ([pscustomobject]@{ok=$true; action='desktop'; state='started'; dataDirectory=$dataDirectory})
        exit 0
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    $receipt = $null
    do {
        if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
            $receipt = [IO.File]::ReadAllText($receiptPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
            if ($receipt.state -in @('completed','awaiting_review','needs_source','needs_chat','failed')) { break }
            if ($receipt.processId -and -not (Get-Process -Id $receipt.processId -ErrorAction SilentlyContinue)) {
                $receipt = [pscustomobject]@{ok=$false;state='failed';message='The desktop parser exited before completion.'}
                break
            }
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $receipt -or $receipt.state -notin @('completed','awaiting_review','needs_source','needs_chat','failed')) {
        $receipt = [pscustomobject]@{ok=$false;state='waiting';message='The parser is still running. Read resultFile for this exact request; do not start another request.'}
    }
    if (-not $receipt.PSObject.Properties['ok']) { $receipt | Add-Member -NotePropertyName ok -NotePropertyValue ($receipt.state -eq 'completed') }
    $receipt | Add-Member -NotePropertyName action -NotePropertyValue 'parse' -Force
    $receipt | Add-Member -NotePropertyName resultFile -NotePropertyValue $receiptPath -Force
    $receipt | Add-Member -NotePropertyName dataDirectory -NotePropertyValue $dataDirectory -Force
    Write-JsonResult $receipt
    if ($receipt.state -eq 'failed') { exit 2 }
    exit 0
}
$requestDirectory = Join-Path $dataDirectory 'requests'
[IO.Directory]::CreateDirectory($requestDirectory) | Out-Null
$requestId = [Guid]::NewGuid().ToString('N')
$requestPath = Join-Path $requestDirectory ($requestId + '.request.json')
$resultPath = Join-Path $requestDirectory ($requestId + '.result.json')
[IO.File]::WriteAllText($requestPath, ($task | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
# A native command pipeline waits for the windowed executable to finish.
& $binaryPath --client $Client.ToLowerInvariant() --task-json $requestPath --result-json $resultPath --data-dir $dataDirectory | Out-Null
$exitCode = $LASTEXITCODE
if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { throw 'The parser returned no result. Check file permissions and paths.' }
$resultText = [IO.File]::ReadAllText($resultPath, [Text.Encoding]::UTF8)
$parsedResult = $resultText | ConvertFrom-Json
$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    [Console]::WriteLine($resultText)
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
if ($exitCode -ne 0 -or -not $parsedResult.ok) { exit 2 }
exit 0
