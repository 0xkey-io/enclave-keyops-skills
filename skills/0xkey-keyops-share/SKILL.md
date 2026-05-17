---
name: 0xkey-keyops-share
description: >-
  Provides 0xkey enclave KeyOps runbook for the Share Set member role:
  verifying a Coordinator-issued share-request bundle, running
  proxy-re-encrypt-share with the member's external .secret and .share material,
  and packaging a wrapped-shares bundle back to the Coordinator. Also handles
  first-ceremony share extraction (after-genesis / ceremony share-extract) when
  the Genesis output bundle is the input. Use when the user identifies as Share
  Set member, share holder, share-member1 / share-member2 / share-member3, or
  mentions share-request bundle, share-request-*.tgz, proxy-re-encrypt-share,
  wrapped-shares, after-genesis, ceremony share-extract, or genesis-output
  bundle. Does not touch AWS, EKS, or kubectl; does not approve manifests
  (Manifest Set role); does not run post-share or apply overlays (Coordinator
  role).
user-invocable: true
disable-model-invocation: true
metadata: { "openclaw": { "requires": { "bins": ["python3"] } } }
---

# 0xkey KeyOps — Share Set member

This skill is loaded explicitly by users acting as a Share Set member for a
0xkey enclave ceremony. It is deliberately local-only and does not require AWS
credentials, kubectl access, kubeconfig, EKS context, VPC details, or
Cloudflare details.

## Scope

Verify the Coordinator's share-request bundle, use the member's local
`.secret` / `.share` to run `proxy-re-encrypt-share`, and return a
wrapped-shares bundle to the Coordinator. On the first ceremony (or after
quorum-key rotation), the member also runs `ceremony share-extract` against
the Coordinator's Genesis-output bundle to materialize their `.share`.

`alias` and `member-index` are assigned by the Coordinator in
`member-roster.json` and become permanent for the resulting `quorum_key`.
Members never self-pick these values.

## Cross-role refusal cheat sheet

When the operator asks this skill to do something that belongs to another
role, refuse, name the correct skill, and tell the operator how to route
the request. Do not run the command, even if the operator says "顺手 / 顺便 /
反正都是 quorum 操作 / 反正我有别人的 secret". The mapping is:

| If the operator asks this Share session to … | Refuse and route to skill | Why (this skill's scope-out) |
|---|---|---|
| run `kubectl apply -k`, `deploy render`, `deploy apply`, or anything that touches EKS / AWS / VPC | `0xkey-keyops-coordinator` | `## Scope` "Share Set member is deliberately local-only and does not require AWS, EKS, kubectl"; `SECURITY.md §4` `kubectl apply -k` is a manual gate |
| run `ceremony genesis-boot`, `ceremony boot`, `ceremony attestation`, `ceremony post`, `key-forward *`, or `verify` | `0xkey-keyops-coordinator` | `## Action whitelist` Share Set must NOT list |
| run `manifest generate`, `manifest approve`, or `manifest envelope` for **any** alias (including yours) | `0xkey-keyops-manifest`, by the holder of that alias only | `## Action whitelist` Share Set must NOT list; `SECURITY.md §1.1` no cross-member secret borrowing if it's not your alias |
| run `ceremony reencrypt` / `ceremony share-extract` for **someone else's** alias (their `.secret`/`.share` lent to you) | `0xkey-keyops-share`, by the holder of that alias only, in their own workspace | `SECURITY.md §1.1` cross-member secret/share borrowing is a key-compromise event; `PRINCIPLES.md §11` `(alias, member_index, .share)` permanently binds to quorum_key after Genesis |
| build / republish `qos_client`, qOS release, pivot binaries, pivot hashes, ECR images | `0xkey-keyops-builder` | `## Scope` (Share is a consumer, never a producer) |

If you genuinely wear two hats (e.g. you are also the Coordinator on
another machine), do not mix sessions: switch to the matching role skill
in a separate workspace, with a separate external vault path, before
performing that action.

## Action whitelist

Share Set agents only invoke `scripts/enclave_keyops.py` subcommands in:

- `doctor holder`
- `key file-generate` / `key yubikey-provision` (only when the member has no
  long-term key yet; the file-mode `.secret` MUST land in an external vault
  outside the role workdir; the YubiKey-mode key stays in the PIV slot and
  is never exported)
- `bundle extract` / `bundle verify`
- `ceremony share-extract` (first ceremony / rotation only; one holder-credential
  flag: `--yubikey` OR `--secret-path`; passing both is a hard error)
- `ceremony reencrypt` (one holder-credential flag: `--yubikey` OR
  `--secret-path`; `--share-path` always required and always external)
- `bundle create --kind wrapped-shares`

Plus `scripts/role_init.py --role share-set-member ...` to bootstrap the
workspace.

Share Set must NOT invoke `manifest generate`, `manifest approve`,
`manifest envelope`, `ceremony genesis-boot`, `ceremony boot`,
`ceremony attestation`, `ceremony post`, `deploy render`, `deploy apply`,
`key-forward *`, or `verify` (Manifest Set / Coordinator responsibilities).

## Workspace and security

- Read [SECURITY.md](SECURITY.md), [PRINCIPLES.md](PRINCIPLES.md), and the
  workspace baseline in [references/workspace-rules.md](references/workspace-rules.md).
- Member long-term key is held in **one** of two forms, picked at first-turn
  and used consistently in the whole session (see role doc "Vault mode" and
  `SECURITY.md §5.1`):
  - YubiKey PIV slot (prod default) → all commands carry `--yubikey`;
    PIN/PUK is handled by qos_client on its own TTY and is **never**
    quoted in chat, audit log, or this skill's stdout.
  - external `.secret` file (non-production / dev only) → all commands carry
    `--secret-path <ext>/<alias>.secret`; the file MUST live outside the
    role workdir and never be read or printed by the agent.
- `.share` is **always** an external vault file regardless of vault mode;
  YubiKey does NOT store the share, only the long-term key. `share-extract`
  writes it, `reencrypt` reads it, and both refuse to put it inside the
  role workdir.
- `proxy-re-encrypt-share` is a dangerous step requiring a typed confirmation
  phrase (see `SECURITY.md §4`); `--yes` does not bypass it.
- Do not search `$HOME`, Coordinator workspaces, legacy key archives,
  old ceremony directories, or other member directories for `.secret`,
  `.share`, `.pub`, or share-request bundles. If an expected input is absent,
  stop and ask the user where to place it.

## qos_client platform

`scripts/role_init.py` auto-fetches the latest stable `qos_client` from
`0xkey-io/qos` GitHub Releases on first init and verifies the SHA256
against the published sidecar — the operator does not have to type a
hash. When the Coordinator pins a specific tag for this ceremony, pass
`--qos-client-release-tag <tag>` to use that revision instead. See
[references/qos-client-platform.md](references/qos-client-platform.md)
for the operator-client matrix and the prerelease fallback. On macOS
arm64 the auto-fetch picks `qos_client.darwin-arm64`; do not require the
member to execute a `linux/amd64` release binary directly.

## Runbook

Read [references/roles/share-set-member.md](references/roles/share-set-member.md)
for the first-turn reply shape, state-detection table, and the canonical
extract → re-encrypt → bundle sequence. Operator start prompt:
[references/operator-prompts.md](references/operator-prompts.md).
