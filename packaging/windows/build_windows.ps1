<#
.SYNOPSIS
    Builds the Windows 10/11 standalone executable and installer for Agent Usage Widget.
#>
$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

Write-Host "==> 1. Running unit and fixture test suite..."
$env:PYTHONPATH = Join-Path $RepoRoot "src"
python -m unittest discover -s tests -v

Write-Host "==> 2. Installing PySide6 and PyInstaller in isolated virtual environment..."
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& .\.venv\Scripts\pip.exe install --upgrade pip
& .\.venv\Scripts\pip.exe install -e ".[qt,dev]"

Write-Host "==> 3. Building standalone Windows executable (dist\agent-usage-widget.exe)..."
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean packaging\windows\agent-usage-widget.spec

if (Get-Command iscc -ErrorAction SilentlyContinue) {
    Write-Host "==> 4. Building Inno Setup Windows Installer..."
    iscc packaging\windows\installer.iss
} else {
    Write-Host "==> Note: Inno Setup (iscc) not found in PATH. Standalone EXE is ready at dist\agent-usage-widget.exe"
}
