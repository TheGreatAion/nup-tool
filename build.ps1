# Builds the app (dist\nup tool) and its installer (dist\nup-tool-setup-<version>.exe).
# Needs the dev setup from the README, plus Inno Setup 6.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 not found. Install it with: winget install --id JRSoftware.InnoSetup --scope user"
}

.venv\Scripts\pyinstaller --noconfirm --log-level WARN nup_tool.spec
if ($LASTEXITCODE) { exit $LASTEXITCODE }

$version = .venv\Scripts\python -c "from nup_gui import __version__; print(__version__)"
if ($LASTEXITCODE) { exit $LASTEXITCODE }

& $iscc "/Qp" "/DAppVersion=$version" installer.iss
exit $LASTEXITCODE
