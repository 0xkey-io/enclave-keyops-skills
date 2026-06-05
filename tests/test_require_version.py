"""Tests for `keyops require-version` binary version gate.

The gate verifies that the running keyops binary version exactly matches the
skill version so stale binaries are caught before they can silently re-
introduce already-fixed bugs during a ceremony.

Behaviour contract:
  - exact match  → exit 0, stdout contains match message
  - mismatch     → exit 2, stderr contains remediation (fetch-keyops + curl)
  - VERSION=="unknown" → exit 2, stderr says cannot verify
  - missing arg  → exit 2, stderr shows usage
  - extra args   → exit 2, stderr shows usage
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "dist" / "src"


def _load_keyops_main_fresh() -> object:
    """Load keyops_main without caching — needed because we override VERSION."""
    path = SCRIPTS / "keyops_main.py"
    spec = importlib.util.spec_from_file_location("_keyops_main_fresh", str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    env_backup = os.environ.copy()
    os.environ["KEYOPS_SOURCE_MODE"] = "1"
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        os.environ.clear()
        os.environ.update(env_backup)
    return mod


# Load once; each test overrides mod.VERSION before calling.
_MOD = _load_keyops_main_fresh()


def _call(args: list[str], version: str = "1.2.3") -> tuple[int, str, str]:
    """Return (exit_code, stdout, stderr) for _cmd_require_version(args)."""
    _MOD.VERSION = version  # type: ignore[attr-defined]
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        rc = _MOD._cmd_require_version(args)  # type: ignore[attr-defined]
    return rc, buf_out.getvalue(), buf_err.getvalue()


class RequireVersionTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_exact_match_exits_0(self) -> None:
        rc, out, err = _call(["1.2.3"], version="1.2.3")
        self.assertEqual(rc, 0, f"expected exit 0, stderr={err!r}")
        self.assertIn("1.2.3", out)
        self.assertIn("matches", out)

    def test_exact_match_no_stderr(self) -> None:
        _, _, err = _call(["0.5.8"], version="0.5.8")
        self.assertEqual(err, "", f"unexpected stderr: {err!r}")

    # ------------------------------------------------------------------
    # Version mismatch
    # ------------------------------------------------------------------

    def test_mismatch_exits_2(self) -> None:
        rc, _, _ = _call(["9.9.9"], version="1.2.3")
        self.assertEqual(rc, 2)

    def test_mismatch_stderr_names_both_versions(self) -> None:
        _, _, err = _call(["9.9.9"], version="1.2.3")
        self.assertIn("1.2.3", err, "binary version missing from stderr")
        self.assertIn("9.9.9", err, "expected version missing from stderr")

    def test_mismatch_stderr_contains_fetch_keyops(self) -> None:
        _, _, err = _call(["9.9.9"], version="1.2.3")
        self.assertIn("fetch-keyops", err)
        self.assertIn("--release-tag", err)
        self.assertIn("v9.9.9", err, "remediation must pin to expected version")

    def test_mismatch_stderr_contains_curl_pin(self) -> None:
        _, _, err = _call(["9.9.9"], version="1.2.3")
        self.assertIn("releases/download/v9.9.9/", err)

    def test_mismatch_no_stdout(self) -> None:
        _, out, _ = _call(["9.9.9"], version="1.2.3")
        self.assertEqual(out, "", f"unexpected stdout: {out!r}")

    # ------------------------------------------------------------------
    # VERSION == "unknown"
    # ------------------------------------------------------------------

    def test_unknown_version_exits_2(self) -> None:
        rc, _, _ = _call(["1.2.3"], version="unknown")
        self.assertEqual(rc, 2)

    def test_unknown_version_stderr_mentions_cannot_verify(self) -> None:
        _, _, err = _call(["1.2.3"], version="unknown")
        self.assertIn("cannot verify", err.lower())

    def test_unknown_version_stderr_contains_fetch_keyops(self) -> None:
        _, _, err = _call(["1.2.3"], version="unknown")
        self.assertIn("fetch-keyops", err)
        self.assertIn("v1.2.3", err)

    # ------------------------------------------------------------------
    # Bad invocations
    # ------------------------------------------------------------------

    def test_no_args_exits_2(self) -> None:
        rc, _, _ = _call([])
        self.assertEqual(rc, 2)

    def test_no_args_stderr_shows_usage(self) -> None:
        _, _, err = _call([])
        self.assertIn("usage", err.lower())

    def test_extra_args_exits_2(self) -> None:
        rc, _, _ = _call(["1.2.3", "extra"])
        self.assertEqual(rc, 2)

    def test_extra_args_stderr_shows_usage(self) -> None:
        _, _, err = _call(["1.2.3", "extra"])
        self.assertIn("usage", err.lower())


if __name__ == "__main__":
    unittest.main()
