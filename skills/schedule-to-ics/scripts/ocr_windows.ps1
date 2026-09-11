# 课表照片 OCR 兜底（Windows.Media.Ocr，Windows 10/11 自带）。
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\ocr_windows.ps1 课表.png [作息时间表.png ...]
#
# 输出格式与 macOS 版一致：行 y≈226: x=26 第1节 | x=150 高等数学
# x、y 都是图片宽高的千分比，x 相近的是同一列，y 越小越靠上。
# 前提：系统装了中文 OCR 语言包（设置 → 时间和语言 → 语言和区域 → 中文(简体) 的「可选语言功能」）。

param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$Path
)

$ErrorActionPreference = 'Stop'

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
}
catch {
    Write-Error "无法加载 System.Runtime.WindowsRuntime：请在 Windows PowerShell 5.1 或 PowerShell 7 里运行。"
}

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    Write-Error "系统没有可用的 OCR 引擎：请在「设置 → 时间和语言 → 语言和区域」里给中文安装语言包（含 OCR 组件）。"
}

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

foreach ($item in $Path) {
    $full = (Resolve-Path -LiteralPath $item).Path
    Write-Output "== $full =="
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

        # OCR 引擎只吃 Bgra8 / Gray8，灰度 JPEG 解出来是 Gray8，统一转成 Bgra8
        if ($bitmap.BitmapPixelFormat -ne [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8) {
            $converted = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
                $bitmap,
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8
            )
            $bitmap.Dispose()
            $bitmap = $converted
        }

        $width = [double]$bitmap.PixelWidth
        $height = [double]$bitmap.PixelHeight
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

        foreach ($line in $result.Lines) {
            if ($line.Words.Count -eq 0) { continue }
            $y = [int](1000 * $line.Words[0].BoundingRect.Y / $height)
            $cells = @()
            $current = $null
            foreach ($word in $line.Words) {
                $rect = $word.BoundingRect
                $x = [int](1000 * $rect.X / $width)
                $right = [int](1000 * ($rect.X + $rect.Width) / $width)
                # 横向间隔超过 15‰ 视为换到下一个单元格
                if ($null -eq $current -or ($x - $current.right) -gt 15) {
                    if ($null -ne $current) { $cells += $current }
                    $current = [pscustomobject]@{ x = $x; right = $right; text = $word.Text }
                }
                else {
                    $current.text = $current.text + $word.Text
                    if ($right -gt $current.right) { $current.right = $right }
                }
            }
            if ($null -ne $current) { $cells += $current }
            $text = ($cells | ForEach-Object { "x=$($_.x) $($_.text)" }) -join ' | '
            Write-Output ("行 y≈{0}: {1}" -f $y, $text)
        }

        $bitmap.Dispose()
        $stream.Dispose()
    }
    catch {
        Write-Output "!! 读取失败：$full — $($_.Exception.Message)"
        Write-Output "   常见原因：路径写错、图片损坏、图片超过 OCR 尺寸上限。此时请让用户直接描述课表内容，不要卡在这里。"
    }
}

Write-Output ""
Write-Output "提示：OCR 会认错字（例如「星期三」→「星期=」），按列位置和上下文判断，拿不准就问用户。"
