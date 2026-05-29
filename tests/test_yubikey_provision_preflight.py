from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()


def _cp(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ykman"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _ns(workdir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        dry_run=False,
        qos_client=workdir / "shared" / "qos_client",
        pub_path="outbox/member.pub",
        workdir=workdir,
    )


class YubiKeyProvisionPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.workdir = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_refuses_aes_management_key_algorithm(self) -> None:
        def fake_ykman(args: list[str]) -> subprocess.CompletedProcess[str]:
            if args == ["piv", "info"]:
                return _cp("Management key algorithm: AES192\n")
            self.fail(f"unexpected ykman call: {args}")

        captured = io.StringIO()
        with mock.patch.object(ek.shutil, "which", return_value="/usr/bin/ykman"):
            with mock.patch.object(ek, "_run_ykman_capture", side_effect=fake_ykman):
                with mock.patch.object(sys, "stderr", captured):
                    with self.assertRaises(SystemExit) as cm:
                        ek.preflight_yubikey_provision(_ns(self.workdir))

        self.assertEqual(cm.exception.code, 2)
        err = captured.getvalue()
        self.assertIn("Management key algorithm: AES192", err)
        self.assertIn("--algorithm TDES", err)
        self.assertIn("Management key algorithm: TDES", err)

    def test_occupied_slots_log_and_proceed(self) -> None:
        calls: list[list[str]] = []

        def fake_ykman(args: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args == ["piv", "info"]:
                return _cp("Management key algorithm: TDES\n")
            if args == ["piv", "keys", "info", "9c"]:
                return _cp("Algorithm: ECCP256\nOrigin: GENERATED\n")
            if args == ["piv", "keys", "info", "9d"]:
                return _cp("No private key stored in slot 9d\n", returncode=1)
            self.fail(f"unexpected ykman call: {args}")

        with mock.patch.object(ek.shutil, "which", return_value="/usr/bin/ykman"):
            with mock.patch.object(ek, "_run_ykman_capture", side_effect=fake_ykman):
                with mock.patch.object(ek, "run_process", return_value=0) as run_mock:
                    with mock.patch.object(ek, "audit_file_hash"):
                        ek.cmd_key_yubikey_provision(_ns(self.workdir), audit_log=None)

        self.assertIn(["piv", "keys", "info", "9c"], calls)
        run_mock.assert_called_once()

    def test_both_slots_occupied_logs_both(self) -> None:
        def fake_ykman(args: list[str]) -> subprocess.CompletedProcess[str]:
            if args == ["piv", "info"]:
                return _cp("Management key algorithm: TDES\n")
            if args == ["piv", "keys", "info", "9c"]:
                return _cp("Algorithm: ECCP256\nOrigin: GENERATED\n")
            if args == ["piv", "keys", "info", "9d"]:
                return _cp("Algorithm: ECCP256\nOrigin: GENERATED\n")
            self.fail(f"unexpected ykman call: {args}")

        with mock.patch.object(ek.shutil, "which", return_value="/usr/bin/ykman"):
            with mock.patch.object(ek, "_run_ykman_capture", side_effect=fake_ykman):
                with mock.patch.object(ek, "run_process", return_value=0):
                    with mock.patch.object(ek, "audit_file_hash"):
                        ek.cmd_key_yubikey_provision(_ns(self.workdir), audit_log=None)

    def test_touch_notice_prints_for_clear_slots(self) -> None:
        def fake_ykman(args: list[str]) -> subprocess.CompletedProcess[str]:
            if args == ["piv", "info"]:
                return _cp("Management key algorithm: TDES\n")
            if args in (["piv", "keys", "info", "9c"], ["piv", "keys", "info", "9d"]):
                return _cp("No private key stored in slot\n", returncode=1)
            self.fail(f"unexpected ykman call: {args}")

        captured = io.StringIO()
        with mock.patch.object(ek.shutil, "which", return_value="/usr/bin/ykman"):
            with mock.patch.object(ek, "_run_ykman_capture", side_effect=fake_ykman):
                with mock.patch.object(sys, "stdout", captured):
                    ek.preflight_yubikey_provision(_ns(self.workdir))

        out = captured.getvalue()
        self.assertIn("YubiKey touch required twice", out)
        self.assertIn("slot 9C", out)
        self.assertIn("slot 9D", out)


if __name__ == "__main__":
    unittest.main()
