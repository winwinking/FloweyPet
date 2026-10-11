# -*- mode: python ; coding: utf-8 -*-
# Same layout as build/FloweyPet.spec in the original project, but with
# relative paths so it can build from a copied working tree.

a = Analysis(
    ['main.py'],
    pathex=['src'],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Local optional font (words.ttf) must never be shipped: drop it from the bundle.
a.datas = [d for d in a.datas if d[0].replace(chr(92), '/') != 'assets/fonts/words.ttf']

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FloweyPet',
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
    icon=['assets/icon.ico'],
    contents_directory='.',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FloweyPet',
)
