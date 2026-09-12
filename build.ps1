[CmdletBinding()]
param(
    [switch]$IncludeModels
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Spec = Join-Path $ProjectRoot 'live2d_pet.spec'
$DistRoot = Join-Path $ProjectRoot 'dist\Live2D Companion Engine'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Missing .venv\Scripts\python.exe. Run setup.ps1 first.'
}

Push-Location $ProjectRoot
try {
    & $Python -c 'import PyInstaller' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'PyInstaller is not installed; installing it into .venv...'
        & $Python -m pip install PyInstaller
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
    }

    & $Python -m PyInstaller --noconfirm --clean $Spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    foreach ($Directory in @('profiles', 'assets')) {
        $Source = Join-Path $ProjectRoot $Directory
        if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
            throw "Required runtime directory is missing: $Source"
        }
        Copy-Item -LiteralPath $Source -Destination $DistRoot -Recurse -Force
    }

    $DistModels = Join-Path $DistRoot 'models'
    if ($IncludeModels) {
        $SourceModels = Join-Path $ProjectRoot 'models'
        if (-not (Test-Path -LiteralPath $SourceModels -PathType Container)) {
            throw "-IncludeModels was requested, but the local models directory is missing: $SourceModels"
        }
        Copy-Item -LiteralPath $SourceModels -Destination $DistRoot -Recurse -Force
    }
    else {
        New-Item -ItemType Directory -Path $DistModels -Force | Out-Null
    }

    $DistLocal = Join-Path $DistRoot 'local'
    New-Item -ItemType Directory -Path $DistLocal -Force | Out-Null

    $RequiredFiles = @(
        'Live2D Companion Engine.exe',
        '_internal\live2d\v3\_v3cpp.pyd',
        '_internal\live2d\v3\FrameworkShaders\VertShaderSrc.vert',
        '_internal\live2d\v3\FrameworkShaders\FragShaderSrc.frag',
        '_internal\PySide6\plugins\platforms\qwindows.dll',
        '_internal\PySide6\Qt6OpenGL.dll',
        '_internal\PySide6\Qt6OpenGLWidgets.dll',
        'profiles\hibana.json',
        'profiles\tsubaki.json',
        'profiles\icegirl.json',
        'assets\food\cookie.png'
    )
    foreach ($RelativePath in $RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $DistRoot $RelativePath) -PathType Leaf)) {
            throw "Build output is missing required file: $RelativePath"
        }
    }

    $SourceQpng = Join-Path $ProjectRoot '.venv\Lib\site-packages\PySide6\plugins\imageformats\qpng.dll'
    if (Test-Path -LiteralPath $SourceQpng -PathType Leaf) {
        $DistQpng = Join-Path $DistRoot '_internal\PySide6\plugins\imageformats\qpng.dll'
        if (-not (Test-Path -LiteralPath $DistQpng -PathType Leaf)) {
            throw 'PySide6 provides qpng.dll, but PyInstaller did not collect it.'
        }
    }
    else {
        Write-Host 'Installed Qt has no separate qpng.dll; PNG support is built into QtGui.'
    }

    if (-not $IncludeModels) {
        $BundledModels = @(Get-ChildItem -LiteralPath $DistModels -Recurse -File)
        if ($BundledModels.Count -ne 0) {
            throw 'Public build unexpectedly contains files in models\.'
        }
    }
    $BundledLocalState = @(Get-ChildItem -LiteralPath $DistLocal -Recurse -File)
    if ($BundledLocalState.Count -ne 0) {
        throw 'Build unexpectedly contains developer files in local\.'
    }

    $BuildKind = if ($IncludeModels) { 'local developer build (models included)' } else { 'engine-only public build (models excluded)' }
    Write-Host "Build complete: $DistRoot"
    Write-Host "Build mode: $BuildKind"
    if (-not $IncludeModels) {
        Write-Warning 'This engine-only build requires the user to add licensed Live2D model files before the app can launch.'
    }
}
finally {
    Pop-Location
}
