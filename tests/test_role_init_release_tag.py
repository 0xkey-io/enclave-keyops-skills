"""Integration tests for `role_init.py --qos-client-release-tag`.

Verifies:

* `--skip-qos-client-fetch` records the release tag in `config.json`
  without performing any download.
* When the fetch runs end-to-end (against a fake local release server),
  the binary is installed at the role-appropriate path and the verified
  SHA256 is persisted in `config.json`.
* The Builder workspace points `qos_client_path` at the per-platform
  binary (`out/qos_client.<plat>`); other roles still use
  `shared/qos_client`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from ._helpers import load_fetch_qos_client, load_role_init
from .test_fetch_qos_client import (
    BIN_BODY,
    PLAT,
    REPO,
    TAG,
    _fake_assets,
    _patch_asset_urls,
    fake_release,
)


fc = load_fetch_qos_client()
ri = load_role_init()


class RoleInitReleaseTagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run_role_init(self, *args: str) -> None:
        argv = ["role_init.py", *args]
        with mock.patch.object(sys, "argv", argv):
            ri.main()

    def test_skip_fetch_records_release_metadata_only(self) -> None:
        root = self.tmp / "manifest1"
        self._run_role_init(
            "--role", "manifest-set-member",
            "--root", str(root),
            "--i-know-unsafe-repo-path",
            "--skip-qos-client-fetch",
            "--qos-client-release-tag", TAG,
        )
        cfg = json.loads((root / "config.json").read_text())
        self.assertEqual(
            cfg["qos_client_release"],
            {"tag": TAG, "repo": "0xkey-io/qos", "platform": fc.detect_platform()},
        )
        # No binary was downloaded.
        self.assertFalse((root / "shared" / "qos_client").exists())
        # SHA stays null until the operator follows the manual fallback.
        self.assertIsNone(cfg["qos_client_sha256_expected"])

    def test_fetch_installs_to_shared_for_member_role(self) -> None:
        root = self.tmp / "manifest1"
        host_plat = fc.detect_platform()
        with ExitStack() as stack:
            base_url = stack.enter_context(fake_release(_fake_assets(plat=host_plat)))
            stack.enter_context(
                mock.patch.object(
                    fc, "asset_urls",
                    side_effect=lambda repo, tag, plat: _patch_asset_urls(base_url, repo, tag, plat),
                )
            )
            self._run_role_init(
                "--role", "manifest-set-member",
                "--root", str(root),
                "--i-know-unsafe-repo-path",
                "--qos-client-release-tag", TAG,
                "--qos-client-release-repo", REPO,
            )

        cfg = json.loads((root / "config.json").read_text())
        # Binary lands at the role-appropriate path.
        self.assertEqual(cfg["qos_client_path"], "shared/qos_client")
        installed = root / "shared" / "qos_client"
        self.assertTrue(installed.exists())
        self.assertEqual(installed.read_bytes(), BIN_BODY)
        # Mode 0755 — operator runs it directly.
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
        # config.json carries the verified SHA256.
        import hashlib
        expected = hashlib.sha256(BIN_BODY).hexdigest()
        self.assertEqual(cfg["qos_client_sha256_expected"], expected)
        # Release metadata captured for doctor / audit.
        self.assertEqual(cfg["qos_client_release"]["tag"], TAG)
        self.assertEqual(cfg["qos_client_release"]["repo"], REPO)
        self.assertEqual(cfg["qos_client_release"]["platform"], fc.detect_platform())
        # Sidecar mirrored next to the binary.
        sidecar = installed.with_name("qos_client.sha256")
        self.assertTrue(sidecar.exists())
        self.assertIn(expected, sidecar.read_text())

    def test_fetch_installs_per_platform_path_for_builder(self) -> None:
        root = self.tmp / "builder1"
        host_plat = fc.detect_platform()
        with ExitStack() as stack:
            base_url = stack.enter_context(fake_release(_fake_assets(plat=host_plat)))
            stack.enter_context(
                mock.patch.object(
                    fc, "asset_urls",
                    side_effect=lambda repo, tag, plat: _patch_asset_urls(base_url, repo, tag, plat),
                )
            )
            self._run_role_init(
                "--role", "builder",
                "--root", str(root),
                "--i-know-unsafe-repo-path",
                "--qos-client-release-tag", TAG,
                "--qos-client-release-repo", REPO,
            )

        cfg = json.loads((root / "config.json").read_text())
        plat = fc.detect_platform()
        self.assertEqual(cfg["qos_client_path"], f"out/qos_client.{plat}")
        installed = root / "out" / f"qos_client.{plat}"
        self.assertTrue(installed.exists())
        self.assertEqual(installed.read_bytes(), BIN_BODY)

    def test_fetch_failure_keeps_init_complete_with_todo(self) -> None:
        # Empty asset map → 404 on every URL. Init should still succeed
        # (writes config.json) and surface the manual fallback in todos.
        root = self.tmp / "manifest1"
        with ExitStack() as stack:
            base_url = stack.enter_context(fake_release({}))
            stack.enter_context(
                mock.patch.object(
                    fc, "asset_urls",
                    side_effect=lambda repo, tag, plat: _patch_asset_urls(base_url, repo, tag, plat),
                )
            )
            # role_init writes the fallback to stderr; we just want to make
            # sure the call doesn't crash.
            self._run_role_init(
                "--role", "manifest-set-member",
                "--root", str(root),
                "--i-know-unsafe-repo-path",
                "--qos-client-release-tag", TAG,
                "--qos-client-release-repo", REPO,
            )

        cfg = json.loads((root / "config.json").read_text())
        # Release metadata still recorded so doctor can re-print fetch hint.
        self.assertEqual(cfg["qos_client_release"]["tag"], TAG)
        # SHA stays null because no binary was successfully installed.
        self.assertIsNone(cfg["qos_client_sha256_expected"])
        # No bogus binary left on disk.
        self.assertFalse((root / "shared" / "qos_client").exists())


class CheckQosClientHintTests(unittest.TestCase):
    """`check_qos_client` should print the release-channel hint when binary
    is missing AND `qos_client_release` is recorded in config — but never
    auto-run anything.
    """

    def setUp(self) -> None:
        from ._helpers import load_enclave_keyops
        self.ek = load_enclave_keyops()

    def test_missing_binary_prints_release_hint(self) -> None:
        cfg = self.ek.Config(
            {
                "qos_client_path": "/does/not/exist/qos_client",
                "qos_client_sha256_expected": None,
                "paths": {},
                "qos_client_release": {
                    "tag": "0xkey-qos_client-v0.1.0",
                    "repo": "0xkey-io/qos",
                    "platform": "darwin-arm64",
                },
                "services": [
                    {"name": n} for n in
                    ("signer", "policy-engine", "notarizer", "tls-fetcher", "transaction-parser")
                ],
            },
            workdir=Path("/tmp/wd"),
        )
        import io
        captured = io.StringIO()
        with mock.patch.object(sys, "stderr", captured):
            with self.assertRaises(SystemExit) as cm:
                self.ek.check_qos_client(cfg)
        text = captured.getvalue()
        # Doctor remains read-only — emits a copy-paste fetch line, no shell exec.
        self.assertIn("missing qos_client", text)
        self.assertIn("fetch_qos_client.py", text)
        self.assertIn("--release-tag 0xkey-qos_client-v0.1.0", text)
        self.assertIn("doctor stays read-only", text)
        self.assertEqual(cm.exception.code, 2)

    def test_missing_binary_without_release_metadata_only_prints_basic_error(self) -> None:
        cfg = self.ek.Config(
            {
                "qos_client_path": "/does/not/exist/qos_client",
                "qos_client_sha256_expected": None,
                "paths": {},
                "qos_client_release": None,
                "services": [
                    {"name": n} for n in
                    ("signer", "policy-engine", "notarizer", "tls-fetcher", "transaction-parser")
                ],
            },
            workdir=Path("/tmp/wd"),
        )
        import io
        captured = io.StringIO()
        with mock.patch.object(sys, "stderr", captured):
            with self.assertRaises(SystemExit):
                self.ek.check_qos_client(cfg)
        text = captured.getvalue()
        self.assertIn("missing qos_client", text)
        self.assertNotIn("fetch_qos_client.py", text)


if __name__ == "__main__":
    unittest.main()
