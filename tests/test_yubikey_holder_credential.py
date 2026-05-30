"""Tests for the YubiKey-vs-file holder credential plumbing.

`resolve_holder_credential` is the single place that decides how to turn the
operator's `--yubikey` / `--secret-path` choice into a qos_client argv slice.
It backs three handlers that share the same shape (Manifest approve, Share
ceremony reencrypt, Share ceremony share-extract). Three invariants matter:

1. Mutual exclusion. Both flags → hard error. Neither flag → hard error.
2. Workdir-external enforcement applies ONLY to the file mode. YubiKey mode
   never even consults the secret-path argument and so must not trip the
   workdir-leak check.
3. The argv slice has the exact qos_client-expected shape: either
   `["--yubikey"]` or `["--secret-path", "<path>"]`.

The handler-level tests also verify the argparsers no longer hard-require
`--secret-path`, which is the user-visible side of the same change.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()


def _make_ns(yubikey: bool = False, secret_path: Any = None) -> argparse.Namespace:
    return argparse.Namespace(yubikey=yubikey, secret_path=secret_path)


class ResolveHolderCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.workdir = Path(self._ctx.name) / "role-workdir"
        self.workdir.mkdir(parents=True)
        # An external vault path is anything resolvable outside workdir.
        self.ext_vault = Path(self._ctx.name) / "ext" / "alias.secret"
        self.ext_vault.parent.mkdir(parents=True)
        self.ext_vault.write_text("dummy", encoding="utf-8")

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def test_neither_flag_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            ek.resolve_holder_credential(_make_ns(), workdir=self.workdir)
        self.assertEqual(cm.exception.code, 2)

    def test_both_flags_are_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            ek.resolve_holder_credential(
                _make_ns(yubikey=True, secret_path=str(self.ext_vault)),
                workdir=self.workdir,
            )
        self.assertEqual(cm.exception.code, 2)

    def test_yubikey_only_returns_single_flag(self) -> None:
        argv = ek.resolve_holder_credential(
            _make_ns(yubikey=True), workdir=self.workdir
        )
        self.assertEqual(argv, ["--yubikey"])

    def test_secret_path_external_is_accepted(self) -> None:
        argv = ek.resolve_holder_credential(
            _make_ns(secret_path=str(self.ext_vault)),
            workdir=self.workdir,
        )
        self.assertEqual(argv, ["--secret-path", str(self.ext_vault)])

    def test_secret_path_inside_workdir_is_rejected(self) -> None:
        inside = self.workdir / "alias.secret"
        inside.write_text("dummy", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            ek.resolve_holder_credential(
                _make_ns(secret_path=str(inside)),
                workdir=self.workdir,
            )
        self.assertEqual(cm.exception.code, 2)

    def test_relative_secret_path_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            ek.resolve_holder_credential(
                _make_ns(secret_path="alias.secret"),
                workdir=self.workdir,
            )
        self.assertEqual(cm.exception.code, 2)

    def test_yubikey_mode_skips_workdir_path_check(self) -> None:
        """YubiKey mode must NOT trip the workdir-leak check even if a stale
        --secret-path string would otherwise be rejected. Mutual exclusion is
        a hard error, so we drive this by passing only --yubikey."""
        # If yubikey-mode wrongly consulted secret_path, this would raise.
        argv = ek.resolve_holder_credential(
            _make_ns(yubikey=True, secret_path=None),
            workdir=self.workdir,
        )
        self.assertNotIn("--secret-path", argv)


class HolderCredentialParserShapeTests(unittest.TestCase):
    """The real argparser of enclave_keyops.py must:

    - accept `--yubikey` as a boolean flag on every command that supports it
    - NOT require `--secret-path` (deferred to the handler via the helper)
    """

    def setUp(self) -> None:
        self.parser = ek.build_parser()
        # build_parser requires --config/--workdir up-front; pre-populate so
        # subcommand parsing reflects what the real CLI would see.
        self.prefix = ["--config", "/abs/cfg.json", "--workdir", "/abs/wd"]

    def test_manifest_approve_accepts_yubikey_without_secret_path(self) -> None:
        ns = self.parser.parse_args(self.prefix + [
            "manifest", "approve",
            "--alias", "manifester1",
            "--yubikey",
            "--service", "signer",
        ])
        self.assertTrue(ns.yubikey)
        self.assertIsNone(ns.secret_path)

    def test_manifest_approve_accepts_secret_path_without_yubikey(self) -> None:
        ns = self.parser.parse_args(self.prefix + [
            "manifest", "approve",
            "--alias", "manifester1",
            "--secret-path", "/abs/ext/manifester1.secret",
            "--service", "signer",
        ])
        self.assertFalse(ns.yubikey)
        self.assertEqual(ns.secret_path, "/abs/ext/manifester1.secret")

    def test_manifest_approve_no_longer_requires_secret_path(self) -> None:
        # Before the YubiKey change, omitting --secret-path here was an
        # argparse-level error (SystemExit). It must now reach the handler.
        ns = self.parser.parse_args(self.prefix + [
            "manifest", "approve",
            "--alias", "manifester1",
            "--service", "signer",
        ])
        self.assertFalse(ns.yubikey)
        self.assertIsNone(ns.secret_path)

    def test_ceremony_reencrypt_accepts_yubikey_without_secret_path(self) -> None:
        ns = self.parser.parse_args(self.prefix + [
            "ceremony", "reencrypt",
            "--alias", "share-member2",
            "--yubikey",
            "--share-path", "/abs/ext/share-member2.share",
            "--member-index", "2",
        ])
        self.assertTrue(ns.yubikey)
        self.assertIsNone(ns.secret_path)

    def test_ceremony_reencrypt_no_longer_requires_secret_path(self) -> None:
        ns = self.parser.parse_args(self.prefix + [
            "ceremony", "reencrypt",
            "--alias", "share-member2",
            "--share-path", "/abs/ext/share-member2.share",
            "--member-index", "2",
        ])
        self.assertFalse(ns.yubikey)
        self.assertIsNone(ns.secret_path)

    def test_ceremony_share_extract_accepts_yubikey_without_secret_path(self) -> None:
        ns = self.parser.parse_args(self.prefix + [
            "ceremony", "share-extract",
            "--alias", "share-member2",
            "--member-index", "2",
            "--yubikey",
            "--share-path", "/abs/ext/share-member2.share",
        ])
        self.assertTrue(ns.yubikey)
        self.assertIsNone(ns.secret_path)


class ManifestApproveArgvCompositionTests(unittest.TestCase):
    """Drive `cmd_manifest_approve` end-to-end and capture qos_client argv to
    prove the --yubikey flag is forwarded (or replaced by --secret-path)
    exactly once per service invocation."""

    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._ctx.name)
        self.workdir = self.tmp / "workdir"
        self.workdir.mkdir()
        self.ext = self.tmp / "ext"
        self.ext.mkdir()
        (self.ext / "manifester1.secret").write_text("dummy", encoding="utf-8")
        (self.workdir / "manifest").mkdir()
        (self.workdir / "manifest" / "signer-manifest.json").write_text("{}", encoding="utf-8")
        (self.workdir / "qos-release").mkdir()
        (self.workdir / "qos-release" / "nitro.pcrs").write_text("pcrs", encoding="utf-8")
        (self.workdir / "qos-release" / "aws-x86_64.pcrs").write_text("pcrs", encoding="utf-8")
        (self.workdir / "pivot-hashes").mkdir()
        (self.workdir / "pivot-hashes" / "signer-pivot-hash.txt").write_text("h", encoding="utf-8")
        (self.workdir / "pcr3-preimage.txt").write_text("p", encoding="utf-8")
        (self.workdir / "quorum_key.pub").write_text("q", encoding="utf-8")
        (self.workdir / "manifest" / "manifest-set").mkdir()
        (self.workdir / "manifest" / "share-set").mkdir()
        (self.workdir / "manifest" / "patch-set").mkdir()
        self.cfg = self._make_cfg()
        self._captured: List[List[str]] = []
        self._confirm_calls: List[str] = []
        self._orig_run_process = ek.run_process
        self._orig_confirm = ek.confirm_dangerous
        self._orig_audit_file = ek.audit_file_hash

        def fake_run(argv, *, dry_run, cwd, audit_log, allow_failure=False):
            self._captured.append(list(argv))
            return 0

        def recording_confirm(ns, msg, phrase):
            # Record the confirmation phrase so individual tests can prove
            # that argument-level errors raise SystemExit BEFORE any
            # confirm_dangerous prompt fires (regression for the 2026-05-16
            # UX bug where wrong --yubikey/--secret-path combinations still
            # demanded the operator type "approve-manifest" first).
            self._confirm_calls.append(phrase)

        def noop_audit(*args, **kwargs):
            return None

        ek.run_process = fake_run
        ek.confirm_dangerous = recording_confirm
        ek.audit_file_hash = noop_audit

    def tearDown(self) -> None:
        ek.run_process = self._orig_run_process
        ek.confirm_dangerous = self._orig_confirm
        ek.audit_file_hash = self._orig_audit_file
        self._ctx.cleanup()

    def _make_cfg(self) -> Any:
        # Place a stub qos_client binary so Config's expanduser is harmless;
        # we don't actually exec it because run_process is mocked.
        qc = self.tmp / "qos_client"
        qc.write_text("#!/bin/sh\n", encoding="utf-8")
        qc.chmod(0o755)
        raw: Dict[str, Any] = {
            "qos_client_path": str(qc),
            "kubectl_path": "kubectl",
            "kubernetes_namespace": "0xkey-enclave",
            "kustomize_overlay_path": "/abs",
            "paths": {
                "workdir_manifest_subdir": "manifest",
                "qos_release_dir": "qos-release",
                "pcr3_preimage_path": "pcr3-preimage.txt",
                "quorum_key_pub_path": "quorum_key.pub",
                "dr_key_pub_path": "dr-key.pub",
                "pivots_dir": "pivots",
                "pivot_hashes_dir": "pivot-hashes",
                "manifest_set_dir": "manifest/manifest-set",
                "share_set_dir": "manifest/share-set",
                "patch_set_dir": "manifest/patch-set",
                "member_roster_path": "shared/member-roster.json",
            },
            "defaults": {
                "approve_unsafe_auto_confirm": False,
            },
            "deploy": {"require_context_match": True},
            "verification": {"data_plane_port": 8081, "use_kubectl_for_health": True},
            "services": [
                {
                    "name": "signer",
                    "manifest_namespace": "0xkey/signer",
                    "manifest_nonce": 1,
                    "host_port_qos": 3001,
                    "pivot_binary_name": "signer",
                    "data_plane_health_path": "/health",
                    "data_plane_post_path": "/v1/signer",
                    "deployment_label_app": "signer",
                    "post_share_members_order": None,
                },
            ],
        }
        return ek.Config(raw, workdir=self.workdir)

    def _ns(self, **overrides: Any) -> argparse.Namespace:
        defaults = dict(
            dry_run=False,
            alias="manifester1",
            secret_path=None,
            yubikey=False,
            service="signer",
            skip_display=True,
            unsafe_auto_confirm=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _patched_all_services(self) -> None:
        # all_services rejects when len != 5; we override for this single-svc test.
        ek.Config.all_services = lambda self: list(self.raw["services"])  # type: ignore

    def test_yubikey_argv_carries_yubikey_flag(self) -> None:
        self._patched_all_services()
        ek.cmd_manifest_approve(self._ns(yubikey=True), self.cfg, audit_log=None)
        self.assertEqual(len(self._captured), 1)
        argv = self._captured[0]
        self.assertIn("--yubikey", argv)
        self.assertNotIn("--secret-path", argv)
        self.assertIn("approve-manifest", argv)

    def test_secret_path_argv_carries_secret_path(self) -> None:
        self._patched_all_services()
        ek.cmd_manifest_approve(
            self._ns(secret_path=str(self.ext / "manifester1.secret")),
            self.cfg,
            audit_log=None,
        )
        self.assertEqual(len(self._captured), 1)
        argv = self._captured[0]
        self.assertIn("--secret-path", argv)
        self.assertEqual(
            argv[argv.index("--secret-path") + 1],
            str(self.ext / "manifester1.secret"),
        )
        self.assertNotIn("--yubikey", argv)

    def test_mutual_exclusion_rejects_before_run(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            ek.cmd_manifest_approve(
                self._ns(
                    yubikey=True,
                    secret_path=str(self.ext / "manifester1.secret"),
                ),
                self.cfg,
                audit_log=None,
            )
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self._captured, [])

    def test_neither_flag_rejects_before_run(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            ek.cmd_manifest_approve(self._ns(), self.cfg, audit_log=None)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self._captured, [])

    def test_mutex_rejects_before_confirm_prompt(self) -> None:
        # Regression for the 2026-05-16 UX bug: parameter-level errors
        # must NOT first force the operator to type "approve-manifest" /
        # "reencrypt-share" / "after-genesis" before reporting the
        # mistake. confirm_dangerous must not fire if the argv is
        # malformed.
        with self.assertRaises(SystemExit):
            ek.cmd_manifest_approve(
                self._ns(
                    yubikey=True,
                    secret_path=str(self.ext / "manifester1.secret"),
                ),
                self.cfg,
                audit_log=None,
            )
        self.assertEqual(
            self._confirm_calls,
            [],
            "confirm_dangerous fired before parameter validation rejected the call",
        )

    def test_neither_flag_rejects_before_confirm_prompt(self) -> None:
        with self.assertRaises(SystemExit):
            ek.cmd_manifest_approve(self._ns(), self.cfg, audit_log=None)
        self.assertEqual(self._confirm_calls, [])

    def test_valid_yubikey_call_still_triggers_confirm(self) -> None:
        # Inverse safety check: the happy path MUST still hit
        # confirm_dangerous. If a future refactor accidentally moves the
        # confirm gate so far down that valid calls skip it, that's a
        # silent loss of the human gate and just as bad as the original
        # UX bug.
        self._patched_all_services()
        ek.cmd_manifest_approve(self._ns(yubikey=True), self.cfg, audit_log=None)
        self.assertIn(
            "approve-manifest",
            self._confirm_calls,
            "happy path skipped the approve-manifest confirmation gate",
        )


if __name__ == "__main__":
    unittest.main()
