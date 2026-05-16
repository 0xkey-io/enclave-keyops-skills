"""Shared test helpers for loading the skill's scripts as plain modules.

The repo ships two `core/scripts/*.py` files that are normally invoked as
`python3 path/to/script.py`. Pytest/unittest cannot import them by name
because there's no package wrapper, so we load them via importlib.util.
This keeps the skill itself dependency-free (no pyproject.toml, no
`pip install -e .`) while still letting us unit-test the internals.

`SCRIPTS` points at `core/scripts/` (the single editable source). The
per-role `skills/<role>/scripts/` copies produced by `tools/sync-skills.py`
are byte-identical to this source; tests verify that invariant separately
in `tests/test_skill_layout.py` instead of importing the copies.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE = REPO_ROOT / "core"
SCRIPTS = CORE / "scripts"


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
