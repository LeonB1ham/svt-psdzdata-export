# Self-contained demo: sample SVT/TAL + tiny fake PSdZData, no full dataset required.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Out = Join-Path $Root "demo\out"

Set-Location $Root
if (Test-Path $Out) {
    Remove-Item -LiteralPath $Out -Recurse -Force
}

Write-Host "1) Single SVT"
py -3 app.py --svt "demo\sample-svt.xml" --psdz "demo" --out "$Out\single"

Write-Host "2) SVT-Ist -> SVT-Soll (only what changes) + missing list for a forum post"
py -3 app.py --svt "demo\sample-svt-ist.xml" --soll "demo\sample-svt.xml" --psdz "demo" --out "$Out\ist-soll" --missing-report "$Out\missing.txt"

Write-Host "3) TAL"
py -3 app.py --tal "demo\sample-tal.xml" --psdz "demo" --out "$Out\tal" --zip

Write-Host ""
Write-Host "Exported to $Out"
Get-ChildItem -LiteralPath $Out -Recurse -File | ForEach-Object {
    Write-Host ("  " + $_.FullName.Substring($Out.Length + 1))
}
