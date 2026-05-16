from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()
parse_quorum_threshold = ek.parse_quorum_threshold


class ParseQuorumThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.dir = Path(self._ctx.name)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _write(self, contents: str) -> Path:
        p = self.dir / "quorum_threshold"
        p.write_text(contents, encoding="utf-8")
        return p

    def test_simple_int(self) -> None:
        self.assertEqual(parse_quorum_threshold(self._write("2"), set_label="x"), 2)

    def test_trailing_newline_ok(self) -> None:
        self.assertEqual(parse_quorum_threshold(self._write("3\n"), set_label="x"), 3)

    def test_surrounding_whitespace_ok(self) -> None:
        self.assertEqual(parse_quorum_threshold(self._write("  4  \n"), set_label="x"), 4)

    def test_missing_file_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_quorum_threshold(self.dir / "nope", set_label="x")
        self.assertEqual(cm.exception.code, 2)

    def test_zero_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_quorum_threshold(self._write("0"), set_label="x")
        self.assertEqual(cm.exception.code, 2)

    def test_negative_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_quorum_threshold(self._write("-1"), set_label="x")
        self.assertEqual(cm.exception.code, 2)

    def test_kv_form_rejected(self) -> None:
        # Canvases sometimes show `=2` shorthand; the wire format is a bare int.
        with self.assertRaises(SystemExit) as cm:
            parse_quorum_threshold(self._write("=2"), set_label="x")
        self.assertEqual(cm.exception.code, 2)

    def test_yaml_like_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_quorum_threshold(self._write("threshold: 2"), set_label="x")
        self.assertEqual(cm.exception.code, 2)

    def test_multi_line_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_quorum_threshold(self._write("2\n3"), set_label="x")
        self.assertEqual(cm.exception.code, 2)

    def test_comment_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_quorum_threshold(self._write("# comment\n2"), set_label="x")
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
