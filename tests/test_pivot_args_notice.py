"""Tests for the manifest-impact reminder printed by `manifest generate`.

pivot args are baked into the attested manifest, so changing them (notably the
notarizer recipient pubkey and signer / tls-fetcher email parameters) forces a
full re-ceremony. `print_pivot_args_manifest_notice` always surfaces the
effective args plus the re-ceremony consequences so the agent prompts the
operator to confirm env-specific values. It is advisory only and must never
raise / block.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from ._helpers import REPO_ROOT, load_enclave_keyops


ek = load_enclave_keyops()


def _base_raw() -> dict:
    raw = json.loads(
        (REPO_ROOT / "core" / "config.prod.example.json").read_text(encoding="utf-8")
    )
    raw["qos_client_path"] = "shared/qos_client"
    return raw


class PivotArgsManifestNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.wd = Path(self._ctx.name).resolve()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _cfg(self):
        return ek.Config(_base_raw(), workdir=self.wd)

    def _notice(self, cfg) -> str:
        buf = io.StringIO()
        with redirect_stderr(buf):
            ek.print_pivot_args_manifest_notice(cfg)
        return buf.getvalue()

    def test_lists_every_service_with_effective_args(self) -> None:
        cfg = self._cfg()
        out = self._notice(cfg)
        for svc in cfg.all_services():
            self.assertIn(svc["name"], out)
            self.assertIn(ek._pivot_args_for_service(cfg, svc), out)

    def test_warns_about_env_specific_values_and_re_ceremony(self) -> None:
        out = self._notice(self._cfg())
        # the three env-specific params the operator must confirm
        self.assertIn("recipient pubkey", out)
        self.assertIn("email", out)
        # both halves of the re-ceremony cost
        self.assertIn("approve-manifest", out)
        self.assertIn("proxy-re-encrypt-share", out)
        self.assertIn("post", out)

    def test_reflects_overridden_pivot_args(self) -> None:
        raw = _base_raw()
        raw["defaults"]["notarizer_pivot_args"] = "[--recipient,age1examplepubkey]"
        cfg = ek.Config(raw, workdir=self.wd)
        out = self._notice(cfg)
        self.assertIn("age1examplepubkey", out)

    def test_advisory_only_never_raises(self) -> None:
        # missing manifest_nonce / threshold files etc. must not turn the
        # reminder into a blocker; it only reads defaults + service names.
        try:
            self._notice(self._cfg())
        except SystemExit:  # pragma: no cover - failure path
            self.fail("print_pivot_args_manifest_notice must not block/exit")


if __name__ == "__main__":
    unittest.main()
