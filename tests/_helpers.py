"""Shared test helpers for loading the skill's scripts as plain modules.

The repo ships PyInstaller build sources in `dist/src/*.py`. Pytest/unittest
cannot import them by name because there's no package wrapper, so we load
them via importlib.util. This keeps the skill itself dependency-free (no
pyproject.toml, no `pip install -e .`) while still letting us unit-test the
internals.

`SCRIPTS` points at `dist/src/` (PyInstaller source directory). These files
are NOT synced into operator-facing skill packages; they are build artefacts
that produce the `keyops` binary.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE = REPO_ROOT / "core"
SCRIPTS = REPO_ROOT / "dist" / "src"


def _load(module_name: str, file_path: Path) -> ModuleType:
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to build spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_enclave_keyops() -> ModuleType:
    return _load("enclave_keyops", SCRIPTS / "enclave_keyops.py")


def load_role_init() -> ModuleType:
    return _load("role_init", SCRIPTS / "role_init.py")


def load_fetch_qos_client() -> ModuleType:
    # `role_init.py` does `from fetch_qos_client import ...` lazily, so we
    # also need the directory on sys.path for that path to resolve in the
    # role_init integration test.
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return _load("fetch_qos_client", SCRIPTS / "fetch_qos_client.py")


def load_fetch_keyops() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return _load("fetch_keyops", SCRIPTS / "fetch_keyops.py")
