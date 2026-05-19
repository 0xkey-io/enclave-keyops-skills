"""Static checks on the safety / cross-role refusal language that the
multi-agent test pass surfaced as missing or implicit.

These tests are intentionally string-level. They make sure the docs that
agents quote when refusing a dangerous request do not silently regress to
weaker language. If you need to rephrase the refusal text, update both the
doc and the corresponding regex here.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from ._helpers import REPO_ROOT


CORE = REPO_ROOT / "core"
SKILLS = REPO_ROOT / "skills"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class SecurityForbidsCrossMemberBorrowing(unittest.TestCase):
    """SECURITY.md must have an explicit clause that one `.secret`/`.share`
    belongs to exactly one alias holder, and that any cross-member borrow
    is a key-compromise event. Previously this was only implicit (scattered
    across §1, §5, workspace-rules.md), which let operators ask "I'll just
    sign this for them" without an obvious doc to point at."""

    def setUp(self) -> None:
        self.text = _read(CORE / "SECURITY.md")

    def test_has_dedicated_section(self) -> None:
        # The section heading exists. We anchor on the 1.1 number so it's
        # discoverable from refusal messages like "see SECURITY.md §1.1".
        self.assertRegex(
            self.text,
            r"##\s+1\.1\s+",
            "SECURITY.md must contain a numbered §1.1 section",
        )
        # Find the §1.1 body (between §1.1 heading and the next H2) and
        # confirm it actually talks about cross-member borrowing.
        m = re.search(
            r"##\s+1\.1\s+.*?(?=\n##\s)", self.text, re.DOTALL
        )
        self.assertIsNotNone(m, "could not isolate §1.1 body")
        body = m.group(0)
        self.assertTrue(
            "borrow" in body.lower(),
            "SECURITY.md §1.1 must discuss cross-member secret/share borrowing",
        )

    def test_calls_out_borrow_scenarios(self) -> None:
        # The section should name the three common social-engineering shapes
        # so an agent can quote a specific example back to the operator.
        for needle in ("away", "share a workstation", "key compromise"):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    self.text,
                    f"SECURITY.md §1.1 should mention {needle!r}",
                )

    def test_disallows_unsafe_auto_confirm_escape(self) -> None:
        # §1.1 must explicitly bar `--unsafe-auto-confirm` / `--yes` from
        # bypassing the "holder = roster" check; otherwise an agent can
        # rationalize the borrow as "the user said --yes".
        excerpt = re.search(
            r"##\s+1\.1.*?(?=\n##\s+\d)", self.text, re.DOTALL
        )
        self.assertIsNotNone(excerpt, "could not find §1.1 body")
        body = excerpt.group(0)
        self.assertTrue(
            "unsafe-auto-confirm" in body and "--yes" in body,
            "§1.1 must explicitly say --unsafe-auto-confirm / --yes "
            "do not bypass the holder=roster check",
        )


class WorkspaceRulesHasRosterFirstRule(unittest.TestCase):
    """workspace-rules.md must carry the Roster-first rule so both Manifest
    and Share role docs can defer to it instead of repeating language
    (which is how the Manifest vs Share inconsistency snuck in)."""

    def setUp(self) -> None:
        self.text = _read(CORE / "references" / "workspace-rules.md")

    def test_section_exists(self) -> None:
        self.assertRegex(
            self.text,
            r"##\s+Roster-first rule",
            "workspace-rules.md must define a Roster-first rule section",
        )

    def test_states_precedence_over_uninitialized(self) -> None:
        self.assertIn("waiting-for-roster", self.text)
        self.assertRegex(
            self.text,
            r"precedence",
            "Roster-first rule must say it takes precedence over the "
            "uninitialized row of the state-detection table",
        )

    def test_blocks_baking_user_alias(self) -> None:
        for needle in ("config.json", "outbox/", "role_init.py"):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    self.text,
                    f"Roster-first rule must reference {needle!r} as a "
                    "place not to bake the user-claimed alias",
                )


class MemberRoleDocsCiteRosterPrecedence(unittest.TestCase):
    """Both Manifest and Share role docs must carry an explicit precedence
    rule above the state-detection table, pointing at workspace-rules.md.
    Previously the Manifest agent enforced the strict reading while the
    Share agent followed the literal `uninitialized` row, leading to
    divergent behavior on the same input."""

    DOCS = (
        CORE / "references" / "roles" / "manifest-set-member.md",
        CORE / "references" / "roles" / "share-set-member.md",
    )

    def test_each_doc_references_roster_first_rule(self) -> None:
        for path in self.DOCS:
            text = _read(path)
            with self.subTest(doc=path.name):
                self.assertIn(
                    "Roster-first rule",
                    text,
                    f"{path.name} must reference the Roster-first rule in "
                    "workspace-rules.md",
                )

    def test_each_doc_overrides_uninitialized_row(self) -> None:
        for path in self.DOCS:
            text = _read(path)
            with self.subTest(doc=path.name):
                # The doc must explicitly say the precedence rule overrides
                # the literal `uninitialized` row when waiting-for-roster is
                # also active. Allow either order and arbitrary whitespace
                # (incl. newlines) between the two keywords.
                self.assertRegex(
                    text,
                    r"(overrides|precedence)[\s\S]{0,400}?uninitialized|"
                    r"uninitialized[\s\S]{0,400}?(overrides|precedence)",
                    f"{path.name} must spell out that the Roster-first rule "
                    "overrides the uninitialized row",
                )

    def test_share_doc_notes_first_ceremony_sequence(self) -> None:
        text = _read(
            CORE / "references" / "roles" / "share-set-member.md"
        )
        # Share-specific: spell out that key-init must precede share-extract
        # (the Share agent flagged this ordering was implicit).
        self.assertIn("first-ceremony", text.replace("\n", " ").lower()
                      .replace("first ceremony", "first-ceremony"))
        self.assertRegex(
            text,
            r"share-extract.*requires.*--secret-path|--secret-path.*share-extract",
        )


class EachSkillHasCrossRoleRefusalCheatSheet(unittest.TestCase):
    """Each role SKILL.md must carry an explicit Cross-role refusal cheat
    sheet, mapping the common adversarial prompts to the skill that should
    handle them. Without this, agents had to assemble the refusal text from
    the action whitelist + SECURITY.md + workspace-rules.md every time,
    which led to inconsistent wording across the four agents in the
    multi-agent test."""

    ROLES = (
        "0xkey-keyops-coordinator",
        "0xkey-keyops-manifest",
        "0xkey-keyops-share",
        "0xkey-keyops-builder",
    )

    SECTION_HEADING = "## Cross-role refusal cheat sheet"

    # Each role MUST point at the correct skill for at least these specific
    # adversarial requests. We do not enforce phrasing — only the presence
    # of the target skill name near a quoted refusal trigger.
    REQUIRED_ROUTING: dict[str, list[tuple[str, str]]] = {
        "0xkey-keyops-coordinator": [
            ("manifest approve", "0xkey-keyops-manifest"),
            ("ceremony reencrypt", "0xkey-keyops-share"),
            ("qos_client", "0xkey-keyops-builder"),
        ],
        "0xkey-keyops-manifest": [
            ("kubectl apply", "0xkey-keyops-coordinator"),
            ("manifest approve", "0xkey-keyops-manifest"),  # someone-else's alias
            ("qos_client", "0xkey-keyops-builder"),
        ],
        "0xkey-keyops-share": [
            ("kubectl apply", "0xkey-keyops-coordinator"),
            ("ceremony genesis-boot", "0xkey-keyops-coordinator"),
            ("manifest approve", "0xkey-keyops-manifest"),
            ("qos_client", "0xkey-keyops-builder"),
        ],
        "0xkey-keyops-builder": [
            ("kubectl apply", "0xkey-keyops-coordinator"),
            ("manifest approve", "0xkey-keyops-manifest"),
            ("ceremony genesis-boot", "0xkey-keyops-coordinator"),
        ],
    }

    def _section(self, role: str) -> str:
        text = _read(SKILLS / role / "SKILL.md")
        # Capture from the heading up to (but not including) the next H2.
        m = re.search(
            r"^## Cross-role refusal cheat sheet\s*\n(.*?)(?=^## )",
            text,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            f"{role}/SKILL.md is missing the Cross-role refusal cheat sheet section",
        )
        return m.group(1)

    def test_section_present_in_every_role(self) -> None:
        for role in self.ROLES:
            with self.subTest(role=role):
                self._section(role)  # raises via assertIsNotNone if absent

    def test_section_appears_before_action_whitelist(self) -> None:
        for role in self.ROLES:
            text = _read(SKILLS / role / "SKILL.md")
            cheat_idx = text.find(self.SECTION_HEADING)
            wl_idx = text.find("## Action whitelist")
            with self.subTest(role=role):
                self.assertGreaterEqual(
                    cheat_idx, 0, f"{role}: no cheat sheet heading"
                )
                self.assertGreaterEqual(
                    wl_idx, 0, f"{role}: no Action whitelist heading"
                )
                self.assertLess(
                    cheat_idx,
                    wl_idx,
                    f"{role}: cheat sheet should appear before Action whitelist "
                    "so an agent reading top-down meets the refusal map first",
                )

    def test_section_routes_each_required_request(self) -> None:
        for role, mappings in self.REQUIRED_ROUTING.items():
            section = self._section(role)
            for trigger, target_skill in mappings:
                with self.subTest(role=role, trigger=trigger):
                    self.assertIn(
                        trigger,
                        section,
                        f"{role} cheat sheet missing trigger {trigger!r}",
                    )
                    self.assertIn(
                        target_skill,
                        section,
                        f"{role} cheat sheet must route somewhere via {target_skill!r}",
                    )

    def test_section_cites_security_borrow_clause_where_relevant(self) -> None:
        """Roles that could be asked to sign on someone else's behalf must
        cite SECURITY.md §1.1 in their cheat sheet so the refusal text
        carries the new authoritative reference."""
        for role in (
            "0xkey-keyops-coordinator",
            "0xkey-keyops-manifest",
            "0xkey-keyops-share",
            "0xkey-keyops-builder",
        ):
            section = self._section(role)
            with self.subTest(role=role):
                self.assertRegex(
                    section,
                    r"SECURITY\.md\s*§1\.1",
                    f"{role} cheat sheet must cite SECURITY.md §1.1",
                )


class OperatorPromptsHaveCompleteTemplates(unittest.TestCase):
    """The minimal-operator prompts must collect every input the matching
    role doc later asks for, so cold-start agents don't have to bounce."""

    def setUp(self) -> None:
        self.text = _read(CORE / "references" / "operator-prompts.md")

    def test_builder_template_collects_env_and_account(self) -> None:
        # Find the Builder section body.
        m = re.search(
            r"##\s+Builder\s*\n```text\n(.*?)\n```", self.text, re.DOTALL
        )
        self.assertIsNotNone(m, "Builder operator prompt missing")
        body = m.group(1)
        for needle in (
            "Target environment",
            "AWS account",
            "ECR registry",
            "operator-client",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)

    def test_builder_template_mentions_platforms(self) -> None:
        m = re.search(
            r"##\s+Builder\s*\n```text\n(.*?)\n```", self.text, re.DOTALL
        )
        body = m.group(1)
        self.assertRegex(
            body,
            r"darwin/arm64|linux/amd64",
            "Builder prompt must hint at platform list for native operator-clients",
        )

    def test_share_template_separates_secret_and_share(self) -> None:
        m = re.search(
            r"##\s+Share Set member\s*\n```text\n(.*?)\n```",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "Share Set member operator prompt missing")
        body = m.group(1)
        secret_lines = [
            line for line in body.splitlines() if "external secret" in line
        ]
        share_lines = [
            line for line in body.splitlines() if "external share" in line
        ]
        self.assertTrue(
            secret_lines, "Share prompt must have an `external secret` line"
        )
        self.assertTrue(
            share_lines, "Share prompt must have an `external share` line"
        )
        # The Share prompt must NOT combine them with `/`.
        self.assertNotIn(
            "external secret/share",
            body,
            "Share prompt must split secret and share into separate lines so "
            "operators don't confuse the two (different producers, different "
            "transports)",
        )

    def test_share_template_mentions_genesis_output_bundle(self) -> None:
        m = re.search(
            r"##\s+Share Set member\s*\n```text\n(.*?)\n```",
            self.text,
            re.DOTALL,
        )
        body = m.group(1)
        self.assertIn(
            "genesis-output",
            body,
            "Share prompt must hint that first-ceremony members need a "
            "genesis-output bundle path",
        )


if __name__ == "__main__":
    unittest.main()
