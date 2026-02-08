# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

exe_name = 'mem_proccess'
script = 'mem_proccess.py'

# Icon used for the built executable (place `app_icon.ico` in the project root)
icon_file = 'app_icon.ico'

# Collect common dynamic imports to help PyInstaller find them
hidden_imports = [
    'dearpygui.dearpygui',
    'pystray',
    'PIL',
    'PIL.Image',
    'psutil',
]
hidden_imports += collect_submodules('PIL')


a = Analysis(
    [script],
    pathex=[],
    binaries=[],
    datas=[('file_version.txt', '.'), (icon_file, '.')],
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_file if os.path.exists(icon_file) else None,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
