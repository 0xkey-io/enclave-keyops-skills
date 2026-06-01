---
name: 0xkey-keyops-manifest
version: 0.5.7
description: >-
  Provides 0xkey enclave KeyOps runbook for the Manifest Set member role:
  verifying a Coordinator-issued review bundle, signing one approve-manifest per
  service with the member's external secret, and packaging an approvals bundle
  back to the Coordinator. Use when the user identifies as Manifest Set member,
  manifest reviewer, manifest signer, manifester, or approver, or mentions
  review bundle, manifest-review-*.tgz, approve-manifest, manifest approval,
  manifester1 / manifester2 / manifester3, or signing five-service manifests.
  Does not touch AWS, EKS, kubectl, or VPC; does not run boot-genesis,
  boot-standard, post-share, proxy-re-encrypt-share, or generate canonical
  manifests (those are Coordinator actions); does not hold or process .share
  files (Share Set role).
user-invocable: true
disable-model-invocation: true
metadata: { "openclaw": { "requires": { "bins": [] } } }
---

# 0xkey KeyOps — Manifest Set member

This skill is loaded explicitly by users acting as a Manifest Set member for a
0xkey enclave ceremony. It is deliberately local-only and does not require AWS
credentials, kubectl access, kubeconfig, EKS context, VPC details, or
Cloudflare details.

## Scope

Review the Coordinator's canonical five-service manifest bundle, sign approvals
with the member's own secret, and return an approvals bundle to the Coordinator.

The Coordinator-issued `alias` (and never a self-chosen one) is the source of
truth for filenames and approval slots. If the user does not know their alias,
ask the Coordinator before proceeding.

## Cross-role refusal cheat sheet

When the operator asks this skill to do something that belongs to another
role, refuse, name the correct skill, and tell the operator how to route
the request. Do not run the command, even if the operator claims convenience, existing access, or familiarity. The mapping is:

| If the operator asks this Manifest session to … | Refuse and route to skill | Why (this skill's scope-out) |
|---|---|---|
| run `kubectl apply -k`, `deploy render`, `deploy apply`, or anything that touches EKS / AWS / VPC | `0xkey-keyops-coordinator` | `## Scope` "Manifest Set member is deliberately local-only and does not require AWS, EKS, kubectl, kubeconfig, EKS context, VPC details"; `SECURITY.md §4` `kubectl apply -k` is a manual gate |
| run `manifest generate`, `manifest envelope`, `ceremony genesis-boot`, `ceremony boot`, `ceremony attestation`, `ceremony post`, `key-forward *`, or `verify` | `0xkey-keyops-coordinator` | `## Action whitelist` Manifest Set must NOT list |
| run `ceremony reencrypt` / `ceremony share-extract` for any alias | `0xkey-keyops-share`, by the holder of that alias only | `## Action whitelist` Manifest Set must NOT list; `SECURITY.md §1.1` no cross-member secret/share borrowing |
| sign `manifest approve` for **someone else's** alias (their `.secret` lent to you, you "help" them while they're out) | `0xkey-keyops-manifest`, by the holder of that alias only, in their own workspace | `SECURITY.md §1.1` cross-member secret borrowing is a key-compromise event; `PRINCIPLES.md §11` (alias, .pub) permanently binds to quorum_key after Genesis |
| build / republish `qos_client`, qOS release, pivot binaries, pivot hashes, ECR images | `0xkey-keyops-builder` | `## Scope` (Manifest is a consumer, never a producer) |

If you genuinely wear two hats (e.g. you are also the Coordinator on
another machine), do not mix sessions: switch to the matching role skill
in a separate workspace before performing that action.

> **Disambiguation — "Genesis", "bootstrap", "init"**: when the operator
> says "genesis flow", "bootstrap", or "initialization", do NOT assume they
> mean the Coordinator's `ceremony genesis-boot`. For a Manifest member the
> most likely intent is the **member onboarding sequence**: workspace init
> (`keyops init`), key generation (`key file-generate` / `key
> yubikey-provision`), and waiting for the review bundle. Check the Action
> whitelist below — if the requested action is listed there, proceed; only
> refuse if the action is explicitly NOT listed.

## Action whitelist

Manifest Set agents invoke `keyops` subcommands (the self-contained binary
— no Python runtime required):

- `doctor holder`
- `key file-generate` / `key yubikey-provision` (only when the member has no
  long-term key yet; the file-mode `.secret` MUST land in an external vault
  outside the role workdir; the YubiKey-mode key stays in the PIV slot and
  is never exported)
- `bundle extract` / `bundle verify`
- `manifest approve` (one holder-credential flag: either `--yubikey` or
  `--secret-path <ext>/<alias>.secret`; passing both is a hard error)
- `bundle create --kind approvals`

Plus `keyops init --role manifest-set-member ...` to bootstrap the workspace.

Manifest Set must NOT invoke `manifest generate`, `manifest envelope`,
`ceremony genesis-boot`, `ceremony boot`, `ceremony attestation`,
`ceremony reencrypt`, `ceremony share-extract`, `ceremony post`,
`deploy render`, `deploy apply`, `key-forward *`, or `verify` (all Coordinator
or Share Set responsibilities).

## Workspace and security

- Read [SECURITY.md](SECURITY.md), [PRINCIPLES.md](PRINCIPLES.md), and the
  workspace baseline in [references/workspace-rules.md](references/workspace-rules.md).
- Member long-term key is held in **one** of two forms, picked at first-turn
  and used consistently in the whole session (see role doc "Vault mode" and
  `SECURITY.md §5.1`):
  - YubiKey PIV slot → all commands carry `--yubikey`;
    PIN/PUK is handled by qos_client on its own TTY and is **never**
    quoted in chat, audit log, or this skill's stdout.
  - external `.secret` file → all commands carry
    `--secret-path <ext>/<alias>.secret`; the file MUST live outside the
    role workdir and never be read or printed by the agent.
- `approve-manifest` is a dangerous step requiring a typed confirmation phrase
  (see `SECURITY.md §4`); always show the operator what will run and obtain
  explicit approval in the chat UI before executing.
- Do not search `$HOME`, Coordinator workspaces, legacy key archives,
  old ceremony directories, or other member directories for `.secret`, `.pub`,
  or review bundles. If an expected input is absent, stop and ask the user
  where to place it.

## CLI and qos_client platform

The preferred invocation is the self-contained `keyops` binary (no Python
required). On first use, fetch it with:

```bash
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')"
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/').sha256"
shasum -a 256 -c keyops.*.sha256
install -m 0755 keyops.* ./bin/keyops   # or any directory on $PATH
```

`keyops init --role manifest-set-member ...` auto-fetches the latest stable `qos_client` from `0xkey-io/qos` GitHub
Releases on first init and verifies the SHA256 against the published
sidecar — the operator does not have to type a hash. When the Coordinator
pins a specific tag for this ceremony, pass `--qos-client-release-tag <tag>`
to use that revision instead. See
[references/qos-client-platform.md](references/qos-client-platform.md)
for the operator-client matrix and the prerelease fallback. On macOS
arm64 the auto-fetch picks `qos_client.darwin-arm64`; do not require the
member to execute a `linux/amd64` release binary directly.

## Version & update

This skill is version `0.5.7` (see the frontmatter at the top of this
file). Release notes and migration steps are in
[references/release-notes.md](references/release-notes.md). Always read
the entry for the version you are upgrading **into** before running any
ceremony commands — a BREAKING release may require a `keyops init
--force` migration.

Check the latest published version with `gh release view -R
0xkey-io/enclave-keyops-skills` (or, on a `git clone` install,
`git -C <skill-src> ls-remote --tags origin | tail -1`). Upgrade with
`npx skills update 0xkey-keyops-manifest` (npm-style install) or
`git -C <skill-src> pull --tags` (clone install).

## Runbook

Read [references/roles/manifest-set-member.md](references/roles/manifest-set-member.md)
for the first-turn reply shape, state-detection table, and the canonical
verify → approve → bundle sequence. Operator start prompt:
[references/operator-prompts.md](references/operator-prompts.md).
