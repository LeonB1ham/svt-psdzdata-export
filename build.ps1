# Сборка SVT → PSdZData Export в .exe (Windows)
# Результат: dist\SVT-PSdZ-Export.exe

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Set-Location $ProjectRoot

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment..."
    py -3 -m venv .venv
}

Write-Host "Installing PyInstaller..."
& $Python -m pip install --upgrade pip pyinstaller -q

Write-Host "Building exe..."
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "SVT-PSdZ-Export" `
    --hidden-import engine `
    app.py

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Done: $ProjectRoot\dist\SVT-PSdZ-Export.exe" -ForegroundColor Green
} else {
    Write-Error "Build failed"
}
