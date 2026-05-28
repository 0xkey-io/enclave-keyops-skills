#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""keyops — unified CLI entry point for the 0xkey Enclave KeyOps skill.

When packaged as a self-contained binary (PyInstaller), this script is the
single entry point that contains the full Python interpreter — callers need
no Python runtime on their machine.

When running from source, each sub-script can still be invoked directly:
    python3 scripts/enclave_keyops.py  <args…>
    python3 scripts/role_init.py       <args…>
    python3 scripts/fetch_qos_client.py <args…>
    python3 scripts/fetch_keyops.py    <args…>

Command routing:
    keyops --version                   → prints version and exits
    keyops init  <args…>               → role_init.main()
    keyops fetch-qos-client  <args…>   → fetch_qos_client.main()
    keyops fetch-keyops  <args…>       → fetch_keyops.main()
    keyops <anything-else>             → enclave_keyops.main()
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Version discovery
# ---------------------------------------------------------------------------

def _read_version() -> str:
    """Return the package version string.

    When frozen by PyInstaller, VERSION is bundled as a data file under
    sys._MEIPASS. When running from source (core/scripts/keyops_main.py),
    VERSION lives two directories up (repo root).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "VERSION"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    # Walk up from the script's directory to find VERSION. This handles:
    #   core/scripts/ layout    → parents[2] = repo root
    #   skills/xxx/scripts/     → parents[1] = skill root (synced by sync-skills.py)
    # We check parents[1] first so that a standalone-installed skill finds its
    # own VERSION before potentially walking into an unrelated parent directory.
    script = Path(__file__).resolve()
    for depth in range(1, 4):
        candidate = script.parents[depth] / "VERSION"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return "unknown"


VERSION = _read_version()

# ---------------------------------------------------------------------------
# Sub-command dispatch table
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, str] = {
    "init": "role_init",
    "fetch-qos-client": "fetch_qos_client",
    "fetch-keyops": "fetch_keyops",
}


def _ensure_scripts_on_path() -> None:
    """Add the scripts directory to sys.path so sibling modules are importable.

    No-op when running as a PyInstaller binary (bundler already wires the
    path). Needed only when running keyops_main.py directly from source.
    """
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def main() -> int:
    args = sys.argv[1:]

    if args and args[0] in ("--version", "-V"):
        print(f"keyops {VERSION}")
        return 0

    if args and args[0] in _DISPATCH:
        subcommand = args[0]
        module_name = _DISPATCH[subcommand]
    else:
        subcommand = None
        module_name = "enclave_keyops"

    if subcommand is not None:
        sys.argv = [f"keyops-{subcommand}"] + args[1:]

    _ensure_scripts_on_path()
    mod = importlib.import_module(module_name)
    return mod.main() or 0  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main())
