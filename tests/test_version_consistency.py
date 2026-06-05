r"""Pin the SemVer string lock-step across `VERSION` and every SKILL.md.

Why this test exists
--------------------

The repo carries one version string in three places that MUST agree:

1.  `VERSION` at the repo root — single source of truth, keep-a-changelog
    style, what `gh release create vX.Y.Z` consumes.
2.  YAML frontmatter `version: X.Y.Z` in each per-role
    `skills/0xkey-keyops-<role>/SKILL.md` — what the agent reads and
    what `npx skills list` surfaces to operators.
3.  Inline mention `version \`X.Y.Z\`` inside each SKILL.md's `## Version
    & update` section — what a human skim-reads.

Without this test it is too easy to bump `VERSION`, forget to re-run
`tools/sync-skills.py`, and ship a release where the published
SKILL.md still claims the old version. A drift here is a *correctness*
bug for `release-notes.md` migrations: if the agent reads `0.2.0` but
the operator already pulled `0.3.0`, the agent will skip the BREAKING
note that shipped between them.

The test does not validate format, only equality. Format checks (single
SemVer token) live in `tools/sync-skills.py::_read_version`.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO_ROOT / "VERSION"
SKILL_DIRS = sorted((REPO_ROOT / "skills").glob("0xkey-keyops-*"))

# Pinned-version patterns in the SKILL.md body that sync-skills.py manages.
# Each regex has one capture group for the version token.
_PINNED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "pinned download URL (releases/download/v<version>/)",
        re.compile(r"releases/download/v([\d]+\.[\d]+\.[\d]+)/"),
    ),
    (
        "keyops require-version <version>",
        re.compile(r"keyops require-version ([\d]+\.[\d]+\.[\d]+)"),
    ),
    (
        "keyops fetch-keyops --release-tag v<version>",
        re.compile(r"keyops fetch-keyops --release-tag v([\d]+\.[\d]+\.[\d]+)"),
    ),
]


def _read_repo_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _frontmatter_version(skill_md: Path) -> str | None:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    fm = text[4:end + 1]
    for line in fm.splitlines():
        # Top-level only; sub-fields are always indented in our SKILL.md.
        if line.startswith("version:") or line.startswith("version :"):
            _, _, value = line.partition(":")
            return value.strip()
    return None


_INLINE_RE = re.compile(r"This skill is version `([^`]+)`")


def _inline_skill_md_version(skill_md: Path) -> str | None:
    text = skill_md.read_text(encoding="utf-8")
    m = _INLINE_RE.search(text)
    return m.group(1) if m else None


class VersionConsistency(unittest.TestCase):
    def test_repo_version_file_exists_and_is_a_single_semver_token(self) -> None:
        self.assertTrue(VERSION_FILE.is_file(), "VERSION file missing at repo root")
        v = _read_repo_version()
        self.assertTrue(v, "VERSION file is empty")
        self.assertNotIn("\n", v, "VERSION must be a single line")
        self.assertNotIn(" ", v, "VERSION must be a single SemVer token")
        # Loose SemVer: MAJOR.MINOR.PATCH with optional pre-release suffix.
        self.assertRegex(
            v,
            r"^\d+\.\d+\.\d+(?:[-+].+)?$",
            f"VERSION must be SemVer-shaped, got {v!r}",
        )

    def test_skills_are_present(self) -> None:
        # If sync-skills regenerated layout was somehow nuked we want a
        # clear failure rather than a vacuously-passing for-loop below.
        self.assertTrue(SKILL_DIRS, "no skills/0xkey-keyops-* directories found")

    def test_each_skill_md_frontmatter_matches_repo_version(self) -> None:
        repo_version = _read_repo_version()
        for skill_dir in SKILL_DIRS:
            skill_md = skill_dir / "SKILL.md"
            with self.subTest(skill=skill_dir.name):
                self.assertTrue(skill_md.is_file(), f"missing {skill_md}")
                got = _frontmatter_version(skill_md)
                self.assertEqual(
                    got,
                    repo_version,
                    f"{skill_md.relative_to(REPO_ROOT)} frontmatter version "
                    f"({got!r}) != VERSION ({repo_version!r}); "
                    "did you forget to run tools/sync-skills.py?",
                )

    def test_each_skill_md_inline_version_matches_repo_version(self) -> None:
        repo_version = _read_repo_version()
        for skill_dir in SKILL_DIRS:
            skill_md = skill_dir / "SKILL.md"
            with self.subTest(skill=skill_dir.name):
                got = _inline_skill_md_version(skill_md)
                self.assertEqual(
                    got,
                    repo_version,
                    f"{skill_md.relative_to(REPO_ROOT)} inline ## Version & "
                    f"update mention ({got!r}) != VERSION ({repo_version!r})",
                )

    def test_each_skill_md_pinned_version_literals_match_repo_version(
        self,
    ) -> None:
        """All version-stamped literals in SKILL.md body must equal VERSION.

        sync-skills.py keeps these in sync on each release; this test is the
        CI guard that catches a stale edit or a missed sync run.
        """
        repo_version = _read_repo_version()
        for skill_dir in SKILL_DIRS:
            skill_md = skill_dir / "SKILL.md"
            text = skill_md.read_text(encoding="utf-8")
            for label, pattern in _PINNED_PATTERNS:
                with self.subTest(skill=skill_dir.name, pattern=label):
                    matches = pattern.findall(text)
                    self.assertTrue(
                        matches,
                        f"{skill_md.relative_to(REPO_ROOT)}: missing {label!r}; "
                        "add the pinned command and re-run tools/sync-skills.py",
                    )
                    for found_version in matches:
                        self.assertEqual(
                            found_version,
                            repo_version,
                            f"{skill_md.relative_to(REPO_ROOT)}: {label!r} "
                            f"has version {found_version!r} != VERSION "
                            f"({repo_version!r}); run tools/sync-skills.py",
                        )

    def test_no_skill_md_uses_releases_latest_download(self) -> None:
        """Pinned download URLs must replace the old `releases/latest/download/` form."""
        for skill_dir in SKILL_DIRS:
            skill_md = skill_dir / "SKILL.md"
            with self.subTest(skill=skill_dir.name):
                text = skill_md.read_text(encoding="utf-8")
                self.assertNotIn(
                    "releases/latest/download/",
                    text,
                    f"{skill_md.relative_to(REPO_ROOT)} still uses "
                    "releases/latest/download/ — replace with pinned "
                    "releases/download/v<version>/ and run sync-skills.py",
                )


if __name__ == "__main__":
    unittest.main()
