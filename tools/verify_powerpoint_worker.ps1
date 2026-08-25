$ErrorActionPreference = 'Stop'
$candidatePaths = @(
  $env:SKILL_MERGE_POWERPOINT_PATH,
  'C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE',
  'C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE'
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$candidatePaths = @($candidatePaths)
if (-not $candidatePaths) { throw 'POWERPNT.EXE was not found. Configure SKILL_MERGE_POWERPOINT_PATH.' }
$version = (Get-Item -LiteralPath ($candidatePaths[0])).VersionInfo
[pscustomobject]@{ status = 'ready'; path = $candidatePaths[0]; fileVersion = $version.FileVersion; productVersion = $version.ProductVersion; interactiveWorkerRequired = $true } | ConvertTo-Json
