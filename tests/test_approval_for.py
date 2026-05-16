from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()
approval_for = ek.approval_for


def make_approvals(root: Path, names) -> None:
    """Create an `approvals/<svc>/` directory tree containing empty .approval
    files named per `names`. Returns the parent that callers should pass as
    `mroot` (i.e. the one above `approvals/`).
    """
    d = root / "approvals" / "signer"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / f"{name}.approval").write_text("", encoding="utf-8")


class ApprovalForTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.mroot = Path(self._ctx.name)
        self.svc = {
            "name": "signer",
            "manifest_namespace": "0xkey/signer",
            "manifest_nonce": 7,
        }

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def test_picks_exact_match(self) -> None:
        make_approvals(
            self.mroot,
            [
                "manifester1-0xkey-signer-7",
                "manifester2-0xkey-signer-7",
                "manifester1-0xkey-policy-engine-7",
            ],
        )
        match = approval_for(self.mroot, self.svc, "manifester1")
        self.assertEqual(match.stem, "manifester1-0xkey-signer-7")

    def test_rejects_when_no_match(self) -> None:
        make_approvals(self.mroot, ["manifester1-0xkey-signer-6"])  # wrong nonce
        with self.assertRaises(SystemExit) as cm:
            approval_for(self.mroot, self.svc, "manifester1")
        self.assertEqual(cm.exception.code, 2)

    def test_rejects_when_alias_missing(self) -> None:
        make_approvals(self.mroot, ["manifester2-0xkey-signer-7"])
        with self.assertRaises(SystemExit) as cm:
            approval_for(self.mroot, self.svc, "manifester1")
        self.assertEqual(cm.exception.code, 2)

    def test_rejects_when_namespace_mismatch(self) -> None:
        make_approvals(self.mroot, ["manifester1-0xkey-policy-engine-7"])  # wrong svc namespace
        with self.assertRaises(SystemExit) as cm:
            approval_for(self.mroot, self.svc, "manifester1")
        self.assertEqual(cm.exception.code, 2)

    def test_rejects_when_two_aliases_share_prefix(self) -> None:
        # "manifester1" should not match a file starting with "manifester10-".
        make_approvals(
            self.mroot,
            ["manifester10-0xkey-signer-7"],
        )
        with self.assertRaises(SystemExit) as cm:
            approval_for(self.mroot, self.svc, "manifester1")
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
