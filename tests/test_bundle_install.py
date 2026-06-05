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


def _set_nonce0(cfg) -> None:
    """share-set approval matching needs a non-null manifest_nonce; the example
    config ships null on purpose, so tests that exercise the wrapped-shares
    invariant pin nonce=0 (mutates the shared service dicts in cfg.raw)."""
    for svc in cfg.all_services():
        svc["manifest_nonce"] = 0


def _approval_name(svc_name: str, nonce: int = 0) -> str:
    """qos_client approval filename: {alias}-{namespace}-{nonce}.approval, with
    namespace slashes replaced by dashes. Example config uses 0xkey/<svc>."""
    return f"share-alice-0xkey-{svc_name}-{nonce}.approval"


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

    def test_share_request_scoped_to_single_service(self) -> None:
        cfg, wd = self._cfg("src_req_scoped")
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

        broot = wd / "outbox" / "req-signer"
        ek.create_bundle(broot, "share-request", cfg, services=["signer"])

        # only the scoped service is packaged
        self.assertTrue((broot / "signer-manifest-envelope.json").is_file())
        for s in svcs:
            if s == "signer":
                continue
            self.assertFalse(
                (broot / f"{s}-manifest-envelope.json").is_file(),
                f"{s} must NOT be in a signer-scoped bundle",
            )
        # BUNDLE.json records only the scoped subset so reencrypt/install agree
        meta = json.loads((broot / "BUNDLE.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["services"], ["signer"])
        self.assertEqual(list(meta["manifest_namespaces"]), ["signer"])

        # install on the member side only distributes the scoped service
        cfg2, wd2 = self._cfg("member_req_scoped")
        ek.install_bundle(broot, cfg2)
        mroot2 = wd2 / cfg2.paths()["workdir_manifest_subdir"]
        self.assertTrue((mroot2 / "signer-manifest-envelope.json").is_file())
        self.assertFalse((mroot2 / "notarizer-manifest-envelope.json").exists())

    def test_share_request_unknown_service_errors(self) -> None:
        cfg, wd = self._cfg("src_req_badsvc")
        broot = wd / "outbox" / "req-bad"
        with self.assertRaises(SystemExit):
            ek.create_bundle(broot, "share-request", cfg, services=["nope"])

    def test_service_selector_rejected_for_non_share_request(self) -> None:
        cfg, wd = self._cfg("src_req_wrongkind")
        broot = wd / "outbox" / "req-wrongkind"
        with self.assertRaises(SystemExit):
            ek.create_bundle(broot, "review", cfg, services=["signer"])

    # ----- wrapped-shares ----------------------------------------------------
    def test_wrapped_shares_round_trip_matches_post_reads(self) -> None:
        cfg, wd = self._cfg("src_wrap")
        _set_nonce0(cfg)
        svcs = self._services(cfg)
        for s in svcs:
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            _touch(wd / "share-set-approvals" / s / _approval_name(s))

        broot = wd / "outbox" / "wrap"
        ek.create_bundle(broot, "wrapped-shares", cfg)

        cfg2, wd2 = self._cfg("coord_wrap")
        _set_nonce0(cfg2)
        ek.install_bundle(broot, cfg2)

        # ceremony post default --wrapped-in-dir is "wrapped-shares-coordinator".
        for s in svcs:
            self.assertTrue(
                (wd2 / "wrapped-shares-coordinator" / s / "member1_eph_wrapped.share").is_file(),
                f"wrapped share for {s} must land in wrapped-shares-coordinator",
            )

    def test_wrapped_shares_includes_share_set_approvals(self) -> None:
        """share-set-approvals/ should be packaged into the wrapped-shares
        bundle and installed into the Coordinator's manifest/approvals/."""
        cfg, wd = self._cfg("src_wrap_appr")
        _set_nonce0(cfg)
        svcs = self._services(cfg)
        for s in svcs:
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            _touch(wd / "share-set-approvals" / s / _approval_name(s))

        broot = wd / "outbox" / "wrap_appr"
        ek.create_bundle(broot, "wrapped-shares", cfg)

        # Verify bundle contains approvals/ alongside wrapped-shares/
        for s in svcs:
            self.assertTrue(
                (broot / "approvals" / s / _approval_name(s)).is_file(),
                f"bundle must contain share-set approval for {s}",
            )

        # BUNDLE.json must carry the share_set_approvals manifest.
        meta = json.loads((broot / "BUNDLE.json").read_text(encoding="utf-8"))
        self.assertIn("share_set_approvals", meta)
        for s in svcs:
            self.assertEqual(meta["share_set_approvals"][s], [_approval_name(s)])

        cfg2, wd2 = self._cfg("coord_wrap_appr")
        _set_nonce0(cfg2)
        ek.install_bundle(broot, cfg2)
        mroot2 = wd2 / cfg2.paths()["workdir_manifest_subdir"]
        for s in svcs:
            self.assertTrue(
                (wd2 / "wrapped-shares-coordinator" / s / "member1_eph_wrapped.share").is_file(),
                f"wrapped share for {s} must land in wrapped-shares-coordinator",
            )
            self.assertTrue(
                (mroot2 / "approvals" / s / _approval_name(s)).is_file(),
                f"share-set approval for {s} must land in Coordinator approvals",
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


class BundleCreateArchiveTests(unittest.TestCase):
    """`bundle create --archive` without --bundle-dir must stage into a temp dir
    that is removed afterwards, so repeated runs never collide on an existing
    --bundle-dir (Coordinator "bundle dir already exists" friction)."""

    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.base = Path(self._ctx.name).resolve()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _cfg(self, name: str):
        wd = self.base / name
        wd.mkdir(parents=True, exist_ok=True)
        return ek.Config(_base_raw(), workdir=wd), wd

    def _ns(self, **kw):
        defaults = dict(kind="wrapped-shares", bundle_dir=None, archive=None, dry_run=False)
        defaults.update(kw)
        return ek.argparse.Namespace(**defaults)

    def _seed_wrapped(self, cfg, wd) -> None:
        _set_nonce0(cfg)
        for s in (s["name"] for s in cfg.all_services()):
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            _touch(wd / "share-set-approvals" / s / _approval_name(s))

    def test_archive_only_packs_and_cleans_temp_dir(self) -> None:
        cfg, wd = self._cfg("src_archive_only")
        self._seed_wrapped(cfg, wd)
        before = set(wd.iterdir())

        ek.cmd_bundle_create(self._ns(archive="out/wrap.tgz"), cfg, None)

        archive = wd / "out" / "wrap.tgz"
        self.assertTrue(archive.is_file(), "archive must be produced")
        # No leftover keyops-bundle-* staging dir in the workdir.
        leftover = [p for p in wd.iterdir() if p.name.startswith("keyops-bundle-")]
        self.assertEqual(leftover, [], "temp staging dir must be removed")
        added = set(wd.iterdir()) - before
        self.assertEqual({p.name for p in added}, {"out"}, "only the archive dir is new")

    def test_archive_only_is_idempotent_across_runs(self) -> None:
        cfg, wd = self._cfg("src_archive_rerun")
        self._seed_wrapped(cfg, wd)
        ns = self._ns(archive="out/wrap.tgz")
        ek.cmd_bundle_create(ns, cfg, None)
        # Second run must not raise "bundle dir already exists".
        ek.cmd_bundle_create(ns, cfg, None)
        self.assertTrue((wd / "out" / "wrap.tgz").is_file())

    def test_archive_only_archive_extracts_to_valid_bundle(self) -> None:
        cfg, wd = self._cfg("src_archive_extract")
        self._seed_wrapped(cfg, wd)
        ek.cmd_bundle_create(self._ns(archive="out/wrap.tgz"), cfg, None)

        cfg2, wd2 = self._cfg("coord_archive_extract")
        ns_x = ek.argparse.Namespace(
            archive=str(wd / "out" / "wrap.tgz"),
            bundle_dir="incoming/wrap",
            install=False,
            dry_run=False,
        )
        ek.cmd_bundle_extract(ns_x, cfg2, None)
        root = ek._find_bundle_root(wd2 / "incoming" / "wrap")
        self.assertTrue((root / "SHA256SUMS").is_file())
        for s in (s["name"] for s in cfg2.all_services()):
            self.assertTrue(
                (root / "wrapped-shares" / s / "member1_eph_wrapped.share").is_file()
            )

    def test_no_bundle_dir_and_no_archive_errors(self) -> None:
        cfg, wd = self._cfg("src_archive_none")
        with self.assertRaises(SystemExit):
            ek.cmd_bundle_create(self._ns(), cfg, None)

    def test_extract_no_bundle_dir_no_install_errors(self) -> None:
        cfg, wd = self._cfg("src_extract_guard")
        self._seed_wrapped(cfg, wd)
        ek.cmd_bundle_create(self._ns(archive="out/wrap.tgz"), cfg, None)
        cfg2, wd2 = self._cfg("coord_extract_guard")
        ns_x = ek.argparse.Namespace(
            archive=str(wd / "out" / "wrap.tgz"),
            bundle_dir=None,
            install=False,
            dry_run=False,
        )
        with self.assertRaises(SystemExit):
            ek.cmd_bundle_extract(ns_x, cfg2, None)

    def test_extract_install_without_bundle_dir_cleans_temp(self) -> None:
        cfg, wd = self._cfg("src_extract_install")
        self._seed_wrapped(cfg, wd)
        ek.cmd_bundle_create(self._ns(archive="out/wrap.tgz"), cfg, None)

        cfg2, wd2 = self._cfg("coord_extract_install")
        before = set(wd2.iterdir())
        ns_x = ek.argparse.Namespace(
            archive=str(wd / "out" / "wrap.tgz"),
            bundle_dir=None,
            install=True,
            dry_run=False,
        )
        ek.cmd_bundle_extract(ns_x, cfg2, None)
        # the throwaway staging dir must not survive
        leftover = [p for p in wd2.iterdir() if p.name.startswith("keyops-extract-")]
        self.assertEqual(leftover, [], "temp extract dir must be removed")
        # install actually distributed the wrapped shares
        for s in (s["name"] for s in cfg2.all_services()):
            self.assertTrue(
                (wd2 / "wrapped-shares-coordinator" / s / "member1_eph_wrapped.share").is_file()
            )
        self.assertNotIn("incoming", {p.name for p in set(wd2.iterdir()) - before})


class WrappedSharesApprovalInvariantTests(unittest.TestCase):
    """The wrapped-shares bundle must never silently ship without the share-set
    approval `ceremony post` needs. Enforced on producer (create_bundle) and
    consumer (install_bundle) sides."""

    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.base = Path(self._ctx.name).resolve()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _cfg(self, name: str, raw: dict | None = None):
        wd = self.base / name
        wd.mkdir(parents=True, exist_ok=True)
        return ek.Config(raw or _base_raw(), workdir=wd), wd

    def _services(self, cfg) -> list[str]:
        return [s["name"] for s in cfg.all_services()]

    # ----- producer side (create_bundle) ------------------------------------
    def test_missing_all_approvals_errors(self) -> None:
        cfg, wd = self._cfg("no_appr")
        _set_nonce0(cfg)
        for s in self._services(cfg):
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
        # No share-set-approvals/ at all.
        with self.assertRaises(SystemExit):
            ek.create_bundle(wd / "outbox" / "b", "wrapped-shares", cfg)

    def test_one_service_missing_approval_errors(self) -> None:
        cfg, wd = self._cfg("one_missing")
        _set_nonce0(cfg)
        svcs = self._services(cfg)
        for s in svcs:
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            _touch(wd / "share-set-approvals" / s / _approval_name(s))
        # Drop exactly one service's approval.
        (wd / "share-set-approvals" / svcs[2] / _approval_name(svcs[2])).unlink()
        with self.assertRaises(SystemExit):
            ek.create_bundle(wd / "outbox" / "b", "wrapped-shares", cfg)

    def test_nonce_mismatch_approval_errors(self) -> None:
        """A stale approval from a previous round (wrong nonce) must not satisfy
        the invariant."""
        cfg, wd = self._cfg("stale_nonce")
        _set_nonce0(cfg)
        for s in self._services(cfg):
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            # nonce 0 expected, seed nonce 9.
            _touch(wd / "share-set-approvals" / s / _approval_name(s, nonce=9))
        with self.assertRaises(SystemExit):
            ek.create_bundle(wd / "outbox" / "b", "wrapped-shares", cfg)

    def test_no_wrapped_shares_errors(self) -> None:
        cfg, wd = self._cfg("no_wrapped")
        _set_nonce0(cfg)
        (wd / "wrapped-shares-out").mkdir(parents=True, exist_ok=True)
        with self.assertRaises(SystemExit):
            ek.create_bundle(wd / "outbox" / "b", "wrapped-shares", cfg)

    # ----- B: path drift via config single-source-of-truth ------------------
    def test_custom_approvals_dir_from_config(self) -> None:
        """create_bundle must read paths.share_set_approvals_dir /
        wrapped_shares_out_dir so it stays aligned with reencrypt."""
        raw = _base_raw()
        raw["paths"]["wrapped_shares_out_dir"] = "custom-wrapped"
        raw["paths"]["share_set_approvals_dir"] = "custom-approvals"
        cfg, wd = self._cfg("custom_dirs", raw)
        _set_nonce0(cfg)
        svcs = self._services(cfg)
        for s in svcs:
            _touch(wd / "custom-wrapped" / s / "member1_eph_wrapped.share")
            _touch(wd / "custom-approvals" / s / _approval_name(s))
        broot = wd / "outbox" / "b"
        ek.create_bundle(broot, "wrapped-shares", cfg)
        for s in svcs:
            self.assertTrue((broot / "approvals" / s / _approval_name(s)).is_file())

    def test_drift_when_only_reencrypt_dir_changes(self) -> None:
        """If approvals were written to a non-default dir but config still points
        at the default, create_bundle must fail loudly (not silently omit)."""
        cfg, wd = self._cfg("drift")
        _set_nonce0(cfg)
        for s in self._services(cfg):
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            # Approvals landed somewhere config does not know about.
            _touch(wd / "elsewhere" / s / _approval_name(s))
        with self.assertRaises(SystemExit):
            ek.create_bundle(wd / "outbox" / "b", "wrapped-shares", cfg)

    # ----- consumer side (install_bundle) -----------------------------------
    def test_install_rejects_bundle_missing_approval(self) -> None:
        """A wrapped-shares bundle hand-trimmed to drop an approval must be
        rejected at install time (defense in depth)."""
        cfg, wd = self._cfg("src_trim")
        _set_nonce0(cfg)
        svcs = self._services(cfg)
        for s in svcs:
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            _touch(wd / "share-set-approvals" / s / _approval_name(s))
        broot = wd / "outbox" / "b"
        ek.create_bundle(broot, "wrapped-shares", cfg)

        # Trim one service's approval out of the built bundle.
        victim = svcs[1]
        (broot / "approvals" / victim / _approval_name(victim)).unlink()

        cfg2, wd2 = self._cfg("coord_trim")
        _set_nonce0(cfg2)
        with self.assertRaises(SystemExit):
            ek.install_bundle(broot, cfg2)

    def test_install_rejects_manifest_mismatch(self) -> None:
        """If on-disk approvals diverge from BUNDLE.json.share_set_approvals the
        install must fail (corrupt/tampered bundle)."""
        cfg, wd = self._cfg("src_tamper")
        _set_nonce0(cfg)
        svcs = self._services(cfg)
        for s in svcs:
            _touch(wd / "wrapped-shares-out" / s / "member1_eph_wrapped.share")
            _touch(wd / "share-set-approvals" / s / _approval_name(s))
        broot = wd / "outbox" / "b"
        ek.create_bundle(broot, "wrapped-shares", cfg)

        # Add an extra approval file not recorded in the manifest.
        extra = svcs[0]
        _touch(broot / "approvals" / extra / _approval_name(extra, nonce=0).replace("alice", "mallory"))

        cfg2, wd2 = self._cfg("coord_tamper")
        _set_nonce0(cfg2)
        with self.assertRaises(SystemExit):
            ek.install_bundle(broot, cfg2)


if __name__ == "__main__":
    unittest.main()
