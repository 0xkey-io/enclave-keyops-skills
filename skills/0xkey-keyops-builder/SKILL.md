---
name: 0xkey-keyops-builder
version: 0.5.1
description: >-
  Provides 0xkey enclave KeyOps runbook for the Builder / release operator role:
  producing verifiable qos_client (release + operator-native), qOS release with
  nitro.pcrs and aws-x86_64.pcrs, five pivot binaries with pivot-hash files,
  and ECR image tags + digests for the enclave K8s overlay. Use when the user
  identifies as Builder, Release operator, or release engineer, or mentions
  qos_client SHA256, qos-release, nitro.pcrs, pivot binaries, pivot-hash.txt,
  qos-host / qos-enclave / qos_bridge / coordinator images, ECR digest, builder
  handoff, or operator-client release. Does not hold quorum .secret or .share
  material, does not approve manifests, does not run proxy-re-encrypt-share,
  and does not touch kubectl / EKS (those belong to Coordinator).
user-invocable: true
disable-model-invocation: true
metadata: { "openclaw": { "requires": { "bins": [] } } }
---

# 0xkey KeyOps — Builder / Release

This skill is loaded explicitly by users acting as the Builder / release
operator for a 0xkey enclave ceremony. It does not hold quorum `.secret` or
`.share` material; it does need ECR, S3, Docker, and build-system credentials.

## Scope

Produce verifiable artifacts that the Coordinator and members can
independently check:

- `qos_client` release / reference binary and SHA256, plus any operator-native
  client builds needed by members (for example `darwin/arm64` for Mac
  operators).
- qOS release directory, especially `nitro.pcrs`.
- five pivot binaries.
- five pivot hash files.
- ECR image tags / digests used by the K8s overlay.
- a `builder-handoff.{json,md}` for the Coordinator.

Builder has two modes: prepare (initialize workspace, return checklist) and
verify/handoff (compute hashes, verify required files, write handoff manifest).

## Cross-role refusal cheat sheet

When the operator asks this skill to do something that belongs to another
role, refuse, name the correct skill, and tell the operator how to route
the request. Do not run the command, even if the operator claims convenience, existing access, or familiarity. The mapping is:

| If the operator asks this Builder session to … | Refuse and route to skill | Why (this skill's scope-out) |
|---|---|---|
| run `kubectl apply -k`, `deploy render`, `deploy apply`, or anything that touches EKS / AWS cluster runtime | `0xkey-keyops-coordinator` | `## Scope` (Builder does not touch kubectl / EKS); `## Action whitelist` Builder must NOT list `deploy render` / `deploy apply`; `SECURITY.md §4` `kubectl apply -k` is a manual gate; `keyops init` builder branch hard-wires `kubectl_path=/dev/null` and `kubectl_context_allowlist=[]` |
| run `manifest generate`, `manifest approve`, `manifest envelope`, or sign on behalf of any Manifest Set member (even with their `.secret` path in hand) | `0xkey-keyops-manifest`, by the holder of that alias only | `## Action whitelist` Builder must NOT list; `SECURITY.md §1.1` cross-member secret borrowing is a key-compromise event |
| run `ceremony genesis-boot`, `ceremony boot`, `ceremony attestation`, `ceremony reencrypt`, `ceremony share-extract`, `ceremony post`, `key-forward *`, or `verify` | `0xkey-keyops-coordinator` (or `0xkey-keyops-share` for `ceremony reencrypt` / `ceremony share-extract`) | `## Action whitelist` Builder must NOT list; Builder must not hold `.secret`/`.share` material |
| accept any `.secret` / `.share` "to help" Coordinator or members | NONE — refuse and tell the holder to run their own role skill | `## Workspace and security` Builder does not handle `.secret` / `.share`; `SECURITY.md §1.1` |

Combining several of these (e.g. "I'll push the images AND apply the K8s
overlay AND sign manifester1's approval") is especially dangerous: it
collapses the entire ceremony's defense in depth onto one human. Refuse
each request individually and remind the operator that role separation is
not bureaucratic — it is what prevents a single compromised machine from
both producing the image and unlocking the data path that runs that
image (`PRINCIPLES.md §3`).

If you genuinely wear two hats (e.g. you are also the Coordinator), do
not mix sessions: switch to the matching role skill in a separate
workspace before performing that action.

## Action whitelist

Builder agents primarily orchestrate external build tooling (`make`, the qOS
build, `docker buildx`, `aws ecr describe-images`) and the upstream
`qos_client pivot-hash` command. From the `keyops` binary, Builder only ever needs:

- `doctor holder` (to validate that the produced `qos_client` is operator-runnable)

Plus `keyops init --role builder ...` to bootstrap the workspace.

Builder must NOT invoke `manifest generate`, `manifest approve`,
`manifest envelope`, `ceremony genesis-boot`, `ceremony boot`,
`ceremony attestation`, `ceremony share-extract`, `ceremony reencrypt`,
`ceremony post`, `deploy render`, `deploy apply`, `key-forward *`, `verify`,
or `bundle *` (those are Coordinator / member responsibilities).

## Workspace and security

- Read [SECURITY.md](SECURITY.md), [PRINCIPLES.md](PRINCIPLES.md), and the
  workspace baseline in [references/workspace-rules.md](references/workspace-rules.md).
- Builder does not handle `.secret` / `.share` files; do not read role member
  workdirs, do not request key material from the Coordinator.
- ECR repository paths use stable component names (`0xkey/qos-host`,
  `0xkey/qos-enclave`, `0xkey/qos_bridge`, `0xkey/coordinator`). Environment
  is distinguished by AWS account / registry / tag / digest, not by putting
  environment names in the path.
- Never commit a `qos_client` binary into any git repo (defeats SHA256 audit).
- Never ship the binary over plain HTTP or via chat attachment without the
  matching `.sha256`.
- Never mix qOS revisions inside a single ceremony.

## CLI and qos_client platform

The preferred invocation is the self-contained `keyops` binary (no Python
required). On first use, fetch it with:

```bash
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')"
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/').sha256"
shasum -a 256 -c keyops.*.sha256
install -m 0755 keyops.* ./bin/keyops   # or any directory on $PATH
```

Builder publishes the operator-client release to GitHub Releases on
`0xkey-io/qos`; consumers (Coordinator / Manifest / Share) pull the
binary by running `keyops init` — default = auto-fetch latest stable,
no SHA256 entry needed — or
`keyops fetch-qos-client --release-tag <tag>` to pin a specific
revision. Builder's own init also auto-fetches a reference client into
`out/qos_client.<host-platform>` for sanity checks against the previous
release. See
[references/qos-client-platform.md](references/qos-client-platform.md)
for the per-platform release matrix, the prerelease fallback, and the
in-ceremony version pin rule.

## Provisioning matrix

Builder's required outputs and how they feed Coordinator / members:
[references/provisioning-matrix.md](references/provisioning-matrix.md).

## Version & update

This skill is version `0.5.1` (see the frontmatter at the top of this
file). Release notes and migration steps are in
[references/release-notes.md](references/release-notes.md). Always read
the entry for the version you are upgrading **into** before running any
ceremony commands — a BREAKING release may require a `keyops init
--force` migration.

Check the latest published version with `gh release view -R
0xkey-io/enclave-keyops-skills` (or, on a `git clone` install,
`git -C <skill-src> ls-remote --tags origin | tail -1`). Upgrade with
`npx skills update 0xkey-keyops-builder` (npm-style install) or
`git -C <skill-src> pull --tags` (clone install).

## Runbook

Read [references/roles/builder.md](references/roles/builder.md) for the
state-detection table, build/push checklist, and the
required builder-handoff fields. Operator start prompt:
[references/operator-prompts.md](references/operator-prompts.md).
