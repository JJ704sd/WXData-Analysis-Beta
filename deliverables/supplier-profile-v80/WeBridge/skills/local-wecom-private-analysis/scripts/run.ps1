[CmdletBinding()]
param(
    [ValidateSet('Desktop','Parse','ProfilePrepare','ProfileStage','ProfileReview','ProfileInsights','ProfileShow','ProfileOverview','ProfileLinkReview')]
    [string]$Mode = 'Desktop',
    [ValidateSet('direct')][string]$ConversationMode = 'direct',
    [string]$Chat,
    [string]$ChatName,
    [string]$DataDir,
    [string]$Store,
    [string]$ExportRoot,
    [string]$RequestId,
    [string]$Candidate,
    [string]$CandidateId,
    [string]$OutputDirectory,
    [string]$Keyword,
    [ValidateRange(0,2147483647)][int]$Offset = 0,
    [ValidateRange(1,1000000)][int]$Limit = 100,
    [string]$Client = 'wecom',
    [string]$AccountKey,
    [string]$PeerId,
    [string]$Identities,
    [ValidateRange(5,600)][int]$WaitSeconds = 300
)
$ErrorActionPreference = 'Stop'
$BoundClient = 'wecom'
if ($Client -ine $BoundClient) { throw 'This Skill is bound to the WeCom client.' }
if ($ConversationMode -ne 'direct') { throw 'This Skill only supports direct conversations.' }

$skillDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimePath = Join-Path $skillDirectory 'runtime.json'
if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) { throw 'The private-analysis runtime manifest is missing.' }
$runtime = [IO.File]::ReadAllText($runtimePath, [Text.Encoding]::UTF8) | ConvertFrom-Json
if ([string]$runtime.client -ne $BoundClient -or [string]$runtime.conversationMode -ne 'direct') { throw 'The Skill runtime is not bound to WeCom direct analysis.' }

function Resolve-BundledFile([string]$relative, [string]$expectedHash, [string]$label) {
    if ([string]::IsNullOrWhiteSpace($relative) -or [IO.Path]::IsPathRooted($relative)) { throw "Invalid $label runtime path." }
    $root = (Join-Path $skillDirectory 'bin').TrimEnd('\') + '\'
    $path = [IO.Path]::GetFullPath((Join-Path $skillDirectory $relative))
    if (-not $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw "Invalid $label runtime path." }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "The $label EXE is missing." }
    $stream = [IO.File]::OpenRead($path); $hasher = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
    if ($hash -ne ([string]$expectedHash).ToLowerInvariant()) { throw "The $label EXE checksum does not match the Skill package." }
    return $path
}

$desktopPath = Resolve-BundledFile ([string]$runtime.desktopExeRelativePath) ([string]$runtime.desktopExeSha256) 'desktop'
$profilePath = Resolve-BundledFile ([string]$runtime.profileCliRelativePath) ([string]$runtime.profileCliSha256) 'profile'
if (-not $DataDir) { $DataDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'LocalWeComArchive' }
if (-not $Store) { $Store = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'LocalChatPrivateProfiles' }
$dataDirectory = [IO.Path]::GetFullPath($DataDir)
$storeDirectory = [IO.Path]::GetFullPath($Store)

function Write-JsonResult($value) {
    $previous = [Console]::OutputEncoding
    try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); [Console]::Out.WriteLine(($value | ConvertTo-Json -Depth 16 -Compress)) }
    finally { [Console]::OutputEncoding = $previous }
}
function Invoke-Profile([string[]]$Arguments) {
    $previous = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
        $output = & $profilePath '--store' $storeDirectory '--bound-client' $BoundClient @Arguments 2>&1
        $code = $LASTEXITCODE
        foreach ($line in $output) { [Console]::WriteLine([string]$line) }
        if ($code -ne 0) { exit $code }
    } finally { [Console]::OutputEncoding = $previous }
}
function Try-ReadJson([string]$Path) {
    try {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
        $text = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return ($text | ConvertFrom-Json)
    } catch { return $null }
}
function Wait-ReadableJson([string]$Path, [int]$TimeoutSeconds = 10) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $value = Try-ReadJson $Path
        if ($null -ne $value) { return $value }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "The private desktop helper result remained unreadable after ${TimeoutSeconds}s (state=waiting). Keep this request and inspect diagnostics; do not start another request."
}
function Test-SameOrUnder([string]$Candidate, [string]$Root) {
    $candidatePath = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    return $candidatePath.Equals($rootPath, [StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)
}

switch ($Mode) {
    'ProfilePrepare' {
        if (-not $ExportRoot) { throw 'ProfilePrepare requires -ExportRoot.' }
        Invoke-Profile @('prepare','--export-root',[IO.Path]::GetFullPath($ExportRoot)); exit 0
    }
    'ProfileStage' {
        if (-not $RequestId -or -not $Candidate) { throw 'ProfileStage requires -RequestId and -Candidate.' }
        Invoke-Profile @('stage','--request-id',$RequestId,'--candidate',[IO.Path]::GetFullPath($Candidate)); exit 0
    }
    'ProfileReview' {
        if (-not $CandidateId) { throw 'ProfileReview requires -CandidateId.' }
        Invoke-Profile @('review','--candidate-id',$CandidateId); exit 0
    }
    'ProfileInsights' {
        if (-not $ExportRoot) { throw 'ProfileInsights requires -ExportRoot.' }
        $insightsArguments = @('insights','--export-root',[IO.Path]::GetFullPath($ExportRoot))
        if ($PSBoundParameters.ContainsKey('OutputDirectory')) {
            if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { throw 'OutputDirectory cannot be empty.' }
            $outputPath = [IO.Path]::GetFullPath($OutputDirectory)
            if (Test-Path -LiteralPath $outputPath) { throw 'OutputDirectory must be a new directory.' }
            if ((Test-SameOrUnder $outputPath ([IO.Path]::GetFullPath($ExportRoot))) -or
                (Test-SameOrUnder $outputPath $storeDirectory)) {
                throw 'OutputDirectory must be outside the formal export and profile store.'
            }
            $insightsArguments += @('--output-dir',$outputPath)
        }
        if ($PSBoundParameters.ContainsKey('Keyword')) { $insightsArguments += @('--keyword',$Keyword) }
        if ($PSBoundParameters.ContainsKey('Offset')) { $insightsArguments += @('--offset',[string]$Offset) }
        if ($PSBoundParameters.ContainsKey('Limit')) { $insightsArguments += @('--limit',[string]$Limit) }
        Invoke-Profile $insightsArguments; exit 0
    }
    'ProfileShow' {
        if (-not $AccountKey -or -not $PeerId) { throw 'ProfileShow requires -AccountKey and -PeerId.' }
        Invoke-Profile @('show','--client',$BoundClient,'--account-key',$AccountKey,'--peer-id',$PeerId); exit 0
    }
    'ProfileOverview' {
        if (-not $AccountKey) { throw 'ProfileOverview requires -AccountKey.' }
        Invoke-Profile @('overview','--client',$BoundClient,'--account-key',$AccountKey); exit 0
    }
    'ProfileLinkReview' {
        if (-not $Identities) { throw 'ProfileLinkReview requires -Identities.' }
        Invoke-Profile @('link-review','--identities',[IO.Path]::GetFullPath($Identities)); exit 0
    }
}

if ($Mode -eq 'Parse' -and $Chat -and $ChatName) { throw 'Select exactly one of -Chat or -ChatName.' }
if ($Mode -eq 'Parse' -and -not ($Chat -or $ChatName)) { throw 'Parse requires -Chat or -ChatName.' }
[IO.Directory]::CreateDirectory($dataDirectory) | Out-Null
$requestDirectory = Join-Path $dataDirectory 'requests'; [IO.Directory]::CreateDirectory($requestDirectory) | Out-Null
$launchId = [Guid]::NewGuid().ToString('N')
$launchPath = Join-Path $requestDirectory ($launchId + '.desktop.json')
$launchResult = Join-Path $requestDirectory ($launchId + '.launch.json')
$launchTask = @{version=1; client=$BoundClient; conversationMode='direct'}
$receiptPath = $null
if ($Chat -or $ChatName) {
    $receiptPath = Join-Path $requestDirectory ($launchId + '.open.json')
    $launchTask.receipt = $receiptPath
    if ($Chat) { $launchTask.chatId = $Chat } else { $launchTask.chatName = $ChatName }
}
[IO.File]::WriteAllText($launchPath, ($launchTask | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
& $desktopPath '--client' $BoundClient '--conversation-mode' 'direct' '--launch-desktop-json' $launchPath '--result-json' $launchResult '--data-dir' $dataDirectory | Out-Null
$launchExit = $LASTEXITCODE
if ($launchExit -ne 0) { throw "The private desktop helper exited with code $launchExit (state=failed)." }
$launchReceipt = Wait-ReadableJson $launchResult
if (-not $launchReceipt.ok) { throw "The private desktop helper did not start (state=$([string]$launchReceipt.state))." }
if (-not $receiptPath) { Write-JsonResult ([pscustomobject]@{ok=$true;action='desktop';state='started';conversationMode='direct';client=$BoundClient}); exit 0 }
$deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds); $receipt = $null
do {
    if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
        $receipt = Try-ReadJson $receiptPath
        if ($null -eq $receipt) { Start-Sleep -Milliseconds 200; continue }
        if ($receipt.state -in @('completed','awaiting_review','needs_source','needs_chat','failed')) { break }
        if ($receipt.processId -and -not (Get-Process -Id $receipt.processId -ErrorAction SilentlyContinue)) { $receipt = [pscustomobject]@{ok=$false;state='failed';message='The desktop parser exited before completion.'}; break }
    }
    Start-Sleep -Milliseconds 500
} while ([DateTime]::UtcNow -lt $deadline)
if (-not $receipt -or $receipt.state -notin @('completed','awaiting_review','needs_source','needs_chat','failed')) { $receipt = [pscustomobject]@{ok=$false;state='waiting';message='The parser is still running; read the result for this request.'} }
if (-not $receipt.PSObject.Properties['ok']) { $receipt | Add-Member -NotePropertyName ok -NotePropertyValue ($receipt.state -eq 'completed') }
$receipt | Add-Member -NotePropertyName action -NotePropertyValue 'parse' -Force
$receipt | Add-Member -NotePropertyName conversationMode -NotePropertyValue 'direct' -Force
$receipt | Add-Member -NotePropertyName resultFile -NotePropertyValue $receiptPath -Force
Write-JsonResult $receipt
if ($receipt.state -eq 'failed') { exit 2 }
exit 0
