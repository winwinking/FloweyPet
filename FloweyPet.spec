# -*- mode: python ; coding: utf-8 -*-
# Same layout as build/FloweyPet.spec in the original project, but with
# relative paths so it can build from a copied working tree.
# The dialogue/roast JSON files are listed in datas (target '.', which with
# contents_directory='.' puts them next to the exe), so they no longer need
# to be copied in by hand after building. UPX is off to avoid antivirus
# false positives.

a = Analysis(
    ['main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('dialogue.json', '.'),
        ('dialogues_anticheat.json', '.'),
        ('dialogues_chase.json', '.'),
        ('dialogues_flirt.json', '.'),
        ('dialogues_game.json', '.'),
        ('dialogues_info.json', '.'),
        ('dialogues_spam.json', '.'),
        ('dialogues_system.json', '.'),
        ('stats_roast.json', '.'),
    ],
    hiddenimports=[],
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
    name='FloweyPet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='FloweyPet',
)
