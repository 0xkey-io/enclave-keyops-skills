"""Tests for the immutable manifest-envelope baseline.

`manifest envelope` snapshots each generated envelope under `manifest/original/`
read-only. Boot / attestation / share-request packaging then refuse to run when
the working envelope drifts from that baseline -- the failure mode that surfaced
in production as an opaque `ProtocolMsgDeserialization` after shareSetApprovals
were injected into the envelope JSON.
"""
from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()


class EnvelopeIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.mroot = Path(self._ctx.name).resolve()
        self.svc = "signer"
        self.working = self.mroot / ek._envelope_name(self.svc)
        self.working.write_text('{"manifest":"x"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        # restore write bit so TemporaryDirectory cleanup can unlink the snapshot
        orig = ek.original_envelope_path(self.mroot, self.svc)
        if orig.exists():
            orig.chmod(0o644)
        self._ctx.cleanup()

    def test_write_creates_readonly_original(self) -> None:
        ek.write_original_envelope(self.mroot, self.svc)
        orig = ek.original_envelope_path(self.mroot, self.svc)
        self.assertTrue(orig.is_file())
        self.assertEqual(orig.read_bytes(), self.working.read_bytes())
        mode = stat.S_IMODE(orig.stat().st_mode)
        self.assertFalse(mode & stat.S_IWUSR, "original snapshot must be read-only")

    def test_write_overwrites_prior_readonly_snapshot(self) -> None:
        ek.write_original_envelope(self.mroot, self.svc)
        # regenerate the envelope with new bytes; snapshot must refresh despite
        # the prior copy being read-only.
        self.working.write_text('{"manifest":"v2"}\n', encoding="utf-8")
        ek.write_original_envelope(self.mroot, self.svc)
        orig = ek.original_envelope_path(self.mroot, self.svc)
        self.assertEqual(orig.read_bytes(), self.working.read_bytes())

    def test_unmodified_passes(self) -> None:
        ek.write_original_envelope(self.mroot, self.svc)
        buf = io.StringIO()
        with redirect_stderr(buf):
            ek.assert_envelope_unmodified(self.mroot, self.svc)
        self.assertEqual(buf.getvalue(), "")

    def test_modified_fails_loud(self) -> None:
        ek.write_original_envelope(self.mroot, self.svc)
        self.working.write_text('{"manifest":"x","shareSetApprovals":[1]}\n', encoding="utf-8")
        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(SystemExit) as cm:
            ek.assert_envelope_unmodified(self.mroot, self.svc)
        self.assertEqual(cm.exception.code, 2)
        msg = buf.getvalue()
        self.assertIn("differs from the immutable original", msg)
        self.assertIn("ProtocolMsgDeserialization", msg)
        # the restore hint points at the original snapshot
        self.assertIn(str(ek.original_envelope_path(self.mroot, self.svc)), msg)

    def test_missing_original_is_advisory(self) -> None:
        # legacy workdir: no baseline yet -> warn but never block
        buf = io.StringIO()
        with redirect_stderr(buf):
            ek.assert_envelope_unmodified(self.mroot, self.svc)
        self.assertIn("no immutable envelope baseline", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
