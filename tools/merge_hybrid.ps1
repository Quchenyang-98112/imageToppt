param([string]$BasePptx, [string]$ExtraPptx, [string]$OutputPptx)
$app = New-Object -ComObject PowerPoint.Application
$base = $app.Presentations.Open((Resolve-Path -LiteralPath $BasePptx).Path, $false, $false, $false)
$extra = $app.Presentations.Open((Resolve-Path -LiteralPath $ExtraPptx).Path, $false, $false, $false)
# Existing seven-page deck contains the stronger editable reconstructions for
# 李佳1/2/3、识别1/2/3 and b60. Append the newly rebuilt saas and 养老 pages.
$extra.Slides.Item(2).Copy(); $base.Slides.Paste() | Out-Null
$extra.Slides.Item(3).Copy(); $base.Slides.Paste() | Out-Null
$base.SaveAs((Join-Path (Get-Location) $OutputPptx), 24)
$extra.Close(); $base.Close(); $app.Quit()
