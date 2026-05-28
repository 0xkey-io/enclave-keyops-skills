"""Tests for the multi-skill layout produced by the `skill-split` change.

These tests are static analyses; they do not invoke any tool, they just read
files. The aim is to keep four invariants stable as the skill evolves:

1.  Each role skill ships a valid `SKILL.md` (name length, sanitized slash
    command uniqueness, non-empty description, description size cap,
    third-person voice).
2.  Description trigger keyword overlap between role skills stays low
    (defense in depth against recall pollution; see `phase0-report.md`).
3.  Each role skill's action whitelist references only `enclave_keyops.py`
    subcommands that actually exist in `dist/src/enclave_keyops.py`.
4.  Markdown links inside each role skill's `SKILL.md` resolve to files that
    really exist next to that skill (or under `core/` for the cross-cut
    references that synthesize the skill body).

`tools/sync-skills.py --check` is verified separately by `test_sync_check`.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Iterable

from ._helpers import REPO_ROOT


CORE = REPO_ROOT / "core"
SKILLS_DIR = REPO_ROOT / "skills"
ENCLAVE_KEYOPS_PY = REPO_ROOT / "dist" / "src" / "enclave_keyops.py"

ROLE_SKILLS = (
    "0xkey-keyops-coordinator",
    "0xkey-keyops-manifest",
    "0xkey-keyops-share",
    "0xkey-keyops-builder",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_frontmatter(skill_md: Path) -> tuple[dict[str, str], str]:
    """Return (frontmatter mapping, body text). Folded YAML scalars (`>-`) are
    re-joined into single-line strings. We deliberately keep this parser tiny
    rather than pulling in PyYAML to stay dependency-free."""
    text = _read(skill_md)
    if not text.startswith("---\n"):
        raise AssertionError(f"{skill_md}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise AssertionError(f"{skill_md}: unterminated YAML frontmatter")
    raw = text[4:end]
    body = text[end + len("\n---\n") :]

    fm: dict[str, str] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in (">-", ">", "|", "|-"):
            i += 1
            block: list[str] = []
            while i < len(lines) and (lines[i].startswith(" ") or lines[i] == ""):
                block.append(lines[i].strip())
                i += 1
            fm[key] = " ".join(part for part in block if part)
            continue
        fm[key] = value
        i += 1
    return fm, body


def _sanitize_slash(name: str) -> str:
    """OpenClaw slash-command sanitization: lowercase, hyphens → underscores,
    drop anything outside [a-z0-9_], cap at 32 chars."""
    s = re.sub(r"[^a-z0-9_]", "", name.lower().replace("-", "_"))
    return s[:32]


# Tokens that appear because every role description follows the same
# "Provides 0xkey enclave KeyOps runbook for the <role> role: ..." template.
# They are not trigger keywords — agents do not route on them — and including
# them inflates Jaccard overlap between any two role descriptions. Filtering
# them out lets the overlap metric measure what actually drives routing
# (verbs, artifact names, role-specific nouns).
_TEMPLATE_STOPWORDS = frozenset(
    {
        "0xkey",
        "back",
        "behalf",
        "coordinator-issued",
        "does",
        "enclave",
        "external",
        "from",
        "handles",
        "identifies",
        "into",
        "keyops",
        "mentions",
        "packaging",
        "produce",
        "produces",
        "provides",
        "role",
        "roles",
        "runbook",
        "runbooks",
        "runs",
        "running",
        "set",
        "skill",
        "those",
        "touch",
        "touches",
        "user",
        "users",
        "verifying",
        "when",
        "with",
        "within",
        "writes",
    }
)


def _trigger_tokens(description: str) -> set[str]:
    """Heuristic trigger keyword extraction.

    Splits the description on whitespace / punctuation, keeps tokens of
    length ≥ 4 that contain a letter, and drops the template stopwords
    above. Output is intentionally fuzzy — we use it only to flag
    suspiciously high overlap between role skills, never to make routing
    decisions. The authoritative routing test is the manual recall matrix
    captured in `phase0-report.md`.
    """
    raw = re.split(r"[\s,.;:/()\[\]]+", description.lower())
    out: set[str] = set()
    for tok in raw:
        if len(tok) < 4:
            continue
        if not re.search(r"[a-z]", tok):
            continue
        if tok in _TEMPLATE_STOPWORDS:
            continue
        out.add(tok)
    return out


def _enclave_keyops_subcommands() -> set[str]:
    """Collect every (top, sub) pair that `enclave_keyops.py` registers, by
    walking the literal `add_parser(...)` calls in the script source. We do
    not import the script (it expects a config + workdir to construct the
    parser); a regex over the source is good enough for whitelist auditing."""
    src = _read(ENCLAVE_KEYOPS_PY)
    pairs: set[str] = set()
    # Top-level commands appear as `subs.add_parser("name", ...)`.
    tops = set(re.findall(r"subs\.add_parser\(\s*[\"']([a-z0-9_-]+)[\"']", src))
    # Every sub-subparser pattern follows the form
    #     <token>s = <top>.add_subparsers(...)
    #     <token>s.add_parser("sub", ...)
    # but the variable names differ. Easier: capture every add_parser(...)
    # that is NOT subs.add_parser, then pair it with whatever top is closest
    # earlier in the file.
    for m in re.finditer(r"(\w+)\.add_parser\(\s*[\"']([a-z0-9_-]+)[\"']", src):
        var, name = m.group(1), m.group(2)
        if var == "subs":
            continue
        # Find the most recent `<top> = subs.add_parser(...)` BEFORE this match
        # so we know which top-level command this sub belongs to.
        snippet = src[: m.start()]
        owner_match = None
        for tm in re.finditer(
            r"(\w+)\s*=\s*subs\.add_parser\(\s*[\"']([a-z0-9_-]+)[\"']", snippet
        ):
            owner_match = tm
        if owner_match is None:
            continue
        owner_top = owner_match.group(2)
        pairs.add(f"{owner_top} {name}")
    # Add the bare-top commands too (for `verify` which has no subparsers).
    for top in tops:
        pairs.add(top)
    return pairs


_ACTION_LINE_RE = re.compile(r"`([a-z0-9_-]+(?:\s+[a-z0-9_-]+){0,2})`")
_EXTERNAL_TOOLS = {
    # Bootstrap helpers and external commands that role skills legitimately
    # name in their whitelist section but that are NOT `enclave_keyops.py`
    # subcommands.
    "role_init.py",
    "qos_client",
    "qos_client pivot-hash",
    "kubectl",
    "make",
    "docker",
    "docker buildx",
    "aws",
    "aws ecr",
    "aws ecr describe-images",
    # keyops binary entry points — the unified CLI wrapper that delegates to
    # enclave_keyops.py / role_init.py / fetch_*.py internally.
    "keyops",
    "keyops init",
    "keyops fetch-qos-client",
    "keyops fetch-keyops",
    "fetch_keyops.py",
}


def _whitelisted_actions(body: str) -> set[str]:
    """Pull tokens that look like CLI subcommands out of the *allow* portion
    of the Action whitelist section.

    Each role SKILL.md uses the same shape::

        ## Action whitelist

        <allowed actions, in backticks>

        <Role> must NOT invoke `...`, `...`, ...

    We split on the first ``must NOT`` so the deny list does not get counted
    as allowed. External tools (`make`, `kubectl`, etc.) are filtered out via
    :data:`_EXTERNAL_TOOLS` because they are valid in the allow list but are
    not `enclave_keyops.py` subcommands.
    """
    out: set[str] = set()
    section_match = re.search(
        r"^## Action whitelist\s*\n(.*?)(?=^## )", body, re.MULTILINE | re.DOTALL
    )
    if not section_match:
        return out
    section = section_match.group(1)
    deny_marker = re.search(r"\bmust NOT\b", section)
    allow_text = section[: deny_marker.start()] if deny_marker else section
    for m in _ACTION_LINE_RE.finditer(allow_text):
        token = m.group(1).strip()
        if "--" in token or "/" in token:
            continue
        if token in _EXTERNAL_TOOLS:
            continue
        out.add(token)
    return out


class SkillFrontmatterTests(unittest.TestCase):
    """Per-skill SKILL.md frontmatter shape."""

    def test_all_skills_present(self) -> None:
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                self.assertTrue(
                    (SKILLS_DIR / name / "SKILL.md").is_file(),
                    f"expected skills/{name}/SKILL.md",
                )

    def test_name_field_matches_directory(self) -> None:
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                fm, _ = _parse_frontmatter(SKILLS_DIR / name / "SKILL.md")
                self.assertEqual(fm.get("name"), name)

    def test_name_within_64_chars(self) -> None:
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                self.assertLessEqual(len(name), 64)

    def test_sanitized_slash_commands_unique(self) -> None:
        sanitized = {name: _sanitize_slash(name) for name in ROLE_SKILLS}
        self.assertEqual(
            len(set(sanitized.values())),
            len(sanitized),
            f"sanitized slash names collide: {sanitized}",
        )
        for name, slash in sanitized.items():
            with self.subTest(skill=name):
                self.assertLessEqual(len(slash), 32, f"slash {slash!r} > 32 chars")

    def test_description_present_and_capped(self) -> None:
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                fm, _ = _parse_frontmatter(SKILLS_DIR / name / "SKILL.md")
                desc = fm.get("description", "")
                self.assertTrue(desc.strip(), "description must be non-empty")
                self.assertLessEqual(
                    len(desc),
                    1024,
                    f"description {len(desc)} chars > 1024 (Cursor cap)",
                )

    def test_description_is_third_person(self) -> None:
        """Reject first/second-person phrasings that Cursor's create-skill
        rule explicitly calls out (`I can/will/am`, `you can/should/may`,
        `Use this skill to ...`)."""
        banned = (
            re.compile(r"\bI\s+(can|will|am)\b"),
            re.compile(r"\byou\s+(can|should|may)\b", re.IGNORECASE),
            re.compile(r"\bUse this (skill|tool|helper)\s+to\b", re.IGNORECASE),
        )
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                fm, _ = _parse_frontmatter(SKILLS_DIR / name / "SKILL.md")
                desc = fm.get("description", "")
                for pat in banned:
                    self.assertFalse(
                        pat.search(desc),
                        f"description hit banned pattern {pat.pattern!r}: {desc!r}",
                    )

    def test_role_skills_disable_model_invocation(self) -> None:
        """All role skills set `disable-model-invocation: true` so they only
        load on explicit user invocation (per Phase 0 design)."""
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                fm, _ = _parse_frontmatter(SKILLS_DIR / name / "SKILL.md")
                self.assertEqual(
                    fm.get("disable-model-invocation"),
                    "true",
                    f"{name} must set disable-model-invocation: true",
                )

    def test_role_skills_user_invocable(self) -> None:
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                fm, _ = _parse_frontmatter(SKILLS_DIR / name / "SKILL.md")
                self.assertEqual(
                    fm.get("user-invocable"),
                    "true",
                    f"{name} must set user-invocable: true so OpenClaw exposes a slash command",
                )


class DescriptionOverlapTests(unittest.TestCase):
    """Pairwise trigger keyword overlap between the four role skills."""

    def test_pairwise_overlap_below_50pct(self) -> None:
        descs: dict[str, set[str]] = {}
        for name in ROLE_SKILLS:
            fm, _ = _parse_frontmatter(SKILLS_DIR / name / "SKILL.md")
            descs[name] = _trigger_tokens(fm.get("description", ""))
        for a in ROLE_SKILLS:
            for b in ROLE_SKILLS:
                if a >= b:
                    continue
                shared = descs[a] & descs[b]
                smaller = min(len(descs[a]), len(descs[b]))
                if smaller == 0:
                    continue
                ratio = len(shared) / smaller
                with self.subTest(pair=(a, b)):
                    self.assertLess(
                        ratio,
                        0.5,
                        f"{a} vs {b}: trigger overlap {len(shared)}/{smaller}="
                        f"{ratio:.0%} ≥ 50% (shared tokens: {sorted(shared)})",
                    )


class ActionWhitelistTests(unittest.TestCase):
    """Each role skill's Action whitelist must reference real subcommands."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.valid: set[str] = _enclave_keyops_subcommands()

    def test_known_subcommands_have_subs(self) -> None:
        # Sanity: the parser walker found the expected top-level commands.
        for top in ("doctor", "key", "manifest", "deploy", "ceremony", "verify",
                    "bundle", "key-forward"):
            with self.subTest(top=top):
                self.assertTrue(
                    any(p == top or p.startswith(top + " ") for p in self.valid),
                    f"parser walker missed top-level {top!r}",
                )

    def test_role_whitelist_subset_of_real_subcommands(self) -> None:
        for name in ROLE_SKILLS:
            with self.subTest(skill=name):
                _, body = _parse_frontmatter(SKILLS_DIR / name / "SKILL.md")
                actions = _whitelisted_actions(body)
                # Drop bootstrap helpers and bundle-create variants that carry
                # `--kind` (we don't model flag-bearing variants in `valid`).
                cleaned: set[str] = set()
                for a in actions:
                    if a in ("role_init.py", "qos_client", "kubectl"):
                        continue
                    cleaned.add(a)
                missing = sorted(a for a in cleaned if a not in self.valid)
                self.assertFalse(
                    missing,
                    f"{name} whitelists non-existent enclave_keyops.py subcommands: {missing}",
                )

    def test_manifest_set_does_not_whitelist_coordinator_actions(self) -> None:
        _, body = _parse_frontmatter(SKILLS_DIR / "0xkey-keyops-manifest" / "SKILL.md")
        actions = _whitelisted_actions(body)
        forbidden = {
            "manifest generate",
            "manifest envelope",
            "deploy render",
            "deploy apply",
            "ceremony genesis-boot",
            "ceremony boot",
            "ceremony attestation",
            "ceremony post",
            "ceremony reencrypt",
            "ceremony share-extract",
            "verify",
        }
        self.assertFalse(
            actions & forbidden,
            f"manifest skill leaked non-manifest actions: {sorted(actions & forbidden)}",
        )

    def test_share_set_does_not_whitelist_coordinator_actions(self) -> None:
        _, body = _parse_frontmatter(SKILLS_DIR / "0xkey-keyops-share" / "SKILL.md")
        actions = _whitelisted_actions(body)
        forbidden = {
            "manifest generate",
            "manifest approve",
            "manifest envelope",
            "deploy render",
            "deploy apply",
            "ceremony genesis-boot",
            "ceremony boot",
            "ceremony attestation",
            "ceremony post",
            "verify",
        }
        self.assertFalse(
            actions & forbidden,
            f"share skill leaked non-share actions: {sorted(actions & forbidden)}",
        )

    def test_builder_does_not_whitelist_ceremony_actions(self) -> None:
        _, body = _parse_frontmatter(SKILLS_DIR / "0xkey-keyops-builder" / "SKILL.md")
        actions = _whitelisted_actions(body)
        forbidden = {
            "manifest generate",
            "manifest approve",
            "manifest envelope",
            "ceremony genesis-boot",
            "ceremony boot",
            "ceremony attestation",
            "ceremony post",
            "ceremony reencrypt",
            "ceremony share-extract",
            "deploy render",
            "deploy apply",
            "verify",
        }
        self.assertFalse(
            actions & forbidden,
            f"builder skill leaked ceremony actions: {sorted(actions & forbidden)}",
        )


_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")


def _markdown_links(body: str) -> Iterable[str]:
    for m in _LINK_RE.finditer(body):
        href = m.group(1)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        yield href


class LinkResolutionTests(unittest.TestCase):
    """Every internal markdown link inside a role skill SKILL.md must resolve
    to a file that actually exists inside that role skill directory (sync
    fan-out is required to keep self-containment)."""

    def test_links_resolve(self) -> None:
        for name in ROLE_SKILLS:
            skill_dir = SKILLS_DIR / name
            _, body = _parse_frontmatter(skill_dir / "SKILL.md")
            for href in _markdown_links(body):
                with self.subTest(skill=name, link=href):
                    candidate = (skill_dir / href).resolve()
                    self.assertTrue(
                        candidate.exists(),
                        f"{name}: link {href!r} resolves to missing path {candidate}",
                    )


class SyncCheckTests(unittest.TestCase):
    """`tools/sync-skills.py --check` must succeed on a clean tree, proving
    each role skill's contents are byte-identical to `core/`."""

    def test_sync_check_clean(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "sync-skills.py"), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"sync-skills --check failed:\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
