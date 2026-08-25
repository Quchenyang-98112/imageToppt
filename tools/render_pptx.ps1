param([string]$PptxPath, [string]$OutDir)
$app = New-Object -ComObject PowerPoint.Application
# PowerPoint may reject hiding the COM application in desktop mode; exports work either way.
$pres = $app.Presentations.Open((Resolve-Path -LiteralPath $PptxPath).Path, $true, $false, $false)
$OutDir = (New-Item -ItemType Directory -Force -Path $OutDir).FullName
$i = 1
foreach ($slide in $pres.Slides) {
  $slide.Export((Join-Path $OutDir ("slide-{0:00}.png" -f $i)), 'PNG', 1600, 900)
  $i++
}
$pres.Close()
$app.Quit()
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($pres) | Out-Null
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null
