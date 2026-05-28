# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""PyInstaller spec for the keyops self-contained binary.

The resulting binary bundles a full Python 3.12 interpreter together with
enclave_keyops.py, role_init.py, fetch_qos_client.py, fetch_keyops.py,
and keyops_main.py. Callers need no Python runtime on their machine.

Build (from repo root):
    pip install pyinstaller
    pyinstaller dist/keyops.spec

Or via the helper:
    bash dist/build.sh

Output (before platform-renaming by build.sh):
    dist/keyops        (macOS/Linux single-file executable)
"""
from pathlib import Path

REPO_ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is injected by PyInstaller)
SCRIPTS = REPO_ROOT / "dist" / "src"

a = Analysis(  # noqa: F821  (Analysis is injected by PyInstaller)
    [str(SCRIPTS / "keyops_main.py")],
    pathex=[str(SCRIPTS)],
    binaries=[],
    datas=[
        # Bundle VERSION so keyops --version works without source tree.
        (str(REPO_ROOT / "VERSION"), "."),
        # Bundle config template so role_init `keyops init` works without the
        # skill source tree on disk. skill_dir() resolves to sys._MEIPASS when
        # frozen, so this file lands at the root of the extraction temp dir.
        (str(REPO_ROOT / "core" / "config.prod.example.json"), "."),
    ],
    # role_init.py uses a lazy `from fetch_qos_client import ...` inside a
    # conditional branch, which PyInstaller's static analyser cannot see.
    # Listing all four sibling modules here guarantees they are bundled.
    hiddenimports=[
        "enclave_keyops",
        "role_init",
        "fetch_qos_client",
        "fetch_keyops",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="keyops",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    # onefile=True packs everything into a single self-extracting executable.
    # On first run it expands into a temp directory (~100 ms); subsequent runs
    # reuse the cached extraction.
    onefile=True,
)
