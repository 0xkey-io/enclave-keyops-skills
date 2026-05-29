#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""keyops — unified CLI entry point for the 0xkey Enclave KeyOps skill.

When packaged as a self-contained binary (PyInstaller), this script is the
single entry point that contains the full Python interpreter — callers need
no Python runtime on their machine.

Direct Python invocation of this file (or any sibling script) is disabled
for operator environments. Set KEYOPS_SOURCE_MODE=1 to enable source-mode
for maintainer / CI use. See core/references/source-invocation.md.

Command routing:
    keyops --version                   → prints version and exits
    keyops init  <args…>               → role_init.main()
    keyops fetch-qos-client  <args…>   → fetch_qos_client.main()
    keyops fetch-keyops  <args…>       → fetch_keyops.main()
    keyops <anything-else>             → enclave_keyops.main()
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# SSL CA bundle for PyInstaller frozen binary
# ---------------------------------------------------------------------------

def _setup_ssl() -> None:
    """Point urllib/ssl at the bundled certifi CA bundle when frozen.

    PyInstaller's --onefile binary extracts into a temp directory, cutting off
    access to the system CA store. We bundle certifi's cacert.pem via the spec
    file and set SSL_CERT_FILE so stdlib ssl/urllib use it transparently.
    """
    if not getattr(sys, "frozen", False):
        return
    if "SSL_CERT_FILE" in os.environ:
        return
    meipass = getattr(sys, "_MEIPASS", "")
    cacert = Path(meipass) / "certifi" / "cacert.pem"
    if cacert.is_file():
        os.environ["SSL_CERT_FILE"] = str(cacert)

_setup_ssl()


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
    #   dist/src/ layout        → parents[2] = repo root
    # We check up to depth 3 to handle edge cases.
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
    import os

    if not getattr(sys, "frozen", False) and "KEYOPS_SOURCE_MODE" not in os.environ:
        print(
            "ERROR: Direct Python invocation is disabled.\n"
            "Use the self-contained 'keyops' binary instead.\n"
            "  Download: https://github.com/0xkey-io/enclave-keyops-skills/releases/latest\n"
            "\n"
            "Maintainers: export KEYOPS_SOURCE_MODE=1 to bypass this check.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    raise SystemExit(main())
