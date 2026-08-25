param(
  [Parameter(Mandatory=$true)][string]$InputPptx,
  [Parameter(Mandatory=$true)][string]$OutputDir,
  [int]$Width = 1600,
  [int]$Height = 900
)

$ErrorActionPreference = 'Stop'
$resolvedInput = (Resolve-Path -LiteralPath $InputPptx).Path
$resolvedOutput = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

$powerpoint = $null
$presentation = $null
try {
  $powerpoint = New-Object -ComObject PowerPoint.Application
  $powerpoint.Visible = -1
  try { $powerpoint.AutomationSecurity = 3 } catch { }
  try { $powerpoint.DisplayAlerts = 1 } catch { }
  $presentation = $powerpoint.Presentations.Open($resolvedInput, $true, $true, $false)
  $presentation.Export($resolvedOutput, 'PNG', $Width, $Height)
  $count = $presentation.Slides.Count
  [pscustomobject]@{ status = 'passed'; input = $resolvedInput; output = $resolvedOutput; slides = $count; renderer = 'Microsoft PowerPoint COM' } | ConvertTo-Json -Compress
}
finally {
  if ($presentation) { try { $presentation.Close() } catch { } }
  if ($powerpoint) { try { $powerpoint.Quit() } catch { } }
  foreach ($object in @($presentation, $powerpoint)) { if ($object) { try { [Runtime.InteropServices.Marshal]::ReleaseComObject($object) | Out-Null } catch { } } }
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
