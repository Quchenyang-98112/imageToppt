param(
  [Parameter(Mandatory = $true)][string]$RepositoryDir,
  [string]$OnlyCategory = ''
)

$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $RepositoryDir 'manifest.json'
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$app = New-Object -ComObject PowerPoint.Application
$app.Visible = -1
try { $app.WindowState = 2 } catch {}

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

function Find-ShapeById([object]$slide, [int]$shapeId) {
  for ($i = 1; $i -le $slide.Shapes.Count; $i++) {
    $shape = $slide.Shapes.Item($i)
    if ([int]$shape.Id -eq $shapeId) { return $shape }
  }
  return $null
}

function Fit-Shape([object]$shape, [string]$category) {
  $maxW = 760.0
  $maxH = 390.0
  $w = [math]::Max(0.1, [double]$shape.Width)
  $h = [math]::Max(0.1, [double]$shape.Height)
  $factor = [math]::Min(1.0, [math]::Min($maxW / $w, $maxH / $h))
  if ($category -in @('icons', 'logos', 'badges', 'basic_shapes')) {
    $long = [math]::Max($w, $h)
    if ($long -lt 125) { $factor = [math]::Min([math]::Min($maxW / $w, $maxH / $h), 125 / $long) }
  }
  if ($category -in @('lines', 'connectors')) {
    if ($w -lt 360 -and $w -gt 0.1) { $factor = [math]::Min($maxW / $w, 360 / $w) }
  }
  if ([math]::Abs($factor - 1.0) -gt 0.001) {
    try { $shape.LockAspectRatio = -1 } catch {}
    try { $shape.Width = $w * $factor } catch {}
    try { if ($h -gt 0.2) { $shape.Height = $h * $factor } } catch {}
  }
  $shape.Left = (960.0 - [double]$shape.Width) / 2.0
  $shape.Top = (540.0 - [double]$shape.Height) / 2.0
}

$categoryGroups = $manifest.items | Group-Object category | Sort-Object Name
if ($OnlyCategory) { $categoryGroups = $categoryGroups | Where-Object { $_.Name -eq $OnlyCategory } }
$pageMap = [ordered]@{}
$errors = @()

try {
  foreach ($group in $categoryGroups) {
    $category = $group.Name
    $categoryDir = Join-Path $RepositoryDir $category
    New-Item -ItemType Directory -Force -Path $categoryDir | Out-Null
    $outputPath = Join-Path $categoryDir 'components.pptx'
    Write-Output ("BUILD {0} COUNT={1}" -f $category, $group.Count)
    $dest = $app.Presentations.Add()
    $dest.PageSetup.SlideWidth = 960
    $dest.PageSetup.SlideHeight = 540
    $currentSource = $null
    $sourcePres = $null
    $catMap = @()
    $page = 0
    try {
      foreach ($item in ($group.Group | Sort-Object source_deck, source_slide, source_shape_id)) {
        if ($currentSource -ne [string]$item.source_deck) {
          if ($null -ne $sourcePres) { $sourcePres.Close(); $sourcePres = $null }
          $currentSource = [string]$item.source_deck
          $sourcePres = $app.Presentations.Open($currentSource, $true, $false, $true)
        }
        $sourceSlide = $sourcePres.Slides.Item([int]$item.source_slide)
        $sourceShape = Find-ShapeById $sourceSlide ([int]$item.source_shape_id)
        if ($null -eq $sourceShape) {
          $errors += [ordered]@{ id = $item.id; error = 'source shape not found'; source = $currentSource }
          continue
        }
        $slide = $dest.Slides.Add($dest.Slides.Count + 1, 12)
        $slide.FollowMasterBackground = 0
        $slide.Background.Fill.Solid()
        $slide.Background.Fill.ForeColor.RGB = 16119285 # #F5F7F9 in Office BGR
        $range = $null
        $pasteError = $null
        for ($attempt = 1; $attempt -le 3; $attempt++) {
          try {
            try { $sourcePres.Windows.Item(1).Activate() } catch {}
            try { $sourceSlide.Select() } catch {}
            try { $sourceShape.Select() } catch {}
            $sourceShape.Copy()
            Start-Sleep -Milliseconds (50 * $attempt)
            try { $dest.Windows.Item(1).Activate() } catch {}
            $range = $slide.Shapes.Paste()
            if ($null -ne $range) { break }
          } catch {
            $pasteError = $_.Exception.Message
            Start-Sleep -Milliseconds (100 * $attempt)
          }
        }
        if ($null -eq $range) {
          $errors += [ordered]@{ id = $item.id; category = $category; error = $pasteError; source = $currentSource }
          try { $slide.Delete() } catch {}
          continue
        }
        $copy = $range.Item(1)
        Clear-ShapeText $copy
        try { $copy.Name = [string]$item.id } catch {}
        try {
          $copy.AlternativeText = (@{
            id = [string]$item.id
            category = $category
            source_deck = [string]$item.source_deck_name
            source_slide = [int]$item.source_slide
            source_shape_id = [int]$item.source_shape_id
          } | ConvertTo-Json -Compress)
        } catch {}
        Fit-Shape $copy $category
        try { $slide.Tags.Add('asset_id', [string]$item.id) } catch {}
        try { $slide.Tags.Add('category', $category) } catch {}
        $page++
        $catMap += [ordered]@{ id = [string]$item.id; slide = $page; source_deck = [string]$item.source_deck_name; source_slide = [int]$item.source_slide; source_shape_id = [int]$item.source_shape_id }
      }
      if ($dest.Slides.Count -gt 0) {
        $dest.SaveAs($outputPath, 24)
      }
      $pageMap[$category] = $catMap
    } catch {
      $errors += [ordered]@{ category = $category; error = $_.Exception.Message }
    } finally {
      if ($null -ne $sourcePres) { $sourcePres.Close() }
      $dest.Close()
    }
  }
} finally {
  try { $app.Quit() } catch {}
}

$result = [ordered]@{
  generated_at = (Get-Date).ToString('o')
  pages = $pageMap
  errors = $errors
}
[System.IO.File]::WriteAllText((Join-Path $RepositoryDir 'component-page-map.json'), ($result | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
Write-Output ("PACKS={0} ERRORS={1}" -f $categoryGroups.Count, $errors.Count)
