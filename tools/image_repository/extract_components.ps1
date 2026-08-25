param(
  [Parameter(Mandatory = $true)][string]$ProjectRoot,
  [Parameter(Mandatory = $true)][string]$WorkDir
)

$ErrorActionPreference = 'Stop'
$rawDir = Join-Path $WorkDir 'raw'
$previewDir = Join-Path $rawDir 'previews'
New-Item -ItemType Directory -Force -Path $rawDir, $previewDir | Out-Null

function Get-RgbHex([object]$colorFormat) {
  try {
    $rgb = [int64]$colorFormat.RGB
    if ($rgb -lt 0) { return $null }
    $r = $rgb -band 255
    $g = ($rgb -shr 8) -band 255
    $b = ($rgb -shr 16) -band 255
    return ('#{0:X2}{1:X2}{2:X2}' -f $r, $g, $b)
  } catch { return $null }
}

function Get-ShapeText([object]$shape) {
  try {
    if ($shape.HasTable -eq -1) {
      $parts = @()
      for ($r = 1; $r -le $shape.Table.Rows.Count; $r++) {
        for ($c = 1; $c -le $shape.Table.Columns.Count; $c++) {
          try { $parts += [string]$shape.Table.Cell($r, $c).Shape.TextFrame.TextRange.Text } catch {}
        }
      }
      return ($parts -join ' ').Trim()
    }
  } catch {}
  try {
    if ($shape.TextFrame.HasText -eq -1) { return ([string]$shape.TextFrame.TextRange.Text).Trim() }
  } catch {}
  try {
    if ($shape.TextFrame2.HasText -eq -1) { return ([string]$shape.TextFrame2.TextRange.Text).Trim() }
  } catch {}
  return ''
}

function Clear-ShapeText([object]$shape) {
  try {
    if ($shape.Type -eq 6) {
      for ($i = 1; $i -le $shape.GroupItems.Count; $i++) { Clear-ShapeText $shape.GroupItems.Item($i) }
    }
  } catch {}
  try {
    if ($shape.HasTable -eq -1) {
      for ($r = 1; $r -le $shape.Table.Rows.Count; $r++) {
        for ($c = 1; $c -le $shape.Table.Columns.Count; $c++) {
          try { $shape.Table.Cell($r, $c).Shape.TextFrame.TextRange.Text = '' } catch {}
        }
      }
    }
  } catch {}
  try { if ($shape.TextFrame.HasText -eq -1) { $shape.TextFrame.TextRange.Text = '' } } catch {}
  try { if ($shape.TextFrame2.HasText -eq -1) { $shape.TextFrame2.TextRange.Text = '' } } catch {}
}

function Get-ShapeStyle([object]$shape) {
  $fillVisible = $false
  $lineVisible = $false
  $fillType = $null
  $fillColor = $null
  $fillBackColor = $null
  $gradientStops = @()
  $lineColor = $null
  $lineWeight = $null
  $lineDash = $null
  try {
    $fillVisible = ($shape.Fill.Visible -ne 0)
    if ($fillVisible) {
      $fillType = [int]$shape.Fill.Type
      $fillColor = Get-RgbHex $shape.Fill.ForeColor
      $fillBackColor = Get-RgbHex $shape.Fill.BackColor
      try {
        for ($i = 1; $i -le $shape.Fill.GradientStops.Count; $i++) {
          $stop = $shape.Fill.GradientStops.Item($i)
          $gradientStops += [ordered]@{ position = [math]::Round([double]$stop.Position, 4); color = Get-RgbHex $stop.Color }
        }
      } catch {}
    }
  } catch {}
  try {
    $lineVisible = ($shape.Line.Visible -ne 0)
    if ($lineVisible) {
      $lineColor = Get-RgbHex $shape.Line.ForeColor
      $lineWeight = [math]::Round([double]$shape.Line.Weight, 3)
      $lineDash = [int]$shape.Line.DashStyle
    }
  } catch {}
  return [ordered]@{
    fill_visible = $fillVisible
    fill_type = $fillType
    fill_color = $fillColor
    fill_back_color = $fillBackColor
    gradient_stops = $gradientStops
    line_visible = $lineVisible
    line_color = $lineColor
    line_weight = $lineWeight
    line_dash = $lineDash
  }
}

function Test-Candidate([object]$shape, [object]$style) {
  try { if ($shape.Visible -eq 0) { return $false } } catch {}
  $type = [int]$shape.Type
  if ($type -in @(4, 15, 18)) { return $false }
  $text = Get-ShapeText $shape
  if (($type -in @(14, 17)) -and -not $style.fill_visible -and -not $style.line_visible) { return $false }
  if (($type -eq 1) -and $text.Length -gt 0 -and -not $style.fill_visible -and -not $style.line_visible) { return $false }
  return ($type -in @(1, 2, 3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 19, 20, 21, 24, 28, 29, 30, 31))
}

function Get-Category([object]$shape, [object]$style, [double]$slideW, [double]$slideH) {
  $type = [int]$shape.Type
  $name = ([string]$shape.Name).ToLowerInvariant()
  $alt = ''
  try { $alt = (([string]$shape.AlternativeText) + ' ' + ([string]$shape.Title)).ToLowerInvariant() } catch {}
  $key = "$name $alt"
  $w = [math]::Max(0.1, [double]$shape.Width)
  $h = [math]::Max(0.1, [double]$shape.Height)
  $areaRatio = ($w * $h) / [math]::Max(1.0, $slideW * $slideH)
  $aspect = $w / $h
  $auto = $null
  try { $auto = [int]$shape.AutoShapeType } catch {}
  try { if ($shape.HasChart -eq -1) { return 'charts' } } catch {}
  try { if ($shape.HasTable -eq -1) { return 'tables' } } catch {}
  if ($areaRatio -ge 0.58) { return 'backgrounds' }
  if ($type -in @(11, 13, 28, 29)) {
    if ($key -match 'logo|emblem|brand|avic|aviation|徽标|标志|品牌') { return 'logos' }
    if ($key -match 'icon|pictogram|target|pie|car|chart|bank|people|crosshair|lightbulb|head|arrow|house|shield|book|star|checklist|鸟|徽章|图标') { return 'icons' }
    if (($areaRatio -le 0.055) -and ($w -le $slideW * 0.30) -and ($h -le $slideH * 0.30)) { return 'icons' }
    return 'decorative_visuals'
  }
  if ($type -eq 9) {
    try { if ($shape.Connector -eq -1) { return 'connectors' } } catch {}
    return 'lines'
  }
  if (($key -match 'arrow|chevron|箭头|流程') -or (($null -ne $auto) -and ($auto -ge 33) -and ($auto -le 63))) { return 'arrows' }
  if ($key -match 'badge|pill|tag|number|tab|标签|角标|编号') { return 'badges' }
  if ($key -match 'card|panel|container|banner|label|header|background|surface|box|卡片|面板|标题栏|底板|框') { return 'cards' }
  if (($type -eq 1) -and ($auto -in @(1, 5))) {
    if (($areaRatio -ge 0.012) -or ($aspect -ge 1.7)) { return 'cards' }
    return 'badges'
  }
  if ($type -eq 6) { return 'components' }
  if ($type -in @(5, 20, 21, 24, 30, 31)) { return 'decorative_shapes' }
  return 'basic_shapes'
}

$allFiles = Get-ChildItem -LiteralPath $ProjectRoot -Recurse -File -Filter '*.pptx' -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notlike '*\Image repository\*' -and $_.FullName -notlike '*\tmp\*' } |
  Sort-Object FullName

$hashSeen = @{}
$files = @()
foreach ($file in $allFiles) {
  $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
  if (-not $hashSeen.ContainsKey($hash)) {
    $hashSeen[$hash] = $true
    $files += [ordered]@{ path = $file.FullName; name = $file.Name; sha256 = $hash; length = $file.Length }
  }
}

$app = New-Object -ComObject PowerPoint.Application
$app.Visible = -1
try { $app.WindowState = 2 } catch {}
$records = @()
$errors = @()
$counter = 0

try {
  foreach ($fileInfo in $files) {
    Write-Output ("SCAN {0}" -f $fileInfo.path)
    $pres = $null
    try {
      $pres = $app.Presentations.Open($fileInfo.path, $false, $false, $false)
      $slideW = [double]$pres.PageSetup.SlideWidth
      $slideH = [double]$pres.PageSetup.SlideHeight
      foreach ($slide in $pres.Slides) {
        for ($shapeIndex = 1; $shapeIndex -le $slide.Shapes.Count; $shapeIndex++) {
          $shape = $slide.Shapes.Item($shapeIndex)
          $style = Get-ShapeStyle $shape
          if (-not (Test-Candidate $shape $style)) { continue }
          $counter++
          $rawId = ('raw-{0:D5}' -f $counter)
          $previewPath = Join-Path $previewDir ($rawId + '.png')
          $exportOk = $false
          $exportError = $null
          try {
            # Duplicate in-memory on the source slide, strip text, export, then
            # delete. The source deck is closed without saving.
            $range = $shape.Duplicate()
            $copy = $range.Item(1)
            Clear-ShapeText $copy
            try { $copy.Name = $rawId } catch {}
            $copy.Export($previewPath, 2)
            $exportOk = Test-Path -LiteralPath $previewPath
            $copy.Delete()
          } catch {
            $exportError = $_.Exception.Message
            try { if ($null -ne $copy) { $copy.Delete() } } catch {}
          }
          if (-not $exportOk) {
            $errors += [ordered]@{ raw_id = $rawId; deck = $fileInfo.path; slide = $slide.SlideIndex; shape_id = $shape.Id; error = $exportError }
            continue
          }
          $auto = $null
          try { $auto = [int]$shape.AutoShapeType } catch {}
          $rotation = 0.0
          try { $rotation = [math]::Round([double]$shape.Rotation, 3) } catch {}
          $alt = ''
          try { $alt = (([string]$shape.AlternativeText) + ' ' + ([string]$shape.Title)).Trim() } catch {}
          $category = Get-Category $shape $style $slideW $slideH
          $records += [ordered]@{
            raw_id = $rawId
            category = $category
            source_deck = $fileInfo.path
            source_deck_name = $fileInfo.name
            source_deck_sha256 = $fileInfo.sha256
            source_slide = [int]$slide.SlideIndex
            source_shape_index = [int]$shapeIndex
            source_shape_id = [int]$shape.Id
            source_shape_name = [string]$shape.Name
            source_alt_text = $alt
            shape_type = [int]$shape.Type
            auto_shape_type = $auto
            left = [math]::Round([double]$shape.Left, 3)
            top = [math]::Round([double]$shape.Top, 3)
            width = [math]::Round([double]$shape.Width, 3)
            height = [math]::Round([double]$shape.Height, 3)
            rotation = $rotation
            slide_width = [math]::Round($slideW, 3)
            slide_height = [math]::Round($slideH, 3)
            style = $style
            original_text_removed = (Get-ShapeText $shape).Length -gt 0
            preview = $previewPath
          }
        }
      }
    } catch {
      $errors += [ordered]@{ deck = $fileInfo.path; error = $_.Exception.Message }
    } finally {
      if ($null -ne $pres) {
        try { $pres.Saved = -1 } catch {}
        $pres.Close()
      }
    }
  }
} finally {
  try { $app.Quit() } catch {}
}

$manifest = [ordered]@{
  project_root = $ProjectRoot
  generated_at = (Get-Date).ToString('o')
  source_decks = $files
  record_count = $records.Count
  records = $records
  errors = $errors
}
$json = $manifest | ConvertTo-Json -Depth 12
[System.IO.File]::WriteAllText((Join-Path $rawDir 'raw-manifest.json'), $json, [System.Text.UTF8Encoding]::new($false))
Write-Output ("EXTRACTED={0} ERRORS={1} DECKS={2}" -f $records.Count, $errors.Count, $files.Count)
