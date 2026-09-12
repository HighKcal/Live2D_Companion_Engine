param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not $Python) {
    $Python = 'python'
}
& $Python -c "import sys; assert sys.version_info[:2] == (3,12), 'Python 3.12 x64 required'; assert sys.maxsize > 2**32, '64-bit Python required'"
if ($LASTEXITCODE -ne 0) { throw 'Python version check failed. Use -Python with a 64-bit Python 3.12 executable path.' }
if (-not (Test-Path .venv/Scripts/python.exe)) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& ./.venv/Scripts/python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
