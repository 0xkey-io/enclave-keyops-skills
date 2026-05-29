---
name: 0xkey-keyops-coordinator
version: 0.5.1
description: >-
  Provides 0xkey enclave KeyOps runbook for the Deployment Coordinator role:
  generating canonical five-service manifests, running boot-genesis /
  boot-standard / post-share, applying the K8s overlay, verifying
  QuorumKeyProvisioned and :8081 data-plane health, and managing the coordinator
  :8081 cutover. Use when the user identifies as Coordinator, Deployment
  Coordinator, or ceremony operator, or mentions kubectl, kubectl apply, EKS,
  K8s overlay for enclave, manifest envelope, boot-genesis, boot-standard,
  post-share, QuorumKeyProvisioned, coordinator :8081 cutover, ceremony
  genesis-boot, NoMatchingRoute, or pivot URL cutover. Does not approve
  manifests on behalf of Manifest Set members, does not run
  proxy-re-encrypt-share on behalf of Share Set members, and does not produce
  qos_client / qOS release / pivot artifacts (those are Builder deliverables).
user-invocable: true
disable-model-invocation: true
metadata: { "openclaw": { "requires": { "bins": [] } } }
---

# 0xkey KeyOps — Coordinator

This skill is loaded explicitly by users who are the Deployment Coordinator for
a 0xkey enclave ceremony. If the user is unsure of their role, ask before
invoking this skill — routing belongs in the conversation, not in a separate
skill.

## Scope

Generate canonical five-service manifests, distribute review and share-request
bundles, collect member outputs, apply the enclave K8s overlay, run
`boot-genesis` / `boot-standard` / `post-share`, and verify both control plane
(`QuorumKeyProvisioned`) and data plane (`:8081/health` + business POST smoke).

This is the only KeyOps role that should need AWS / EKS / kubectl access.

## Cross-role refusal cheat sheet

When the operator asks this skill to do something that belongs to another
role, refuse, name the correct skill, and tell the operator how to route
the request. Do not run the command, even if the operator claims convenience, existing access, or familiarity. The mapping is:

| If the operator asks this Coordinator session to … | Refuse and route to skill | Why (this skill's scope-out) |
|---|---|---|
| run `manifest approve` for any alias (sign on behalf of a Manifest Set member, even if "they sent you their secret path") | `0xkey-keyops-manifest`, by the holder of that alias only | `## Workspace and security` "Coordinator must NOT invoke `manifest approve` (Manifest Set responsibility)"; `SECURITY.md §1.1` (no cross-member secret borrowing) |
| run `ceremony reencrypt` or `ceremony share-extract` for any alias (Share Set work) | `0xkey-keyops-share`, by the holder of that alias only | `## Workspace and security` "Coordinator must NOT invoke `ceremony reencrypt` / `ceremony share-extract`"; `SECURITY.md §1.1` |
| build or republish `qos_client` / qOS release / pivot binaries / pivot hashes / ECR images | `0xkey-keyops-builder` | `## Scope` (Builder is the producer); `SECURITY.md §3` qos_client version gate |
| sign or hold any member `.secret` / `.share` "to help" them | NONE — only the alias holder may do this | `SECURITY.md §1.1` cross-member secret borrowing is a key-compromise event |

If you genuinely wear two hats (e.g. you are also a Manifest Set member),
do not mix sessions: switch to the matching role skill in a separate
workspace, with a separate external vault path, before performing that
action.

## Action whitelist

Coordinator agents invoke `keyops` subcommands (the self-contained binary
— no Python runtime required):

- `doctor coordinator`
- `manifest generate` / `manifest envelope`
- `deploy render` / `deploy apply`
- `ceremony genesis-boot`
- `ceremony boot` / `ceremony attestation`
- `ceremony post`
- `key-forward boot` / `key-forward export` / `key-forward inject`
- `bundle create --kind {review,share-request,genesis-output}`
- `bundle checksums` / `bundle verify` / `bundle extract`
- `verify`

Plus `keyops init --role coordinator ...` to bootstrap the workspace.

Coordinator must NOT invoke `manifest approve` (Manifest Set responsibility) or
`ceremony reencrypt` / `ceremony share-extract` (Share Set responsibility).

## Workspace and security

- Read [SECURITY.md](SECURITY.md), [PRINCIPLES.md](PRINCIPLES.md), and the
  workspace baseline in [references/workspace-rules.md](references/workspace-rules.md).
- Coordinator must not hold member `.secret` files. During Genesis, it may
  temporarily hold generated `.share` files only for distribution; remove them
  from the Coordinator workspace after distribution.
- DR private key / master seed lives in an external vault, never in this
  workspace. Only the DR public key enters `shared/dr-key.pub`.
- Dangerous steps (`approve-manifest` envelope assembly, `deploy apply`,
  `post-share`, unsafe skips) require typed confirmation phrases — see
  `SECURITY.md §4`.

## CLI and qos_client platform

The preferred invocation is the self-contained `keyops` binary (no Python
required). On first use, fetch it with:

```bash
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')"
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/').sha256"
shasum -a 256 -c keyops.*.sha256
install -m 0755 keyops.* ./bin/keyops   # or any directory on $PATH
```

`keyops init --role coordinator ...` auto-fetches
the latest stable `qos_client` from `0xkey-io/qos` GitHub Releases on first
init (verified SHA256, no operator hash entry needed). When ceremony lock
requires a specific revision, pass `--qos-client-release-tag <tag>` and
communicate the same tag to all members. See
[references/qos-client-platform.md](references/qos-client-platform.md)
for the operator-client matrix, the in-ceremony version pin rule (one
ceremony, one `qos_client` revision), and the prerelease fallback.

## Provisioning matrix and exchange transport

- Pre-ceremony input/output expectations: [references/provisioning-matrix.md](references/provisioning-matrix.md).
- Bundle interface (the only contract this skill ships): `<name>-<stamp>.tgz`
  plus outer `.tgz.sha256`, with `BUNDLE.json` and `SHA256SUMS` inside. How the
  bundle travels (shared FS, S3, IM with file attachment, encrypted email,
  encrypted USB, private git repo) is the operator's choice.

## Version & update

This skill is version `0.5.1` (see the frontmatter at the top of this
file). Release notes and migration steps are in
[references/release-notes.md](references/release-notes.md). Always read
the entry for the version you are upgrading **into** before running any
ceremony commands — a BREAKING release may require a `keyops init
--force` migration. The Coordinator should also broadcast the new
version (and any migration step) to the Manifest / Share / Builder
members before they upgrade their own role workspaces.

Check the latest published version with `gh release view -R
0xkey-io/enclave-keyops-skills` (or, on a `git clone` install,
`git -C <skill-src> ls-remote --tags origin | tail -1`). Upgrade with
`npx skills update 0xkey-keyops-coordinator` (npm-style install) or
`git -C <skill-src> pull --tags` (clone install).

## Runbook

Read [references/roles/coordinator.md](references/roles/coordinator.md) for the
state-detection table, alias / member-index assignment workflow, and the full
phase A→J sequencing. Cross-check against the end-to-end ceremony narrative in
[WORKFLOWS.md](WORKFLOWS.md) — this skill is the only role package that ships
the full WORKFLOWS document, because the Coordinator is the only role that
orchestrates the whole ceremony.

Operator start prompt: [references/operator-prompts.md](references/operator-prompts.md).
