from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()
parse_member_roster = ek.parse_member_roster
_check_roster_against_pub_dir = ek._check_roster_against_pub_dir


def _well_formed_roster() -> dict:
    return {
        "ceremony": "0xkey-test-2026q2",
        "manifest_set": [
            {"alias": "manifester1", "owner": "Alice"},
            {"alias": "manifester2", "owner": "Bob"},
        ],
        "share_set": [
            {"member_index": 1, "alias": "share-member1"},
            {"member_index": 2, "alias": "share-member2"},
            {"member_index": 3, "alias": "share-member3"},
        ],
        "patch_set": [],
    }


class ParseMemberRosterShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.dir = Path(self._ctx.name)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _write(self, contents) -> Path:
        path = self.dir / "member-roster.json"
        if isinstance(contents, str):
            path.write_text(contents, encoding="utf-8")
        else:
            path.write_text(json.dumps(contents), encoding="utf-8")
        return path

    def test_well_formed(self) -> None:
        roster = parse_member_roster(self._write(_well_formed_roster()))
        self.assertEqual({e["alias"] for e in roster["manifest-set"]}, {"manifester1", "manifester2"})
        self.assertEqual([e["member_index"] for e in roster["share-set"]], [1, 2, 3])
        self.assertEqual(roster["patch-set"], [])

    def test_missing_roster_file_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self.dir / "nope.json")
        self.assertEqual(cm.exception.code, 2)

    def test_invalid_json_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write("{not json"))
        self.assertEqual(cm.exception.code, 2)

    def test_top_level_must_be_object(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write([{"alias": "x"}]))
        self.assertEqual(cm.exception.code, 2)

    def test_set_must_be_list(self) -> None:
        bad = _well_formed_roster()
        bad["share_set"] = {"alias": "share-member1"}
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_entry_must_be_object(self) -> None:
        bad = _well_formed_roster()
        bad["manifest_set"][0] = "manifester1"
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)


class AliasValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.dir = Path(self._ctx.name)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _write(self, contents) -> Path:
        path = self.dir / "member-roster.json"
        path.write_text(json.dumps(contents), encoding="utf-8")
        return path

    def test_duplicate_alias_in_share_set_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["share_set"][1]["alias"] = "share-member1"
        bad["share_set"][1]["member_index"] = 4
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_duplicate_alias_in_manifest_set_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["manifest_set"].append({"alias": "manifester1", "owner": "Eve"})
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_alias_with_path_separator_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["share_set"][0]["alias"] = "../escape"
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_alias_with_slash_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["share_set"][0]["alias"] = "share/member1"
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_empty_alias_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["manifest_set"][0]["alias"] = ""
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_overlong_alias_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["manifest_set"][0]["alias"] = "x" * 65
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_alias_starting_with_dot_rejected(self) -> None:
        # `_ALIAS_RE` requires the first character to be alnum so an alias
        # never collides with hidden files like `.DS_Store`.
        bad = _well_formed_roster()
        bad["manifest_set"][0]["alias"] = ".sneaky"
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)


class MemberIndexValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.dir = Path(self._ctx.name)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _write(self, contents) -> Path:
        path = self.dir / "member-roster.json"
        path.write_text(json.dumps(contents), encoding="utf-8")
        return path

    def test_share_set_entry_without_index_rejected(self) -> None:
        bad = _well_formed_roster()
        del bad["share_set"][0]["member_index"]
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_share_set_index_zero_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["share_set"][0]["member_index"] = 0
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_share_set_index_negative_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["share_set"][0]["member_index"] = -1
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_share_set_index_must_be_int_not_bool(self) -> None:
        # bool is a subclass of int in Python; the parser must reject it.
        bad = _well_formed_roster()
        bad["share_set"][0]["member_index"] = True
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_duplicate_index_rejected(self) -> None:
        bad = _well_formed_roster()
        bad["share_set"][1]["member_index"] = 1
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_non_consecutive_indexes_rejected(self) -> None:
        bad = _well_formed_roster()
        # 1, 2, 4 (gap)
        bad["share_set"][2]["member_index"] = 4
        with self.assertRaises(SystemExit) as cm:
            parse_member_roster(self._write(bad))
        self.assertEqual(cm.exception.code, 2)

    def test_indexes_out_of_order_ok_when_complete(self) -> None:
        # Order in JSON doesn't matter — values just need to be {1..N}.
        bad = _well_formed_roster()
        bad["share_set"] = [
            {"member_index": 3, "alias": "share-member3"},
            {"member_index": 1, "alias": "share-member1"},
            {"member_index": 2, "alias": "share-member2"},
        ]
        roster = parse_member_roster(self._write(bad))
        self.assertEqual(
            sorted(e["member_index"] for e in roster["share-set"]), [1, 2, 3]
        )

    def test_manifest_set_index_optional(self) -> None:
        # Manifest-set entries do NOT need member_index; absence is fine.
        bad = _well_formed_roster()
        bad["manifest_set"][0].pop("owner", None)
        roster = parse_member_roster(self._write(bad))
        self.assertNotIn("member_index", roster["manifest-set"][0])


class CheckRosterAgainstPubDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.dir = Path(self._ctx.name)
        self.pub_dir = self.dir / "share-set"
        self.pub_dir.mkdir()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _touch(self, name: str) -> None:
        (self.pub_dir / name).write_text("dummy", encoding="utf-8")

    def test_match_passes(self) -> None:
        for n in ("share-member1.pub", "share-member2.pub"):
            self._touch(n)
        entries = [
            {"alias": "share-member1", "member_index": 1},
            {"alias": "share-member2", "member_index": 2},
        ]
        _check_roster_against_pub_dir(entries, self.pub_dir, "share-set")

    def test_extra_pub_rejected(self) -> None:
        for n in ("share-member1.pub", "share-member2.pub", "share-member3.pub"):
            self._touch(n)
        entries = [
            {"alias": "share-member1", "member_index": 1},
            {"alias": "share-member2", "member_index": 2},
        ]
        with self.assertRaises(SystemExit) as cm:
            _check_roster_against_pub_dir(entries, self.pub_dir, "share-set")
        self.assertEqual(cm.exception.code, 2)

    def test_missing_pub_rejected(self) -> None:
        self._touch("share-member1.pub")
        entries = [
            {"alias": "share-member1", "member_index": 1},
            {"alias": "share-member2", "member_index": 2},
        ]
        with self.assertRaises(SystemExit) as cm:
            _check_roster_against_pub_dir(entries, self.pub_dir, "share-set")
        self.assertEqual(cm.exception.code, 2)

    def test_alias_mismatch_with_filename_rejected(self) -> None:
        # The .pub stem MUST byte-equal the alias in the roster. A coincidental
        # but different filename should be rejected, not silently accepted.
        self._touch("share-member1.pub")
        self._touch("share-member-two.pub")  # operator typo
        entries = [
            {"alias": "share-member1", "member_index": 1},
            {"alias": "share-member2", "member_index": 2},  # roster has member2, fs has -two
        ]
        with self.assertRaises(SystemExit) as cm:
            _check_roster_against_pub_dir(entries, self.pub_dir, "share-set")
        self.assertEqual(cm.exception.code, 2)

    def test_skipped_when_dir_absent(self) -> None:
        # Members of disabled patch-set typically have no pub_dir on disk.
        # Skipping is correct so a non-coordinator workdir doesn't false-fail.
        _check_roster_against_pub_dir(
            [{"alias": "patcher1"}], self.dir / "no-such-dir", "patch-set"
        )

    def test_empty_roster_rejects_extras(self) -> None:
        self._touch("ghost.pub")
        with self.assertRaises(SystemExit) as cm:
            _check_roster_against_pub_dir([], self.pub_dir, "patch-set")
        self.assertEqual(cm.exception.code, 2)

    def test_empty_roster_and_empty_dir_passes(self) -> None:
        # Disabled set, properly cleared.
        _check_roster_against_pub_dir([], self.pub_dir, "patch-set")


if __name__ == "__main__":
    unittest.main()
