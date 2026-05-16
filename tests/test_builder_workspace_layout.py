"""Builder workspace must use the `out/` producer layout and must NOT inherit
the Coordinator-style `shared/<set>/` consumer layout. This invariant came
out of the multi-agent test run that surfaced a documentation / scaffolding
mismatch where `builder.md` promised `out/` but `role_init.py` only created
`shared/`.

We also check that other roles still get `shared/` and never get `out/`,
so a stale Builder workspace cannot be mistaken for a Coordinator one by
the state-detection rules.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ._helpers import load_role_init


ri = load_role_init()


def _init(role: str, root: Path, **extra) -> None:
    kwargs = {
        "role": role,
        "account_id": None,
        "enclave_role_name": None,
        "force": False,
    }
    kwargs.update(extra)
    ri.init_common(root, **kwargs)


class BuilderUsesOutLayout(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._ctx.name).resolve()
        self.root = self.tmp / "builder"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def test_builder_creates_out_skeleton(self) -> None:
        _init("builder", self.root)
        for sub in ("out", "out/pivots", "out/pivot-hashes", "out/qos-release"):
            with self.subTest(sub=sub):
                self.assertTrue(
                    (self.root / sub).is_dir(),
                    f"builder workspace missing {sub}",
                )

    def test_builder_creates_metadata_and_logs(self) -> None:
        _init("builder", self.root)
        for sub in ("metadata", "logs"):
            with self.subTest(sub=sub):
                self.assertTrue(
                    (self.root / sub).is_dir(),
                    f"builder workspace missing {sub}",
                )

    def test_builder_does_not_create_coordinator_shared_set_dirs(self) -> None:
        """Builder is a producer, not a consumer. It must not pretend to own
        member-set directories that only Coordinator collects into.
        """
        _init("builder", self.root)
        for sub in (
            "shared",
            "shared/manifest-set",
            "shared/share-set",
            "shared/patch-set",
            "shared/pivots",
            "shared/pivot-hashes",
            "shared/qos-release",
        ):
            with self.subTest(sub=sub):
                self.assertFalse(
                    (self.root / sub).exists(),
                    f"builder workspace must not contain {sub} (Coordinator concern)",
                )

    def test_builder_does_not_create_quorum_threshold_examples(self) -> None:
        """quorum_threshold is a Coordinator / member concern; Builder must not
        ship even an `.example` file for those (it would imply Builder owns
        the set, which it doesn't)."""
        _init("builder", self.root)
        for set_dir in ("manifest-set", "share-set", "patch-set"):
            ex = self.root / "shared" / set_dir / "quorum_threshold.example"
            with self.subTest(set_dir=set_dir):
                self.assertFalse(
                    ex.exists(),
                    f"builder workspace must not contain {ex.relative_to(self.root)}",
                )


class NonBuilderRolesUseSharedLayout(unittest.TestCase):
    """Coordinator / Manifest / Share are consumers. They must keep using the
    `shared/` layout and must NOT grow an `out/` skeleton (that would let a
    stale workspace look like a Builder workspace)."""

    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._ctx.name).resolve()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _check_no_out_dir(self, role: str) -> None:
        root = self.tmp / role.replace("-", "_")
        root.mkdir()
        _init(role, root)
        self.assertFalse(
            (root / "out").exists(),
            f"role {role!r} must not create an out/ directory (Builder-only)",
        )

    def test_coordinator_no_out(self) -> None:
        self._check_no_out_dir("coordinator")

    def test_manifest_no_out(self) -> None:
        self._check_no_out_dir("manifest-set-member")

    def test_share_no_out(self) -> None:
        self._check_no_out_dir("share-set-member")

    def test_consumer_roles_create_shared_set_dirs(self) -> None:
        for role in ("coordinator", "manifest-set-member", "share-set-member"):
            root = self.tmp / role
            root.mkdir()
            _init(
                role,
                root,
                account_id="111122223333" if role == "coordinator" else None,
                enclave_role_name="some-role" if role == "coordinator" else None,
            )
            for sub in ("shared/manifest-set", "shared/share-set", "shared/patch-set"):
                with self.subTest(role=role, sub=sub):
                    self.assertTrue(
                        (root / sub).is_dir(),
                        f"role {role!r} missing {sub}",
                    )


class BuilderConfigPointsAtOut(unittest.TestCase):
    """`config.json.paths.*` and `config.json.qos_client_path` must point at
    `out/...` for Builder, and at `shared/...` for everyone else. This is
    what makes `doctor holder` check the right file from each role's POV.
    """

    def test_builder_paths_use_out(self) -> None:
        cfg = ri.configure_json(
            role="builder",
            alias="builder",
            member_index=None,
            account_id=None,
            region=None,
            cluster=None,
            kubectl_context_alias=None,
            enclave_role_name=None,
            qos_client_sha256=None,
            kustomize_overlay_path=None,
        )
        self.assertEqual(cfg["qos_client_path"], "out/qos_client")
        paths = cfg["paths"]
        for key in (
            "qos_release_dir",
            "pcr3_preimage_path",
            "quorum_key_pub_path",
            "dr_key_pub_path",
            "pivots_dir",
            "pivot_hashes_dir",
        ):
            with self.subTest(key=key):
                self.assertTrue(
                    paths[key].startswith("out/"),
                    f"builder paths.{key} must start with out/, got {paths[key]!r}",
                )

    def test_consumer_paths_use_shared(self) -> None:
        for role in ("coordinator", "manifest-set-member", "share-set-member"):
            cfg = ri.configure_json(
                role=role,
                alias="alice" if role != "coordinator" else "coordinator",
                member_index=1 if role == "share-set-member" else None,
                account_id="111122223333" if role == "coordinator" else None,
                region="ap-southeast-1" if role == "coordinator" else None,
                cluster="0xkey-test" if role == "coordinator" else None,
                kubectl_context_alias=None,
                enclave_role_name="some-role" if role == "coordinator" else None,
                qos_client_sha256=None,
                kustomize_overlay_path="/abs/path" if role == "coordinator" else None,
            )
            with self.subTest(role=role, key="qos_client_path"):
                self.assertEqual(cfg["qos_client_path"], "shared/qos_client")
            paths = cfg["paths"]
            for key in (
                "qos_release_dir",
                "pcr3_preimage_path",
                "quorum_key_pub_path",
                "dr_key_pub_path",
                "pivots_dir",
                "pivot_hashes_dir",
            ):
                with self.subTest(role=role, key=key):
                    self.assertTrue(
                        paths[key].startswith("shared/"),
                        f"{role} paths.{key} must start with shared/, got {paths[key]!r}",
                    )


class RoleInitStdoutHints(unittest.TestCase):
    """`role_init.py` must surface follow-up gates so the agent does not
    silently leave a workspace half-configured."""

    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._ctx.name).resolve()
        self._original_cwd = os.getcwd()
        # Run from a non-git location so refuse_under_cwd() does not trip.
        os.chdir(self.tmp)

    def tearDown(self) -> None:
        os.chdir(self._original_cwd)
        self._ctx.cleanup()

    def _run_main(self, *argv: str) -> str:
        from io import StringIO
        from contextlib import redirect_stdout

        old_argv = list(__import__("sys").argv)
        try:
            __import__("sys").argv = ["role_init.py", *argv]
            buf = StringIO()
            with redirect_stdout(buf):
                ri.main()
            return buf.getvalue()
        finally:
            __import__("sys").argv = old_argv

    def test_qos_client_null_emits_todo(self) -> None:
        out = self._run_main(
            "--role", "manifest-set-member",
            "--root", str(self.tmp / "manifester1"),
            "--alias", "manifester1",
        )
        self.assertIn("qos_client_sha256_expected", out)
        self.assertRegex(
            out,
            r"todo \d+:.*qos_client_sha256_expected",
        )

    def test_builder_emits_build_config_todo(self) -> None:
        out = self._run_main(
            "--role", "builder",
            "--root", str(self.tmp / "builder"),
        )
        self.assertRegex(
            out,
            r"todo \d+:.*metadata/build-config\.json",
        )

    def test_coordinator_emits_dr_and_roster_todos(self) -> None:
        out = self._run_main(
            "--role", "coordinator",
            "--root", str(self.tmp / "coordinator"),
            "--account-id", "111122223333",
            "--region", "ap-southeast-1",
            "--cluster", "0xkey-test",
            "--enclave-role-name", "0xkey-test-enclave-node-role",
            "--kustomize-overlay-path", "/abs/path/overlays/prod",
        )
        self.assertIn("dr-key.pub", out)
        self.assertIn("member-roster.json", out)


if __name__ == "__main__":
    unittest.main()
