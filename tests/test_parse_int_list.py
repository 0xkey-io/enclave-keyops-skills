from __future__ import annotations

import unittest

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()
parse_int_list = ek.parse_int_list
post_order_for_svc = ek.post_order_for_svc


class ParseIntListTests(unittest.TestCase):
    def test_none_passthrough(self) -> None:
        self.assertIsNone(parse_int_list(None))

    def test_empty_string_passthrough(self) -> None:
        self.assertIsNone(parse_int_list(""))
        self.assertIsNone(parse_int_list("   "))

    def test_simple_csv(self) -> None:
        self.assertEqual(parse_int_list("1,2,3"), [1, 2, 3])

    def test_member_prefix_stripped(self) -> None:
        self.assertEqual(parse_int_list("m2,m1"), [2, 1])

    def test_whitespace_stripped(self) -> None:
        self.assertEqual(parse_int_list("  1 , 2 ,  3  "), [1, 2, 3])

    def test_invalid_token_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            parse_int_list("1,xx,2")
        self.assertEqual(cm.exception.code, 2)


class PostOrderForSvcTests(unittest.TestCase):
    def test_uses_per_svc_list(self) -> None:
        svc = {"post_share_members_order": [3, 1, 2]}
        self.assertEqual(post_order_for_svc(svc, None), [3, 1, 2])

    def test_uses_per_svc_string(self) -> None:
        svc = {"post_share_members_order": "m3,m1,m2"}
        self.assertEqual(post_order_for_svc(svc, None), [3, 1, 2])

    def test_falls_back_to_global(self) -> None:
        svc = {"post_share_members_order": None}
        self.assertEqual(post_order_for_svc(svc, [4, 5]), [4, 5])

    def test_default_when_no_input(self) -> None:
        svc = {"post_share_members_order": None}
        self.assertEqual(post_order_for_svc(svc, None), [1, 2])


if __name__ == "__main__":
    unittest.main()
