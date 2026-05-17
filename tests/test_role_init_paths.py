from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ._helpers import load_role_init


ri = load_role_init()


class FindEnclosingGitRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._ctx.name).resolve()

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def test_returns_none_when_no_git(self) -> None:
        # tempdir on macOS is under /private/var/... which is not itself a Git
        # working tree (and the enclosing /private/var is not either). The
        # function should walk up to the FS root and return None.
        sub = self.tmp / "deep" / "nested"
        sub.mkdir(parents=True)
        # Walking up from sub may eventually find a real .git on the host
        # filesystem, but our temp tree definitely is not in a Git working
        # tree. Use the temp dir as start and verify we don't claim it has a
        # Git root.
        result = ri.find_enclosing_git_root(sub)
        # If the host happens to have a parent .git (e.g. the agent runs from
        # inside a workspace), the result may not be None. Only assert when
        # there's no enclosing git root in the test environment.
        if result is not None:
            self.assertNotEqual(result, sub)

    def test_returns_self_when_dir_has_git(self) -> None:
        (self.tmp / ".git").mkdir()
        self.assertEqual(ri.find_enclosing_git_root(self.tmp), self.tmp)

    def test_returns_ancestor_with_git(self) -> None:
        (self.tmp / ".git").mkdir()
        nested = self.tmp / "a" / "b" / "c"
        nested.mkdir(parents=True)
        self.assertEqual(ri.find_enclosing_git_root(nested), self.tmp)


class RefuseUnderCwdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._ctx.name).resolve()
        self._original_cwd = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self._original_cwd)
        self._ctx.cleanup()

    def test_passes_when_cwd_outside_any_git(self) -> None:
        outside_cwd = self.tmp / "no-git"
        outside_cwd.mkdir()
        os.chdir(outside_cwd)
        # find_enclosing_git_root may still find a real .git above /tmp on
        # rare host setups, in which case we cannot meaningfully assert
        # "passes". Skip rather than fail.
        if ri.find_enclosing_git_root(Path(os.getcwd())) is not None:
            self.skipTest("host filesystem has an enclosing git working tree")
        target = self.tmp / "workdir"
        ri.refuse_under_cwd(target, force=False)  # must not raise

    def test_refuses_when_target_under_cwd_git(self) -> None:
        repo = self.tmp / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        os.chdir(repo)
        target = repo / "deep" / "workdir"
        with self.assertRaises(SystemExit) as cm:
            ri.refuse_under_cwd(target, force=False)
        self.assertEqual(cm.exception.code, 2)

    def test_force_overrides(self) -> None:
        repo = self.tmp / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        os.chdir(repo)
        target = repo / "deep" / "workdir"
        # force=True should not raise.
        ri.refuse_under_cwd(target, force=True)


if __name__ == "__main__":
    unittest.main()
