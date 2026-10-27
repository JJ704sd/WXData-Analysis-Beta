"""Windows OCR, isolated in a bounded process; input (including passwords) uses stdin."""
import base64
import json
import os
import subprocess

# The script is bundled as source inside the executable, with no external runtime files.
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $types = @('Windows.Storage.StorageFile','Windows.Storage.Streams.IRandomAccessStream',
        'Windows.Storage.Streams.InMemoryRandomAccessStream','Windows.Graphics.Imaging.BitmapDecoder',
        'Windows.Graphics.Imaging.SoftwareBitmap','Windows.Graphics.Imaging.BitmapFrame','Windows.Media.Ocr.OcrEngine','Windows.Media.Ocr.OcrResult',
        'Windows.Data.Pdf.PdfDocument','Windows.Data.Pdf.PdfPageRenderOptions')
    foreach ($name in $types) { $null = [Type]::GetType("$name, Windows, ContentType=WindowsRuntime") }
    $generic = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Await($operation, [Type]$type) {
        $task = $generic.MakeGenericMethod($type).Invoke($null, @($operation))
        $task.GetAwaiter().GetResult()
    }
    function Emit($value) { [Console]::WriteLine(($value | ConvertTo-Json -Depth 8 -Compress)) }
    $inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) { Emit @{status='unavailable'}; exit }
    function RecognizeFrame($decoder, $page) {
        $bitmap = $null
        try {
            $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
            $scale = [Math]::Min(1.0, 4000.0 / [Math]::Max($decoder.PixelWidth, $decoder.PixelHeight))
            $transform.ScaledWidth = [uint32][Math]::Max(1, [Math]::Round($decoder.PixelWidth * $scale))
            $transform.ScaledHeight = [uint32][Math]::Max(1, [Math]::Round($decoder.PixelHeight * $scale))
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $transform,
                [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,
                [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $lines = @($result.Lines | ForEach-Object {
                $line = $_
                $words = @($line.Words | ForEach-Object {
                    @{text=$_.Text; x=$_.BoundingRect.X; y=$_.BoundingRect.Y;
                        width=$_.BoundingRect.Width; height=$_.BoundingRect.Height}
                })
                @{text=$line.Text; words=$words}
            })
            Emit @{page=$page; text=(($lines | ForEach-Object {$_.text}) -join "`n");
                lines=$lines; width=$bitmap.PixelWidth; height=$bitmap.PixelHeight;
                textAngle=$result.TextAngle; language=$engine.RecognizerLanguage.LanguageTag}
        } finally { if ($null -ne $bitmap) { $bitmap.Dispose() } }
    }
    function RecognizeFile($path, $number, $allFrames) {
        $stream = $null
        try {
            $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
            $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $count = if ($allFrames) { [Math]::Min(100, $decoder.FrameCount) } else { 1 }
            for ($index=0; $index -lt $count; $index++) {
                $pageNumber = if ($allFrames) {$index+1} else {$number}
                try {
                    $frame = Await ($decoder.GetFrameAsync([uint32]$index)) ([Windows.Graphics.Imaging.BitmapFrame])
                    RecognizeFrame $frame $pageNumber
                } catch { Emit @{page=$pageNumber; error='page_failed'} }
            }
        } catch { Emit @{page=$number; error='page_failed'} }
        finally { if ($null -ne $stream) { $stream.Dispose() } }
    }
    if ($inputData.pdf) {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($inputData.path)) ([Windows.Storage.StorageFile])
        $password = [string]$inputData.password
        $pdf = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file, $password)) ([Windows.Data.Pdf.PdfDocument])
        foreach ($index in $inputData.pages) {
            $page = $null; $stream = $null
            try {
                $page = $pdf.GetPage([uint32]$index)
                $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
                $scale = [Math]::Min(2.5, 4000.0 / [Math]::Max($page.Size.Width, $page.Size.Height))
                $options.DestinationWidth = [uint32][Math]::Max(1, [Math]::Round($page.Size.Width * $scale))
                $options.DestinationHeight = [uint32][Math]::Max(1, [Math]::Round($page.Size.Height * $scale))
                $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
                $actionMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
                    $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
                } | Select-Object -First 1
                $task = $actionMethod.Invoke($null, @($page.RenderToStreamAsync($stream, $options)))
                $task.GetAwaiter().GetResult()
                $stream.Seek(0)
                $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
                RecognizeFrame $decoder ([int]$index + 1)
            } catch { Emit @{page=([int]$index + 1); error='page_failed'} }
            finally {
                if ($null -ne $stream) { $stream.Dispose() }
                if ($null -ne $page) { $page.Dispose() }
            }
        }
    } elseif ($inputData.images) {
        $number=0
        foreach ($path in $inputData.images) { $number++; RecognizeFile $path $number $false }
    } else { RecognizeFile $inputData.path 1 $true }
    Emit @{status='complete'}
} catch { Emit @{status='failed'} }
'''


def recognize(path, *, pages=None, password=None, images=None, timeout=60):
    if os.name != 'nt':
        return {'status': 'unavailable', 'pages': [], 'engine': 'Windows.Media.Ocr'}
    command = base64.b64encode(SCRIPT.encode('utf-16-le')).decode('ascii')
    payload = json.dumps({'path': str(path), 'pdf': pages is not None,
                          'pages': pages, 'password': password,
                          'images': [str(p) for p in images] if images is not None else None}, ensure_ascii=True)
    status = 'failed'
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', command],
            input=payload.encode('ascii'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        output = result.stdout
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b''
        status = 'timeout'
    except OSError:
        output = b''
        status = 'unavailable'
    records = []
    for line in output.decode('utf-8-sig', errors='replace').splitlines():
        try:
            value = json.loads(line)
            if 'page' in value:
                records.append(value)
            elif 'status' in value and status != 'timeout':
                status = value['status']
        except (ValueError, TypeError):
            continue
    return {'status': status, 'pages': records, 'engine': 'Windows.Media.Ocr'}
