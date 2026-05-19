"""Integration tests for `role_init.py`'s qos_client auto-fetch path.

The first-init UX is: pass nothing, get a verified `qos_client` binary on
disk. These tests pin that contract so future refactors cannot regress
it back into a "remember to set --qos-client-sha256" workflow.

Coverage:

* Default invocation (no `--qos-client-release-*` flag) resolves the
  `latest` sentinel via the GitHub REST API and installs the binary at
  the role-correct path.
* `--qos-client-release-tag <explicit>` skips the latest lookup and pins
  to the given tag.
* `--no-qos-client-fetch` records release metadata but never touches the
  network; the resulting workspace surfaces a follow-up todo with the
  exact `fetch_qos_client.py` command to run later.
* Builder workspaces still install to `out/qos_client.<platform>` and
  point `qos_client_path` at it; other roles use `shared/qos_client`.
* Fetch failure (release exists in metadata but binary 404s) keeps init
  non-blocking: workspace is fully scaffolded, todo points at the manual
  fallback, and no bogus binary is left at the install target.
* `check_qos_client` always emits a copy-pasteable hint when the binary
  is missing — defaulting to "fetch the latest from 0xkey-io/qos" even
  for legacy workspaces that never recorded a `qos_client_release` block.
"""
from __future__ import annotations

import hashlib
import io
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
    REPO,
    TAG,
    _fake_assets,
    _patch_asset_urls,
    fake_release,
    patch_github_api_base,
)


fc = load_fetch_qos_client()
ri = load_role_init()


def _fake_release_stack(stack: ExitStack, host_plat: str, *, tag: str = TAG):
    """Bring up a fake release server, point `asset_urls` and the GitHub API
    base at it, and return the base URL.

    Used by every test that needs role_init to think it talked to GitHub.
    """
    base_url = stack.enter_context(fake_release(_fake_assets(plat=host_plat, tag=tag)))
    stack.enter_context(
        mock.patch.object(
            fc, "asset_urls",
            side_effect=lambda repo, t, plat: _patch_asset_urls(base_url, repo, t, plat),
        )
    )
    stack.enter_context(patch_github_api_base(base_url))
    return base_url


class RoleInitDefaultFetchTests(unittest.TestCase):
    """First-init UX: no flags, get a verified binary."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run_role_init(self, *args: str) -> None:
        argv = ["role_init.py", *args]
        with mock.patch.object(sys, "argv", argv):
            ri.main()

    def test_default_no_flags_resolves_latest_and_installs(self) -> None:
        root = self.tmp / "manifest1"
        host_plat = fc.detect_platform()
        with ExitStack() as stack:
            _fake_release_stack(stack, host_plat)
            # Crucially: NO --qos-client-release-tag, NO --qos-client-sha256.
            # The first-init contract is "type nothing, get a binary".
            self._run_role_init(
                "--role", "manifest-set-member",
                "--root", str(root),
                "--alias", "manifester1",
                "--i-know-unsafe-repo-path",
            )

        cfg = json.loads((root / "config.json").read_text())
        installed = root / "shared" / "qos_client"
        self.assertTrue(installed.exists(), "default init must install the binary")
        self.assertEqual(installed.read_bytes(), BIN_BODY)
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)

        expected_sha = hashlib.sha256(BIN_BODY).hexdigest()
        self.assertEqual(cfg["qos_client_sha256_expected"], expected_sha)

        # Release metadata captured:
        # - `tag` is None (operator typed nothing)
        # - `resolved_tag` is the concrete tag the API returned
        rel = cfg["qos_client_release"]
        self.assertIsNone(rel["tag"], "tag records what the operator typed")
        self.assertEqual(rel["resolved_tag"], TAG)
        self.assertEqual(rel["repo"], "0xkey-io/qos")
        self.assertEqual(rel["platform"], host_plat)

    def test_explicit_tag_skips_latest_lookup(self) -> None:
        root = self.tmp / "manifest1"
        host_plat = fc.detect_platform()
        explicit_tag = "0xkey-qos_client-v0.1.0-rc99"
        with ExitStack() as stack:
            # The fake server only serves `explicit_tag` — if role_init were
            # accidentally hitting /releases/latest first, this test would
            # 404 because the API endpoint we configured returns TAG, not
            # explicit_tag. But because resolve_release_tag short-circuits
            # for explicit tags, the API is never called.
            _fake_release_stack(stack, host_plat, tag=explicit_tag)
            self._run_role_init(
                "--role", "manifest-set-member",
                "--root", str(root),
                "--alias", "manifester1",
                "--i-know-unsafe-repo-path",
                "--qos-client-release-tag", explicit_tag,
            )

        cfg = json.loads((root / "config.json").read_text())
        rel = cfg["qos_client_release"]
        self.assertEqual(rel["tag"], explicit_tag)
        self.assertEqual(rel["resolved_tag"], explicit_tag)


class RoleInitNoFetchTests(unittest.TestCase):
    """Offline init: no network, just metadata + actionable todo."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run_role_init_capture(self, *args: str) -> str:
        from io import StringIO
        from contextlib import redirect_stdout
        argv = ["role_init.py", *args]
        buf = StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
            ri.main()
        return buf.getvalue()

    def test_no_fetch_records_release_metadata_and_emits_todo(self) -> None:
        root = self.tmp / "manifest1"
        out = self._run_role_init_capture(
            "--role", "manifest-set-member",
            "--root", str(root),
            "--alias", "manifester1",
            "--i-know-unsafe-repo-path",
            "--no-qos-client-fetch",
        )
        cfg = json.loads((root / "config.json").read_text())

        # No binary on disk.
        self.assertFalse((root / "shared" / "qos_client").exists())
        self.assertIsNone(cfg["qos_client_sha256_expected"])

        # Release metadata still captured (just `tag=None, resolved_tag=None`,
        # plus `platform` so the doctor hint can pre-fill `--platform`).
        rel = cfg["qos_client_release"]
        self.assertIsNotNone(rel)
        self.assertEqual(rel["repo"], "0xkey-io/qos")
        self.assertEqual(rel["platform"], fc.detect_platform())

        # Todo line points at the exact follow-up command.
        self.assertIn("--no-qos-client-fetch", out)
        self.assertIn("fetch_qos_client.py", out)
        self.assertIn("--release-tag latest", out)

    def test_no_fetch_with_explicit_tag_records_tag(self) -> None:
        root = self.tmp / "manifest1"
        explicit_tag = "0xkey-qos_client-v0.1.0"
        out = self._run_role_init_capture(
            "--role", "manifest-set-member",
            "--root", str(root),
            "--alias", "manifester1",
            "--i-know-unsafe-repo-path",
            "--no-qos-client-fetch",
            "--qos-client-release-tag", explicit_tag,
        )
        cfg = json.loads((root / "config.json").read_text())
        rel = cfg["qos_client_release"]
        self.assertEqual(rel["tag"], explicit_tag)
        # No network call → resolved_tag stays unset.
        self.assertIsNone(rel.get("resolved_tag"))
        # Todo command pre-fills the explicit tag the operator pinned.
        self.assertIn(f"--release-tag {explicit_tag}", out)


class RoleInitInstallPathTests(unittest.TestCase):
    """Builder ↔ consumer roles install the binary at different paths."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run_role_init(self, *args: str) -> None:
        argv = ["role_init.py", *args]
        with mock.patch.object(sys, "argv", argv):
            ri.main()

    def test_consumer_role_uses_shared_path(self) -> None:
        root = self.tmp / "manifest1"
        host_plat = fc.detect_platform()
        with ExitStack() as stack:
            _fake_release_stack(stack, host_plat)
            self._run_role_init(
                "--role", "manifest-set-member",
                "--root", str(root),
                "--alias", "manifester1",
                "--i-know-unsafe-repo-path",
            )
        cfg = json.loads((root / "config.json").read_text())
        self.assertEqual(cfg["qos_client_path"], "shared/qos_client")
        self.assertTrue((root / "shared" / "qos_client").exists())

    def test_builder_role_uses_per_platform_path(self) -> None:
        root = self.tmp / "builder1"
        host_plat = fc.detect_platform()
        with ExitStack() as stack:
            _fake_release_stack(stack, host_plat)
            self._run_role_init(
                "--role", "builder",
                "--root", str(root),
                "--i-know-unsafe-repo-path",
            )
        cfg = json.loads((root / "config.json").read_text())
        self.assertEqual(cfg["qos_client_path"], f"out/qos_client.{host_plat}")
        self.assertTrue((root / "out" / f"qos_client.{host_plat}").exists())


class RoleInitFetchFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run_role_init_capture(self, *args: str) -> str:
        from io import StringIO
        from contextlib import redirect_stdout
        argv = ["role_init.py", *args]
        buf = StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
            ri.main()
        return buf.getvalue()

    def test_fetch_failure_keeps_init_complete_with_todo(self) -> None:
        # The API endpoint resolves `latest` → TAG, but the asset itself
        # 404s. Init must still complete (workspace scaffolded, config
        # written) and surface a fetch-retry todo. No bogus binary on disk.
        root = self.tmp / "manifest1"
        host_plat = fc.detect_platform()
        # Asset map without the binary (only the API endpoint), so:
        # - resolve_release_tag succeeds (returns TAG)
        # - fetch_binary 404s on /releases/download/<TAG>/qos_client.<plat>
        api_only_assets: dict[str, bytes] = {
            f"/repos/{REPO}/releases/latest": json.dumps(
                {"tag_name": TAG, "prerelease": False}
            ).encode(),
        }
        with ExitStack() as stack:
            base_url = stack.enter_context(fake_release(api_only_assets))
            stack.enter_context(
                mock.patch.object(
                    fc, "asset_urls",
                    side_effect=lambda repo, t, plat: _patch_asset_urls(base_url, repo, t, plat),
                )
            )
            stack.enter_context(patch_github_api_base(base_url))
            out = self._run_role_init_capture(
                "--role", "manifest-set-member",
                "--root", str(root),
                "--alias", "manifester1",
                "--i-know-unsafe-repo-path",
            )

        cfg = json.loads((root / "config.json").read_text())
        # Release metadata still recorded so doctor can re-print fetch hint.
        self.assertEqual(cfg["qos_client_release"]["resolved_tag"], TAG)
        # SHA stays null because no binary was successfully installed.
        self.assertIsNone(cfg["qos_client_sha256_expected"])
        # No bogus binary left on disk.
        self.assertFalse((root / "shared" / "qos_client").exists())
        # Todo points the operator at the retry command.
        self.assertIn("auto-fetch failed", out)
        self.assertIn("fetch_qos_client.py", out)
        self.assertIn(f"--release-tag {TAG}", out)


class CheckQosClientHintTests(unittest.TestCase):
    """`check_qos_client` should always print a copy-pasteable fetch command
    when the binary is missing — including for legacy workspaces that have
    no `qos_client_release` block (we fall back to "latest from
    0xkey-io/qos", which is also the role_init.py default).
    """

    def setUp(self) -> None:
        from ._helpers import load_enclave_keyops
        self.ek = load_enclave_keyops()

    def _run(self, cfg_dict: dict) -> str:
        cfg = self.ek.Config(cfg_dict, workdir=Path("/tmp/wd"))
        captured = io.StringIO()
        with mock.patch.object(sys, "stderr", captured):
            with self.assertRaises(SystemExit):
                self.ek.check_qos_client(cfg)
        return captured.getvalue()

    def _services(self) -> list[dict[str, str]]:
        return [
            {"name": n} for n in
            ("signer", "policy-engine", "notarizer", "tls-fetcher", "transaction-parser")
        ]

    def test_release_metadata_drives_concrete_tag(self) -> None:
        text = self._run(
            {
                "qos_client_path": "/does/not/exist/qos_client",
                "qos_client_sha256_expected": None,
                "paths": {},
                "qos_client_release": {
                    "tag": "0xkey-qos_client-v0.1.0",
                    "resolved_tag": "0xkey-qos_client-v0.1.0",
                    "repo": "0xkey-io/qos",
                    "platform": "darwin-arm64",
                },
                "services": self._services(),
            }
        )
        self.assertIn("missing qos_client", text)
        self.assertIn("fetch_qos_client.py", text)
        self.assertIn("--release-tag 0xkey-qos_client-v0.1.0", text)
        self.assertIn("--platform darwin-arm64", text)
        self.assertIn("doctor stays read-only", text)

    def test_legacy_workspace_falls_back_to_latest_default(self) -> None:
        # Older workspaces (or ones initialized with --no-qos-client-fetch
        # before the operator typed any tag) have no release metadata.
        # The hint must still tell them what to run, defaulting to
        # `--release-tag latest` against the upstream repo.
        text = self._run(
            {
                "qos_client_path": "/does/not/exist/qos_client",
                "qos_client_sha256_expected": None,
                "paths": {},
                "qos_client_release": None,
                "services": self._services(),
            }
        )
        self.assertIn("missing qos_client", text)
        self.assertIn("fetch_qos_client.py", text)
        self.assertIn("--release-tag latest", text)


if __name__ == "__main__":
    unittest.main()
