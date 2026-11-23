"""Tests for `manifest check`, the manifest hash table and `workspace seed`.

The fixtures are synthetic (random-looking hex, no real ceremony material).
They build a Coordinator workdir whose generated manifests are written by the
test itself in qos `generate-manifest` JSON shape, so no qos_client is needed.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from ._helpers import REPO_ROOT, load_enclave_keyops

ek = load_enclave_keyops()
mc = ek.manifest_check

SERVICES = ["signer", "policy-engine", "notarizer", "tls-fetcher", "transaction-parser"]
QK = "04" + "11" * 64 + "04" + "22" * 64
PCR02 = {"pcr0": "aa" * 48, "pcr1": "aa" * 48, "pcr2": "bb" * 48}
ROOT_CERT = "cc" * 20
ARN = "arn:aws:iam::000000000000:role/example-enclave"
NONCES = {
    "signer": 3,
    "policy-engine": 2,
    "notarizer": 2,
    "tls-fetcher": 2,
    "transaction-parser": 1,
}


def _pub(i: int) -> str:
    return "04" + f"{i:02x}" * 64


def _pcr3() -> str:
    return hashlib.sha384(b"\0" * 48 + ARN.encode()).hexdigest()


def _pivot_hash(svc: str) -> str:
    return hashlib.sha256(f"pivot-{svc}".encode()).hexdigest()


def _manifest_json(
    svc: str, nonce: int, args: list, *, members=None, quorum_key=QK, pcr3=None
) -> dict:
    ms_members = (
        members if members is not None else [("manifest-a", _pub(1)), ("manifest-b", _pub(2))]
    )
    return {
        "namespace": {"name": f"example/{svc}", "nonce": nonce, "quorumKey": quorum_key},
        "pivot": {
            "hash": _pivot_hash(svc),
            "restart": "Always",
            "bridgeConfig": [{"type": "server", "port": 8081, "host": "0.0.0.0"}],
            "debugMode": False,
            "args": args,
        },
        "manifestSet": {
            "threshold": 2,
            "members": [{"alias": a, "pubKey": p} for a, p in ms_members],
        },
        "shareSet": {"threshold": 1, "members": [{"alias": "share-a", "pubKey": _pub(3)}]},
        "enclave": {
            **PCR02,
            "pcr3": pcr3 or _pcr3(),
            "awsRootCertificate": ROOT_CERT,
            "qosCommit": "",
        },
        "patchSet": {"threshold": 0, "members": []},
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_workdir(wd: Path, *, nonce_offset: int = 0) -> Path:
    raw = json.loads((REPO_ROOT / "core" / "config.prod.example.json").read_text(encoding="utf-8"))
    raw["qos_client_path"] = "shared/qos_client"
    raw["paths"].update(
        {
            "qos_release_dir": "shared/qos-release",
            "pcr3_preimage_path": "shared/pcr3-preimage.txt",
            "quorum_key_pub_path": "shared/quorum_key.pub",
            "dr_key_pub_path": "shared/dr-key.pub",
            "pivots_dir": "shared/pivots",
            "pivot_hashes_dir": "shared/pivot-hashes",
            "manifest_set_dir": "shared/manifest-set",
            "share_set_dir": "shared/share-set",
            "patch_set_dir": "shared/patch-set",
        }
    )
    raw["defaults"]["pivot_args"] = "[--app-port,8081]"
    raw["defaults"]["tls_fetcher_pivot_args"] = None
    for s in raw["services"]:
        s["manifest_nonce"] = NONCES[s["name"]] + nonce_offset
        s["manifest_namespace"] = f"example/{s['name']}"
    _write(wd / "config.json", json.dumps(raw, indent=2))
    sh = wd / "shared"
    _write(sh / "quorum_key.pub", QK + "\n")
    _write(sh / "pcr3-preimage.txt", ARN + "\n")
    _write(sh / "dr-key.pub", _pub(9) + "\n")
    _write(sh / "manifest-set" / "manifest-a.pub", _pub(1))
    _write(sh / "manifest-set" / "manifest-b.pub", _pub(2))
    _write(sh / "manifest-set" / "quorum_threshold", "2\n")
    _write(sh / "share-set" / "share-a.pub", _pub(3))
    _write(sh / "share-set" / "quorum_threshold", "1\n")
    _write(sh / "patch-set" / "quorum_threshold", "0\n")
    _write(
        sh / "member-roster.json",
        json.dumps(
            {
                "manifest_set": [{"alias": "manifest-a"}, {"alias": "manifest-b"}],
                "share_set": [{"alias": "share-a", "member_index": 1}],
            }
        ),
    )
    _write(
        sh / "qos-release" / "nitro.pcrs", "".join(f"{PCR02[f'pcr{i}']} PCR{i}\n" for i in range(3))
    )
    for svc in SERVICES:
        _write(sh / "pivot-hashes" / f"{svc}-pivot-hash.txt", _pivot_hash(svc) + "\n")
    return wd / "config.json"


def _write_manifests(
    wd: Path,
    *,
    args_override=None,
    nonce_offset: int = 0,
    envelope: bool = False,
    subdir: str = "manifest",
    **kw,
) -> None:
    for svc in SERVICES:
        args = (args_override or {}).get(svc, ["--app-port", "8081"])
        m = _manifest_json(svc, NONCES[svc] + nonce_offset, args, **kw)
        name = f"{svc}-manifest-envelope.json" if envelope else f"{svc}-manifest.json"
        body = (
            {"manifest": m, "manifestSetApprovals": [], "shareSetApprovals": []} if envelope else m
        )
        _write(wd / subdir / name, json.dumps(body))


def _run(wd: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            ek.main(["--config", str(wd / "config.json"), "--workdir", str(wd), *args])
        except SystemExit as e:
            code = int(e.code or 0)
    return code, out.getvalue()


class BorshHashTests(unittest.TestCase):
    def test_member_order_changes_hash(self) -> None:
        a = mc.normalize(_manifest_json("signer", 1, []))
        b = mc.normalize(
            _manifest_json(
                "signer", 1, [], members=[("manifest-b", _pub(2)), ("manifest-a", _pub(1))]
            )
        )
        self.assertNotEqual(mc.manifest_sha256(a), mc.manifest_sha256(b))

    def test_envelope_and_bare_manifest_hash_equal(self) -> None:
        m = _manifest_json("signer", 1, ["--x", "1"])
        env = {"manifest": m, "manifestSetApprovals": [{}], "shareSetApprovals": []}
        self.assertEqual(mc.manifest_sha256(mc.normalize(m)), mc.manifest_sha256(mc.normalize(env)))

    def test_borsh_prefix_layout(self) -> None:
        b = mc.borsh_encode_v1(mc.normalize(_manifest_json("signer", 7, [])))
        name = b"example/signer"
        self.assertEqual(b[:4], len(name).to_bytes(4, "little"))
        self.assertEqual(b[4 : 4 + len(name)], name)
        self.assertEqual(b[4 + len(name) : 8 + len(name)], (7).to_bytes(4, "little"))

    def test_v2_rejected(self) -> None:
        m = _manifest_json("signer", 1, [])
        m["version"] = "v2"
        with self.assertRaises(mc.ManifestFormatError):
            mc.normalize(m)

    def test_parse_pivot_args_and_diff(self) -> None:
        self.assertEqual(mc.parse_pivot_args("[--a,1,--b]"), ["--a", "1", "--b"])
        self.assertEqual(mc.parse_pivot_args("[]"), [])
        diff = mc.diff_args(["--a", "1", "--b", "2"], ["--a", "1", "--b", "3", "--c", "x"])
        self.assertEqual(diff, ["~ --b ['2'] -> ['3']", "+ --c ['x']"])

    def test_pcr3_from_preimage(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pre.txt"
            p.write_text(ARN + "\n", encoding="utf-8")
            self.assertEqual(mc.pcr3_from_preimage(p), _pcr3())


class ManifestCheckCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.wd = Path(self._ctx.name).resolve() / "coordinator"
        _build_workdir(self.wd)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def test_pass_and_writes_report(self) -> None:
        _write_manifests(self.wd)
        code, out = _run(self.wd, "manifest", "check")
        self.assertEqual(code, 0, out)
        self.assertIn("manifest check: PASS", out)
        report = json.loads((self.wd / "manifest" / "manifest-check.json").read_text())
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(set(report["manifest_sha256"]), set(SERVICES))

    def test_tampered_arg_fails(self) -> None:
        _write_manifests(self.wd, args_override={"notarizer": ["--app-port", "9999"]})
        code, out = _run(self.wd, "manifest", "check")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"FAIL\s+notarizer\s+pivot_args")

    def test_wrong_pcr3_fails(self) -> None:
        _write_manifests(self.wd, pcr3="dd" * 48)
        code, out = _run(self.wd, "manifest", "check")
        self.assertEqual(code, 1)
        self.assertIn("pcr3=sha384(preimage)", out)

    def test_unregistered_member_fails(self) -> None:
        _write_manifests(self.wd, members=[("manifest-a", _pub(1)), ("manifest-x", _pub(8))])
        code, out = _run(self.wd, "manifest", "check")
        self.assertEqual(code, 1)
        self.assertIn("manifest_set.members", out)

    def test_forbidden_arg_fails(self) -> None:
        cfg = json.loads((self.wd / "config.json").read_text())
        cfg["services"][0]["pivot_args"] = "[--app-port,8081,--debug-witness,1]"
        (self.wd / "config.json").write_text(json.dumps(cfg))
        _write_manifests(
            self.wd, args_override={"signer": ["--app-port", "8081", "--debug-witness", "1"]}
        )
        code, out = _run(self.wd, "manifest", "check", "--forbid-arg=--debug-witness")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"FAIL\s+signer\s+forbidden --debug-witness")

    def test_expected_hashes_mismatch_fails(self) -> None:
        _write_manifests(self.wd)
        exp = self.wd / "expected.json"
        exp.write_text(json.dumps({"signer": "00" * 32}))
        code, out = _run(self.wd, "manifest", "check", "--expected-hashes", str(exp))
        self.assertEqual(code, 1)
        self.assertIn("manifest_sha256=expected", out)

    def test_live_nonce_delta(self) -> None:
        live = self.wd.parent / "live"
        _write_manifests(live, nonce_offset=-1, envelope=True, subdir="")
        _write_manifests(self.wd)
        code, out = _run(self.wd, "manifest", "check", "--live-dir", str(live))
        self.assertEqual(code, 0, out)
        code, out = _run(
            self.wd, "manifest", "check", "--live-dir", str(live), "--nonce-delta", "2"
        )
        self.assertEqual(code, 1)
        self.assertIn("nonce=live+2", out)

    def test_live_quorum_change_fails_unless_allowed(self) -> None:
        live = self.wd.parent / "live"
        _write_manifests(
            live,
            nonce_offset=-1,
            envelope=True,
            subdir="",
            members=[("manifest-a", _pub(1)), ("manifest-z", _pub(7))],
        )
        _write_manifests(self.wd)
        code, out = _run(self.wd, "manifest", "check", "--live-dir", str(live))
        self.assertEqual(code, 1)
        self.assertRegex(out, r"FAIL\s+\S+\s+live\.manifest_set")
        code, out = _run(
            self.wd,
            "manifest",
            "check",
            "--live-dir",
            str(live),
            "--allow-live-diff",
            "manifest_set",
        )
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"WARN\s+\S+\s+live\.manifest_set")

    def test_per_service_pivot_args_override(self) -> None:
        cfg = ek.Config(json.loads((self.wd / "config.json").read_text()), workdir=self.wd)
        svc = dict(cfg.svc("policy-engine"), pivot_args="[--only,this]")
        self.assertEqual(ek._pivot_args_for_service(cfg, svc), "[--only,this]")

    def test_write_manifest_hashes(self) -> None:
        _write_manifests(self.wd)
        cfg = ek.Config(json.loads((self.wd / "config.json").read_text()), workdir=self.wd)
        with contextlib.redirect_stdout(io.StringIO()):
            hashes = ek.write_manifest_hashes(cfg, None)
        on_disk = json.loads((self.wd / "manifest" / "manifest-hashes.json").read_text())
        self.assertEqual(hashes, on_disk)
        self.assertEqual(len(on_disk), 5)


class WorkspaceSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        base = Path(self._ctx.name).resolve()
        self.prev = base / "prev" / "coordinator"
        _build_workdir(self.prev, nonce_offset=-1)
        _write_manifests(self.prev, nonce_offset=-1, envelope=True)
        self.new = base / "new" / "coordinator"
        self.new.mkdir(parents=True)
        cfg = json.loads((self.prev / "config.json").read_text())
        for s in cfg["services"]:
            s["manifest_nonce"] = None
        (self.new / "config.json").write_text(json.dumps(cfg))

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def test_copies_public_material_and_bumps_nonce(self) -> None:
        _write(self.prev / "shared" / "manifest-set" / "manifest-a.secret", "do-not-copy")
        code, out = _run(self.new, "workspace", "seed", "--from", str(self.prev))
        self.assertEqual(code, 0, out)
        sh = self.new / "shared"
        self.assertTrue((sh / "quorum_key.pub").is_file())
        self.assertTrue((sh / "manifest-set" / "manifest-b.pub").is_file())
        self.assertFalse((sh / "manifest-set" / "manifest-a.secret").exists())
        self.assertFalse((sh / "pivot-hashes").exists())
        self.assertFalse((sh / "qos-release").exists())
        cfg = json.loads((self.new / "config.json").read_text())
        self.assertEqual({s["name"]: s["manifest_nonce"] for s in cfg["services"]}, NONCES)
        prov = json.loads((self.new / "seed-provenance.json").read_text())
        self.assertEqual(prov["source_workdir"], str(self.prev))
        self.assertTrue(all(len(f["sha256"]) == 64 for f in prov["files"]))

    def test_dry_run_writes_nothing(self) -> None:
        code, out = _run(self.new, "--dry-run", "workspace", "seed", "--from", str(self.prev))
        self.assertEqual(code, 0, out)
        self.assertFalse((self.new / "shared").exists())
        self.assertFalse((self.new / "seed-provenance.json").exists())

    def test_refuses_symlink_source(self) -> None:
        qk = self.prev / "shared" / "quorum_key.pub"
        real = self.prev.parent / "elsewhere.pub"
        real.write_text(QK)
        qk.unlink()
        os.symlink(real, qk)
        code, out = _run(self.new, "workspace", "seed", "--from", str(self.prev))
        self.assertEqual(code, 2)
        self.assertIn("refusing symlink", out)

    def test_refuses_occupied_destination(self) -> None:
        _write(self.new / "shared" / "quorum_key.pub", "04" + "99" * 129)
        code, out = _run(self.new, "workspace", "seed", "--from", str(self.prev))
        self.assertEqual(code, 2)
        self.assertIn("occupied", out)

    def test_identical_destination_is_fine(self) -> None:
        _write(self.new / "shared" / "quorum_key.pub", QK + "\n")
        code, out = _run(self.new, "workspace", "seed", "--from", str(self.prev))
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
