# Self-contained demo: sample SVT + tiny fake PSdZData, no full dataset required.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Out = Join-Path $Root "demo\out"

Set-Location $Root
if (Test-Path $Out) {
    Remove-Item -LiteralPath $Out -Recurse -Force
}

Write-Host "Searching demo SVT against demo/psdzdata ..."
py -3 app.py --svt "demo\sample-svt.xml" --psdz "demo" --out $Out --layout psdzdata

Write-Host ""
Write-Host "Exported to $Out"
Get-ChildItem -LiteralPath $Out -Recurse -File | ForEach-Object {
    Write-Host ("  " + $_.FullName.Substring($Out.Length + 1))
}
