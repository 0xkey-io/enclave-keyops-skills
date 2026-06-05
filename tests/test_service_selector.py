"""Tests for the `--service` selector that enables single-service recovery.

`filter_services` / `selected_services` scope a ceremony command to a subset of
the five enclave services. Omitting `--service` keeps the all-five default;
naming services limits the command so a failed service can be recovered without
re-booting (and thereby wiping the quorum key of) the healthy ones.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ._helpers import REPO_ROOT, load_enclave_keyops


ek = load_enclave_keyops()


def _base_raw() -> dict:
    raw = json.loads(
        (REPO_ROOT / "core" / "config.prod.example.json").read_text(encoding="utf-8")
    )
    raw["qos_client_path"] = "shared/qos_client"
    return raw


class ServiceSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.wd = Path(self._ctx.name).resolve()
        self.cfg = ek.Config(_base_raw(), workdir=self.wd)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def test_none_returns_all_five(self) -> None:
        got = ek.filter_services(self.cfg, None)
        self.assertEqual(len(got), 5)
        self.assertEqual(
            [s["name"] for s in got], [s["name"] for s in self.cfg.all_services()]
        )

    def test_empty_returns_all_five(self) -> None:
        self.assertEqual(len(ek.filter_services(self.cfg, [])), 5)

    def test_single_service_subset(self) -> None:
        got = ek.filter_services(self.cfg, ["signer"])
        self.assertEqual([s["name"] for s in got], ["signer"])

    def test_multiple_services_preserve_roster_order(self) -> None:
        # request out of order; result follows config/roster order, not arg order
        got = ek.filter_services(self.cfg, ["notarizer", "signer"])
        names = [s["name"] for s in got]
        self.assertIn("signer", names)
        self.assertIn("notarizer", names)
        self.assertEqual(names, sorted(names, key=lambda n: [s["name"] for s in self.cfg.all_services()].index(n)))

    def test_duplicates_deduped(self) -> None:
        got = ek.filter_services(self.cfg, ["signer", "signer"])
        self.assertEqual([s["name"] for s in got], ["signer"])

    def test_unknown_service_fails_loud(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            ek.filter_services(self.cfg, ["does-not-exist"])
        self.assertEqual(cm.exception.code, 2)

    def test_selected_services_reads_ns_service(self) -> None:
        ns = ek.argparse.Namespace(service=["tls-fetcher"])
        self.assertEqual(
            [s["name"] for s in ek.selected_services(self.cfg, ns)], ["tls-fetcher"]
        )

    def test_selected_services_missing_attr_defaults_to_all(self) -> None:
        ns = ek.argparse.Namespace()  # no `service` attr at all
        self.assertEqual(len(ek.selected_services(self.cfg, ns)), 5)


if __name__ == "__main__":
    unittest.main()
