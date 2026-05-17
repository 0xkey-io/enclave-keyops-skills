"""Unit tests for `fetch_qos_client.py`.

These tests stand up a tiny local `http.server` to serve a fake GitHub
release directory (`/<repo>/releases/download/<tag>/qos_client.<plat>` and
`.sha256`) plus the matching REST API endpoints
(`/repos/<repo>/releases/latest`, `/repos/<repo>/releases?per_page=1`). All
network requests stay on `127.0.0.1`, so the suite has no external
dependencies and is safe to run in CI.

Coverage:

* `detect_platform` recognizes the supported uname pairs and refuses unknowns.
* `resolve_release_tag` resolves the `latest` sentinel via the GitHub REST
  API, falls back to the most recent prerelease when no stable release
  exists yet (and emits a stderr WARN), and passes explicit tags through
  unchanged.
* Successful fetch installs the binary at `--out` with mode 0755 and writes
  the matching `.sha256` sidecar.
* SHA mismatch (downloaded `.sha256` disagrees with the binary) quarantines
  the download at `<out>.tainted` and never installs.
* `--expected-sha256` mismatch behaves identically (double-verification gate).
* HTTP 404 → manual fallback path; binary is not written.
* Atomic write: a previous `.partial` file does not leak into a successful
  install.
"""
from __future__ import annotations

import http.server
import io
import json
import socketserver
import sys
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock

from ._helpers import load_fetch_qos_client


fc = load_fetch_qos_client()


class _ReleaseHandler(http.server.BaseHTTPRequestHandler):
    """Serves files from `self.server.assets` (a dict[path -> bytes])."""

    def log_message(self, format: str, *args) -> None:  # type: ignore[override]
        # Silence noisy access logs during tests.
        return

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        body = self.server.assets.get(self.path)  # type: ignore[attr-defined]
        if body is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ReleaseServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, assets: dict[str, bytes]) -> None:
        super().__init__(("127.0.0.1", 0), _ReleaseHandler)
        self.assets = assets


@contextmanager
def fake_release(assets: dict[str, bytes]) -> Iterator[str]:
    """Yields the base URL (`http://127.0.0.1:<port>`) of a server hosting `assets`."""
    server = _ReleaseServer(assets)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _patch_asset_urls(monkey_base: str, repo: str, tag: str, plat: str) -> tuple[str, str]:
    """Compute the URL pair the fake server expects for a given (repo,tag,plat)."""
    base = f"{monkey_base}/{repo}/releases/download/{tag}/qos_client.{plat}"
    return base, f"{base}.sha256"


# ---------------------------------------------------------------------------
# Pure-function tests (no HTTP)
# ---------------------------------------------------------------------------


class DetectPlatformTests(unittest.TestCase):
    def test_linux_amd64(self) -> None:
        with mock.patch.object(fc.platform, "system", return_value="Linux"):
            with mock.patch.object(fc.platform, "machine", return_value="x86_64"):
                self.assertEqual(fc.detect_platform(), "linux-amd64")

    def test_linux_amd64_alias(self) -> None:
        with mock.patch.object(fc.platform, "system", return_value="Linux"):
            with mock.patch.object(fc.platform, "machine", return_value="amd64"):
                self.assertEqual(fc.detect_platform(), "linux-amd64")

    def test_darwin_arm64(self) -> None:
        with mock.patch.object(fc.platform, "system", return_value="Darwin"):
            with mock.patch.object(fc.platform, "machine", return_value="arm64"):
                self.assertEqual(fc.detect_platform(), "darwin-arm64")

    def test_unknown_platform_refuses(self) -> None:
        with mock.patch.object(fc.platform, "system", return_value="Windows"):
            with mock.patch.object(fc.platform, "machine", return_value="x86_64"):
                with self.assertRaises(fc.FetchError) as cm:
                    fc.detect_platform()
                self.assertIn("unsupported platform", str(cm.exception))

    def test_linux_arm64_refuses_until_builder_publishes(self) -> None:
        # linux-arm64 is intentionally NOT in the active runbook map.
        with mock.patch.object(fc.platform, "system", return_value="Linux"):
            with mock.patch.object(fc.platform, "machine", return_value="aarch64"):
                with self.assertRaises(fc.FetchError):
                    fc.detect_platform()


class ReadRemoteShaTests(unittest.TestCase):
    def test_bare_hex(self, tmp_path: Path | None = None) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.sha256"
            p.write_text("a" * 64 + "\n")
            self.assertEqual(fc._read_remote_sha(p), "a" * 64)

    def test_hex_plus_filename(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.sha256"
            p.write_text("b" * 64 + "  qos_client.linux-amd64\n")
            self.assertEqual(fc._read_remote_sha(p), "b" * 64)

    def test_malformed_raises(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.sha256"
            p.write_text("nope-not-a-hash\n")
            with self.assertRaises(fc.FetchError):
                fc._read_remote_sha(p)

    def test_empty_raises(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.sha256"
            p.write_text("")
            with self.assertRaises(fc.FetchError):
                fc._read_remote_sha(p)


# ---------------------------------------------------------------------------
# End-to-end tests against a fake release server
# ---------------------------------------------------------------------------


REPO = "0xkey-io/qos"
TAG = "0xkey-qos_client-v0.1.0"
PLAT = "linux-amd64"
BIN_BODY = b"\x7fELF<imagine-this-is-qos_client>" * 32


def _fake_assets(
    *,
    plat: str = PLAT,
    sha_override: bytes | None = None,
    tag: str = TAG,
) -> dict[str, bytes]:
    """Build the asset URL → bytes map served by the local fake release server.

    Includes both the `releases/download/<tag>/...` binary + sidecar and the
    matching REST API endpoint (`/repos/<repo>/releases/latest`) so a single
    server can satisfy both `resolve_release_tag` and `fetch_binary`.
    """
    import hashlib
    sha = hashlib.sha256(BIN_BODY).hexdigest()
    sha_line = sha_override if sha_override is not None else f"{sha}  qos_client.{plat}\n".encode()
    base = f"/{REPO}/releases/download/{tag}/qos_client.{plat}"
    api_latest = f"/repos/{REPO}/releases/latest"
    api_list = f"/repos/{REPO}/releases?per_page=1"
    return {
        base: BIN_BODY,
        f"{base}.sha256": sha_line,
        api_latest: json.dumps({"tag_name": tag, "prerelease": False}).encode(),
        api_list: json.dumps([{"tag_name": tag, "prerelease": False}]).encode(),
    }


@contextmanager
def patch_github_api_base(base_url: str) -> Iterator[None]:
    """Redirect `fc.GITHUB_API_BASE` at the local fake server for the duration
    of the with-block. Required by every test that exercises
    `resolve_release_tag` since that path uses the REST API URL directly
    (it is not routed through `asset_urls`).
    """
    original = fc.GITHUB_API_BASE
    fc.GITHUB_API_BASE = base_url
    try:
        yield
    finally:
        fc.GITHUB_API_BASE = original


class FetchBinaryE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _patched_asset_urls(self, base_url: str):
        # Redirect production GitHub URLs to our fake server.
        original = fc.asset_urls

        def fake(repo: str, tag: str, plat: str) -> tuple[str, str]:
            return _patch_asset_urls(base_url, repo, tag, plat)

        return mock.patch.object(fc, "asset_urls", side_effect=fake)

    def test_happy_path_installs_with_mode_0755(self) -> None:
        out = self.tmp / "shared" / "qos_client"
        with fake_release(_fake_assets()) as base:
            with self._patched_asset_urls(base):
                digest = fc.fetch_binary(
                    repo=REPO, tag=TAG, plat=PLAT, out=out,
                    expected_sha256=None, token=None, timeout=5.0,
                )
        self.assertTrue(out.exists())
        # Mode bits: rwxr-xr-x = 0o755
        self.assertEqual(out.stat().st_mode & 0o777, 0o755)
        sidecar = Path(str(out) + ".sha256")
        self.assertTrue(sidecar.exists())
        # The sidecar should contain at least the hex token we computed.
        self.assertIn(digest, sidecar.read_text())
        # Body is intact.
        self.assertEqual(out.read_bytes(), BIN_BODY)

    def test_double_verify_with_expected_sha_passes(self) -> None:
        import hashlib
        out = self.tmp / "shared" / "qos_client"
        sha = hashlib.sha256(BIN_BODY).hexdigest()
        with fake_release(_fake_assets()) as base:
            with self._patched_asset_urls(base):
                digest = fc.fetch_binary(
                    repo=REPO, tag=TAG, plat=PLAT, out=out,
                    expected_sha256=sha, token=None, timeout=5.0,
                )
        self.assertEqual(digest, sha)
        self.assertTrue(out.exists())

    def test_sidecar_disagrees_quarantines(self) -> None:
        # Server lies about the sidecar hash; binary content unchanged.
        bogus_sha_line = ("c" * 64 + "  qos_client.linux-amd64\n").encode()
        out = self.tmp / "shared" / "qos_client"
        with fake_release(_fake_assets(sha_override=bogus_sha_line)) as base:
            with self._patched_asset_urls(base):
                with self.assertRaises(fc.FetchError) as cm:
                    fc.fetch_binary(
                        repo=REPO, tag=TAG, plat=PLAT, out=out,
                        expected_sha256=None, token=None, timeout=5.0,
                    )
        self.assertIn("sha256 mismatch", str(cm.exception))
        # Binary was NOT installed.
        self.assertFalse(out.exists())
        # Bad download was quarantined.
        tainted = Path(str(out) + ".tainted")
        self.assertTrue(tainted.exists(), f"expected quarantine at {tainted}")
        self.assertEqual(tainted.read_bytes(), BIN_BODY)

    def test_expected_sha_disagrees_quarantines(self) -> None:
        out = self.tmp / "shared" / "qos_client"
        with fake_release(_fake_assets()) as base:
            with self._patched_asset_urls(base):
                with self.assertRaises(fc.FetchError) as cm:
                    fc.fetch_binary(
                        repo=REPO, tag=TAG, plat=PLAT, out=out,
                        expected_sha256="d" * 64, token=None, timeout=5.0,
                    )
        self.assertIn("--expected-sha256 mismatch", str(cm.exception))
        self.assertFalse(out.exists())
        self.assertTrue(Path(str(out) + ".tainted").exists())

    def test_404_does_not_install_binary(self) -> None:
        # Empty asset map → every URL is 404.
        out = self.tmp / "shared" / "qos_client"
        with fake_release({}) as base:
            with self._patched_asset_urls(base):
                with self.assertRaises(fc.FetchError) as cm:
                    fc.fetch_binary(
                        repo=REPO, tag=TAG, plat=PLAT, out=out,
                        expected_sha256=None, token=None, timeout=5.0,
                    )
        self.assertIn("HTTP 404", str(cm.exception))
        self.assertFalse(out.exists())

    def test_partial_file_from_previous_run_is_cleared(self) -> None:
        # Pre-create a stale `.partial` so we exercise the cleanup branch.
        out = self.tmp / "shared" / "qos_client"
        out.parent.mkdir(parents=True, exist_ok=True)
        stale = out.with_suffix(out.suffix + ".partial")
        stale.write_bytes(b"stale-partial-from-prior-run")
        with fake_release(_fake_assets()) as base:
            with self._patched_asset_urls(base):
                fc.fetch_binary(
                    repo=REPO, tag=TAG, plat=PLAT, out=out,
                    expected_sha256=None, token=None, timeout=5.0,
                )
        # `.partial` no longer exists; install succeeded with the real body.
        self.assertFalse(stale.exists())
        self.assertTrue(out.exists())
        self.assertEqual(out.read_bytes(), BIN_BODY)


class ManualFallbackTests(unittest.TestCase):
    def test_fallback_includes_curl_and_gh_recipes(self) -> None:
        import sys
        captured = io.StringIO()
        with mock.patch.object(sys, "stderr", captured):
            fc.print_manual_fallback(
                reason="HTTP 404",
                repo=REPO,
                tag=TAG,
                plat=PLAT,
                out=Path("/tmp/qc"),
            )
        text = captured.getvalue()
        self.assertIn("Auto-fetch failed: HTTP 404", text)
        self.assertIn("curl -fL", text)
        self.assertIn("shasum -a 256", text)
        self.assertIn("gh release download", text)
        self.assertIn(f"qos_client.{PLAT}", text)

    def test_fallback_when_platform_unknown(self) -> None:
        import sys
        captured = io.StringIO()
        with mock.patch.object(sys, "stderr", captured):
            fc.print_manual_fallback(
                reason="unsupported platform",
                repo=REPO,
                tag=TAG,
                plat=None,
                out=Path("/tmp/qc"),
            )
        text = captured.getvalue()
        self.assertIn("qos_client.<platform>", text)


# ---------------------------------------------------------------------------
# resolve_release_tag — covers the `latest` default that role_init.py relies
# on so first-init "just works" without the operator picking a tag.
# ---------------------------------------------------------------------------


class ResolveReleaseTagTests(unittest.TestCase):
    def test_explicit_tag_is_returned_verbatim_without_network(self) -> None:
        # A non-"latest" value never hits the REST API; the function must
        # return the input string unchanged.
        out = fc.resolve_release_tag(
            REPO, want="0xkey-qos_client-v9.9.9", token=None, timeout=5.0
        )
        self.assertEqual(out, "0xkey-qos_client-v9.9.9")

    def test_default_resolves_via_releases_latest_endpoint(self) -> None:
        with fake_release(_fake_assets()) as base:
            with patch_github_api_base(base):
                # `want=None` is the role_init.py default path.
                resolved = fc.resolve_release_tag(
                    REPO, want=None, token=None, timeout=5.0
                )
        self.assertEqual(resolved, TAG)

    def test_literal_latest_string_resolves_same_as_none(self) -> None:
        with fake_release(_fake_assets()) as base:
            with patch_github_api_base(base):
                resolved = fc.resolve_release_tag(
                    REPO, want=fc.LATEST_TAG, token=None, timeout=5.0
                )
        self.assertEqual(resolved, TAG)

    def test_falls_back_to_prerelease_when_no_stable_exists(self) -> None:
        # Mimic GitHub when only RC tags have been published: /releases/latest
        # returns 404, but /releases?per_page=1 returns the most recent
        # prerelease. The function must return that prerelease tag and emit a
        # stderr WARN so the operator notices.
        rc_tag = "0xkey-qos_client-v0.1.0-rc1"
        assets: dict[str, bytes] = {
            f"/repos/{REPO}/releases?per_page=1": json.dumps(
                [{"tag_name": rc_tag, "prerelease": True}]
            ).encode(),
            # Note: no /releases/latest entry → server returns 404.
        }
        captured = io.StringIO()
        with fake_release(assets) as base:
            with patch_github_api_base(base):
                with mock.patch.object(sys, "stderr", captured):
                    resolved = fc.resolve_release_tag(
                        REPO, want=None, token=None, timeout=5.0
                    )
        self.assertEqual(resolved, rc_tag)
        self.assertIn("WARN", captured.getvalue())
        self.assertIn(rc_tag, captured.getvalue())

    def test_no_releases_at_all_raises(self) -> None:
        # Fresh repo: neither /releases/latest nor /releases?per_page=1
        # return anything useful. We must surface a clear error rather
        # than silently picking an empty tag.
        assets: dict[str, bytes] = {
            f"/repos/{REPO}/releases?per_page=1": b"[]",
        }
        with fake_release(assets) as base:
            with patch_github_api_base(base):
                with self.assertRaises(fc.FetchError) as cm:
                    fc.resolve_release_tag(
                        REPO, want=None, token=None, timeout=5.0
                    )
        self.assertIn("no releases", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
