from pathlib import Path
import importlib.util

from PyInstaller.utils.hooks import collect_dynamic_libs


project_root = Path(SPECPATH).resolve()
live2d_origin = Path(importlib.util.find_spec('live2d.v3').origin).resolve().parent
shader_root = live2d_origin / 'FrameworkShaders'

# live2d-py loads these shader sources from its package directory at runtime.
shader_datas = [
    (str(path), 'live2d/v3/FrameworkShaders')
    for pattern in ('*.vert', '*.frag')
    for path in sorted(shader_root.glob(pattern))
]

hidden_imports = [
    'live2d.v3.params',
    'live2d.v3._v3cpp',
    'live2d.v3.lapp_model',
    'OpenGL.platform.win32',
    'OpenGL.arrays.ctypesarrays',
    'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
]

a = Analysis(
    [str(project_root / 'app.py')],
    pathex=[str(project_root)],
    binaries=collect_dynamic_libs('live2d'),
    datas=shader_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Live2D Companion Engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Live2D Companion Engine',
)
