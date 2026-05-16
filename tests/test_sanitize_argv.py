from __future__ import annotations

import unittest

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()
sanitize_argv = ek.sanitize_argv


class SanitizeArgvTests(unittest.TestCase):
    def test_redacts_value_after_secret_flag(self) -> None:
        out = sanitize_argv(["qos_client", "--secret-path", "/v/x.secret"])
        self.assertEqual(out[1], "--secret-path")
        self.assertEqual(out[2], "[REDACTED]")

    def test_redacts_value_after_share_flag(self) -> None:
        out = sanitize_argv(["qos_client", "--share-path", "/v/x.share"])
        self.assertEqual(out[2], "[REDACTED]")

    def test_redacts_seed_flag(self) -> None:
        out = sanitize_argv(["qos_client", "--master-seed-path", "/v/seed.bin"])
        self.assertEqual(out[2], "[REDACTED]")

    def test_redacts_pem_value_via_sensitive_flag_name(self) -> None:
        # A path ending in `.pem` is only redacted when carried by a
        # sensitive-flag NAME (e.g. `--secret-path=/v/x.pem`). The previous
        # behavior — substring-matching the VALUE itself — caused false
        # positives on public binary paths and `.pub` filenames; see
        # `SENSITIVE_FLAG_MARKERS` docstring in enclave_keyops.py.
        out = sanitize_argv(["qos_client", "--secret-path=/v/x.pem"])
        self.assertEqual(out[1], "--secret-path=[REDACTED]")

    def test_does_not_redact_neutral_flag_with_pem_value(self) -> None:
        # `--cert` is a neutral flag name (no sensitive substring); its
        # value should be preserved verbatim even when it happens to end
        # in `.pem`. PEM cert paths are typically public material.
        out = sanitize_argv(["qos_client", "--cert=/v/x.pem"])
        self.assertEqual(out, ["qos_client", "--cert=/v/x.pem"])

    def test_redacts_token_flag(self) -> None:
        out = sanitize_argv(["qos_client", "--token", "abc123"])
        self.assertEqual(out[2], "[REDACTED]")

    def test_redacts_eq_form(self) -> None:
        out = sanitize_argv(["qos_client", "--secret-path=/v/x.secret"])
        self.assertEqual(out[1], "--secret-path=[REDACTED]")

    def test_redacts_eq_form_for_seed(self) -> None:
        out = sanitize_argv(["qos_client", "--master-seed-path=/v/seed"])
        self.assertEqual(out[1], "--master-seed-path=[REDACTED]")

    def test_does_not_redact_unrelated_args(self) -> None:
        out = sanitize_argv(["qos_client", "boot-standard", "--manifest-envelope-path", "/m/x.json"])
        self.assertNotIn("[REDACTED]", out)

    def test_trailing_sensitive_flag_emits_redacted_placeholder(self) -> None:
        # If a sensitive flag appears as the last token without an =, the
        # function defensively appends `[REDACTED]` so a stray secret can never
        # leak into audit logs even if argv was truncated mid-construction.
        out = sanitize_argv(["qos_client", "--secret-path"])
        self.assertEqual(out, ["qos_client", "--secret-path", "[REDACTED]"])

    # ----------------------------------------------------------------------
    # Regressions for the three real-world cases observed during the
    # YubiKey integration test on 2026-05-16. See `SENSITIVE_FLAG_MARKERS`
    # docstring for the full backstory; the symptom was that the audit
    # log printed `shared/qos_client '[REDACTED]' --pub-path
    # outbox/share-member2-yk-smoke.pub '[REDACTED]'` — the subcommand
    # name `provision-yubikey` was erased and a phantom trailing
    # `[REDACTED]` was appended.
    # ----------------------------------------------------------------------

    def test_binary_path_with_shared_prefix_does_not_pollute_next_token(self) -> None:
        # `shared/qos_client` contains the "share" substring but is a
        # public binary path, not a sensitive flag. It must NOT cause the
        # following positional argument (the subcommand name) to be
        # redacted.
        out = sanitize_argv(
            ["shared/qos_client", "provision-yubikey", "--pub-path", "outbox/x.pub"]
        )
        self.assertEqual(out[1], "provision-yubikey")
        self.assertNotIn("[REDACTED]", out)

    def test_positional_subcommand_with_sensitive_substring_is_preserved(self) -> None:
        # Subcommand names like `provision-yubikey`, `proxy-re-encrypt-share`,
        # `after-genesis`, `approve-manifest` are public operation
        # metadata and must never be redacted regardless of their
        # substrings — that information is essential for audit log
        # readability.
        for sub in ("provision-yubikey", "proxy-re-encrypt-share", "after-genesis"):
            out = sanitize_argv(["qos_client", sub])
            self.assertEqual(out[1], sub, f"subcommand {sub!r} was erased")

    def test_value_containing_share_substring_does_not_append_phantom_redacted(
        self,
    ) -> None:
        # The bug: a final positional VALUE whose path happens to contain
        # a sensitive substring (e.g. `outbox/share-member2-yk-smoke.pub`)
        # used to trigger `redact_next=True` (because the substring match
        # ran against the whole token), which then appended a phantom
        # trailing `[REDACTED]` after the loop — making the audit log
        # imply an extra omitted secret value that did not actually exist.
        out = sanitize_argv(
            [
                "qos_client",
                "provision-yubikey",
                "--pub-path",
                "outbox/share-member2-yk-smoke.pub",
            ]
        )
        self.assertEqual(
            out,
            [
                "qos_client",
                "provision-yubikey",
                "--pub-path",
                "outbox/share-member2-yk-smoke.pub",
            ],
        )

    def test_full_provision_yubikey_argv_round_trip(self) -> None:
        # End-to-end re-creation of the exact argv that produced the
        # original misbehaving audit-log line. After the fix, every
        # token must survive verbatim.
        argv = [
            "shared/qos_client",
            "provision-yubikey",
            "--pub-path",
            "outbox/share-member2-yk-smoke.pub",
        ]
        self.assertEqual(sanitize_argv(argv), argv)


if __name__ == "__main__":
    unittest.main()
