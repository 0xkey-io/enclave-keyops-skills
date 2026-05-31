"""End-to-end tests for `bundle install` (install_bundle).

These tests pin the contract between `create_bundle` (producer side) and
`install_bundle` (consumer side): a bundle created on one workdir, when
installed into a fresh workdir, must land every file at the exact path the
downstream command reads from. The downstream read locations are encoded
here on purpose so a future change to either side that breaks the round
trip fails loudly.

Downstream readers (see dist/src/enclave_keyops.py):
  - review          -> cmd_manifest_approve
  - genesis-output  -> cmd_ceremony_share_extract  (--namespace-dir default
                       "incoming/genesis-output")
  - share-request   -> cmd_ceremony_reencrypt       (envelopes/approvals under
                       workdir_manifest_subdir; attestations under the
                       --attest-dir default "attestations")
  - approvals       -> mroot/approvals
  - wrapped-shares  -> cmd_ceremony_post             (--wrapped-in-dir default
                       "wrapped-shares-coordinator")
"""
from __future__ import annotations

import json
import os
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


def _touch(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class InstallRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        # resolve() so we compare against the canonical path; the production
        # code resolves symlinks internally (covered separately below).
        self.base = Path(self._ctx.name).resolve()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _cfg(self, name: str):
        wd = self.base / name
        wd.mkdir(parents=True, exist_ok=True)
        return ek.Config(_base_raw(), workdir=wd), wd

    def _services(self, cfg) -> list[str]:
        return [s["name"] for s in cfg.all_services()]

    # ----- review -----------------------------------------------------------
    def test_review_round_trip(self) -> None:
        cfg, wd = self._cfg("src_review")
        p = cfg.paths()
        mroot = wd / p["workdir_manifest_subdir"]
        svcs = self._services(cfg)
        for s in svcs:
            _touch(mroot / f"{s}-manifest.json")
        for key in ("manifest_set_dir", "share_set_dir", "patch_set_dir"):
            _touch(wd / p[key] / "alice.pub")
        _touch(wd / p["quorum_key_pub_path"])
        _touch(wd / p["pcr3_preimage_path"])
        _touch(wd / p["pivot_hashes_dir"] / "signer.txt")
        _touch(wd / p["qos_release_dir"] / "nitro.pcrs")
        _touch(wd / p["qos_release_dir"] / "aws-x86_64.pcrs")
        _touch(wd / p["member_roster_path"], "{}")

        broot = wd / "outbox" / "review"
        ek.create_bundle(broot, "review", cfg)

        cfg2, wd2 = self._cfg("member_review")
        ek.install_bundle(broot, cfg2)
        p2 = cfg2.paths()
        mroot2 = wd2 / p2["workdir_manifest_subdir"]

        for s in svcs:
            self.assertTrue((mroot2 / f"{s}-manifest.json").is_file(), s)
        self.assertTrue((wd2 / p2["manifest_set_dir"] / "alice.pub").is_file())
        self.assertTrue((wd2 / p2["share_set_dir"] / "alice.pub").is_file())
        self.assertTrue((wd2 / p2["patch_set_dir"] / "alice.pub").is_file())
        self.assertTrue((wd2 / p2["quorum_key_pub_path"]).is_file())
        self.assertTrue((wd2 / p2["pcr3_preimage_path"]).is_file())
        self.assertTrue((wd2 / p2["pivot_hashes_dir"] / "signer.txt").is_file())
        self.assertTrue((wd2 / p2["qos_release_dir"] / "nitro.pcrs").is_file())
        self.assertTrue((wd2 / p2["qos_release_dir"] / "aws-x86_64.pcrs").is_file())
        self.assertTrue((wd2 / p2["member_roster_path"]).is_file())

    # ----- genesis-output ----------------------------------------------------
    def test_genesis_output_round_trip(self) -> None:
        cfg, wd = self._cfg("src_gen")
        p = cfg.paths()
        gout = wd / p.get("genesis_output_dir", "genesis-output")
        _touch(gout / "genesis_output")
        _touch(gout / "quorum_key.pub")
        _touch(wd / p["pcr3_preimage_path"])
        _touch(wd / p["qos_release_dir"] / "nitro.pcrs")
        _touch(wd / p["qos_release_dir"] / "aws-x86_64.pcrs")
        _touch(wd / p["member_roster_path"], "{}")

        broot = wd / "outbox" / "gen"
        ek.create_bundle(broot, "genesis-output", cfg)

        cfg2, wd2 = self._cfg("member_gen")
        ek.install_bundle(broot, cfg2)
        p2 = cfg2.paths()

        # share-extract default --namespace-dir is "incoming/genesis-output".
        ns_dir = wd2 / "incoming" / "genesis-output"
        self.assertTrue((ns_dir / "genesis_output").is_file())
        self.assertTrue((ns_dir / "quorum_key.pub").is_file())
        self.assertTrue((wd2 / p2["qos_release_dir"] / "nitro.pcrs").is_file())
        self.assertTrue((wd2 / p2["pcr3_preimage_path"]).is_file())

    # ----- share-request -----------------------------------------------------
    def test_share_request_round_trip_matches_reencrypt_reads(self) -> None:
        cfg, wd = self._cfg("src_req")
        p = cfg.paths()
        mroot = wd / p["workdir_manifest_subdir"]
        svcs = self._services(cfg)
        for s in svcs:
            _touch(mroot / f"{s}-manifest-envelope.json", f"env-{s}")
            _touch(wd / "attestations" / f"{s}.cose", f"cose-{s}")
            _touch(mroot / "approvals" / s / "alice.approval", f"appr-{s}")
        _touch(wd / p["manifest_set_dir"] / "alice.pub")
        _touch(wd / p["pcr3_preimage_path"])
        _touch(wd / p["member_roster_path"], "{}")

        broot = wd / "outbox" / "req"
        ek.create_bundle(broot, "share-request", cfg)

        cfg2, wd2 = self._cfg("member_req")
        ek.install_bundle(broot, cfg2)
        p2 = cfg2.paths()
        mroot2 = wd2 / p2["workdir_manifest_subdir"]

        # These are exactly the paths cmd_ceremony_reencrypt reads.
        for s in svcs:
            self.assertTrue(
                (mroot2 / f"{s}-manifest-envelope.json").is_file(),
                f"envelope for {s} must land in workdir_manifest_subdir",
            )
            self.assertTrue(
                (wd2 / "attestations" / f"{s}.cose").is_file(),
                f"attestation for {s} must land in default --attest-dir",
            )
            self.assertTrue(
                (mroot2 / "approvals" / s / "alice.approval").is_file(),
                f"approval for {s} must land under mroot/approvals",
            )
        self.assertTrue((wd2 / p2["manifest_set_dir"] / "alice.pub").is_file())
        self.assertTrue((wd2 / p2["pcr3_preimage_path"]).is_file())

    # ----- wrapped-shares ----------------------------------------------------
    def test_wrapped_shares_round_trip_matches_post_reads(self) -> None:
        cfg, wd = self._cfg("src_wrap")
        svcs = self._services(cfg)
        for s in svcs:
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")

        broot = wd / "outbox" / "wrap"
        ek.create_bundle(broot, "wrapped-shares", cfg)

        cfg2, wd2 = self._cfg("coord_wrap")
        ek.install_bundle(broot, cfg2)

        # ceremony post default --wrapped-in-dir is "wrapped-shares-coordinator".
        for s in svcs:
            self.assertTrue(
                (wd2 / "wrapped-shares-coordinator" / s / "member1_eph_wrapped.share").is_file(),
                f"wrapped share for {s} must land in wrapped-shares-coordinator",
            )

    # ----- approvals ---------------------------------------------------------
    def test_approvals_round_trip(self) -> None:
        cfg, wd = self._cfg("src_appr")
        p = cfg.paths()
        mroot = wd / p["workdir_manifest_subdir"]
        svcs = self._services(cfg)
        for s in svcs:
            _touch(mroot / "approvals" / s / "alice.approval")

        broot = wd / "outbox" / "appr"
        ek.create_bundle(broot, "approvals", cfg)

        cfg2, wd2 = self._cfg("member_appr")
        ek.install_bundle(broot, cfg2)
        mroot2 = wd2 / cfg2.paths()["workdir_manifest_subdir"]
        for s in svcs:
            self.assertTrue((mroot2 / "approvals" / s / "alice.approval").is_file(), s)


class ManifestNonceBackfillTests(unittest.TestCase):
    """install_bundle for share-request must backfill null manifest_nonces
    from BUNDLE.json."""

    def test_backfill_null_nonces_persisted_to_disk(self) -> None:
        """Cross-invocation contract: the backfilled nonce must be written to
        config.json on disk, because `bundle install` and `ceremony reencrypt`
        run as separate keyops processes. An in-memory-only patch would be
        lost when the install process exits."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()

            # Producer: create a share-request bundle with nonces set to 3.
            raw_src = _base_raw()
            for svc in raw_src["services"]:
                svc["manifest_nonce"] = 3
            cfg_src = ek.Config(raw_src, workdir=base / "src")
            (base / "src").mkdir(parents=True, exist_ok=True)
            p = cfg_src.paths()
            mroot = base / "src" / p["workdir_manifest_subdir"]
            svcs = [s["name"] for s in cfg_src.all_services()]
            for s in svcs:
                _touch(mroot / f"{s}-manifest-envelope.json")
                _touch(base / "src" / "attestations" / f"{s}.cose")
                _touch(mroot / "approvals" / s / "alice.approval")
            _touch(base / "src" / p["manifest_set_dir"] / "alice.pub")
            _touch(base / "src" / p["pcr3_preimage_path"])
            _touch(base / "src" / p["member_roster_path"], "{}")
            broot = base / "src" / "outbox" / "req"
            ek.create_bundle(broot, "share-request", cfg_src)

            # Consumer: write a real config.json on disk with null nonces.
            raw_dst = _base_raw()
            for svc in raw_dst["services"]:
                svc["manifest_nonce"] = None
            wd_dst = base / "dst"
            wd_dst.mkdir(parents=True, exist_ok=True)
            cfg_file = wd_dst / "config.json"
            cfg_file.write_text(json.dumps(raw_dst, indent=2), encoding="utf-8")

            # Simulate the `bundle install` process loading config from disk.
            loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
            cfg_dst = ek.Config(loaded, workdir=wd_dst, config_path=cfg_file)
            ek.install_bundle(broot, cfg_dst)

            # Simulate the subsequent, separate `ceremony reencrypt` process:
            # it re-reads config.json fresh from disk. The persisted backfill
            # must be visible there.
            reread = json.loads(cfg_file.read_text(encoding="utf-8"))
            for svc in reread["services"]:
                self.assertEqual(svc["manifest_nonce"], 3, svc["name"])

    def test_backfill_no_config_path_stays_in_memory(self) -> None:
        """Without a config_path (e.g. unit-test Config), the backfill still
        patches the in-memory raw but cannot persist."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            raw_src = _base_raw()
            for svc in raw_src["services"]:
                svc["manifest_nonce"] = 0
            cfg_src = ek.Config(raw_src, workdir=base / "src")
            (base / "src").mkdir(parents=True, exist_ok=True)
            p = cfg_src.paths()
            mroot = base / "src" / p["workdir_manifest_subdir"]
            svcs = [s["name"] for s in cfg_src.all_services()]
            for s in svcs:
                _touch(mroot / f"{s}-manifest-envelope.json")
                _touch(base / "src" / "attestations" / f"{s}.cose")
                _touch(mroot / "approvals" / s / "alice.approval")
            _touch(base / "src" / p["manifest_set_dir"] / "alice.pub")
            _touch(base / "src" / p["pcr3_preimage_path"])
            _touch(base / "src" / p["member_roster_path"], "{}")
            broot = base / "src" / "outbox" / "req"
            ek.create_bundle(broot, "share-request", cfg_src)

            raw_dst = _base_raw()
            for svc in raw_dst["services"]:
                svc["manifest_nonce"] = None
            cfg_dst = ek.Config(raw_dst, workdir=base / "dst")
            (base / "dst").mkdir(parents=True, exist_ok=True)
            ek.install_bundle(broot, cfg_dst)
            for svc in cfg_dst.all_services():
                self.assertEqual(svc["manifest_nonce"], 0, svc["name"])

    def test_no_backfill_when_nonces_already_set(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()

            raw_src = _base_raw()
            for svc in raw_src["services"]:
                svc["manifest_nonce"] = 0
            cfg_src = ek.Config(raw_src, workdir=base / "src")
            (base / "src").mkdir(parents=True, exist_ok=True)
            p = cfg_src.paths()
            mroot = base / "src" / p["workdir_manifest_subdir"]
            svcs = [s["name"] for s in cfg_src.all_services()]
            for s in svcs:
                _touch(mroot / f"{s}-manifest-envelope.json")
                _touch(base / "src" / "attestations" / f"{s}.cose")
                _touch(mroot / "approvals" / s / "alice.approval")
            _touch(base / "src" / p["manifest_set_dir"] / "alice.pub")
            _touch(base / "src" / p["pcr3_preimage_path"])
            _touch(base / "src" / p["member_roster_path"], "{}")
            broot = base / "src" / "outbox" / "req"
            ek.create_bundle(broot, "share-request", cfg_src)

            # Consumer already has nonce=5 set; backfill should NOT overwrite.
            raw_dst = _base_raw()
            for svc in raw_dst["services"]:
                svc["manifest_nonce"] = 5
            cfg_dst = ek.Config(raw_dst, workdir=base / "dst")
            (base / "dst").mkdir(parents=True, exist_ok=True)

            ek.install_bundle(broot, cfg_dst)

            for svc in cfg_dst.all_services():
                self.assertEqual(svc["manifest_nonce"], 5, svc["name"])


class InstallSymlinkedWorkdirTests(unittest.TestCase):
    """Regression: install_bundle must not crash when --workdir is under a
    symlinked path (e.g. macOS /tmp -> /private/tmp). resolve_path() resolves
    symlinks, so the relative_to() display must compare against a resolved
    workdir."""

    def test_install_under_symlinked_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as real_dir:
            real = Path(real_dir).resolve()
            link = real / "link"
            target = real / "target"
            target.mkdir()
            os.symlink(target, link)

            # Producer workdir (use the real path so create_bundle is simple).
            cfg = ek.Config(_base_raw(), workdir=target / "src")
            (target / "src").mkdir(parents=True, exist_ok=True)
            p = cfg.paths()
            _touch(target / "src" / p["workdir_manifest_subdir"] / "approvals" / "signer" / "a.approval")
            for s in (s["name"] for s in cfg.all_services()):
                _touch(target / "src" / p["workdir_manifest_subdir"] / "approvals" / s / "a.approval")
            broot = target / "src" / "outbox" / "appr"
            ek.create_bundle(broot, "approvals", cfg)

            # Consumer workdir addressed through the symlink — must not raise.
            wd_via_link = link / "member"
            wd_via_link.mkdir(parents=True, exist_ok=True)
            cfg2 = ek.Config(_base_raw(), workdir=wd_via_link)
            ek.install_bundle(broot, cfg2)  # would raise ValueError before the fix
            mroot2 = wd_via_link / cfg2.paths()["workdir_manifest_subdir"]
            self.assertTrue((mroot2 / "approvals" / "signer" / "a.approval").is_file())


if __name__ == "__main__":
    unittest.main()
