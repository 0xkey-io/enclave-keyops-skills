# Builder / Release Workflow

Use this file when the user says they are the builder, release operator, or the
person producing qOS / pivot / image artifacts for a KeyOps ceremony.

This role does not hold quorum `.secret` or `.share` material. It may need ECR,
S3, Docker, and build-system credentials, but it does not need to approve
manifests or re-encrypt shares.

## Goal

Produce verifiable artifacts that the coordinator and members can independently
check:

- `qos_client` release/reference binary and SHA256, plus any operator-native
  client builds needed by members (for example `darwin/arm64` for Mac operators)
- qOS release directory, especially `nitro.pcrs`
- five pivot binaries
- five pivot hash files
- ECR image tags / digests used by the K8s overlay

Builder has two modes:

1. **Prepare mode**: initialize the workspace and return a precise checklist of
   required source revisions, build commands, ECR targets, and artifact paths.
   Stop here if artifacts are not present yet.
2. **Verify/handoff mode**: once artifacts exist, compute hashes, verify required
   files, generate missing pivot hashes when possible, and write a handoff
   manifest for the Coordinator.

## Inputs The User Must Provide

- source revisions for the concrete component repositories or release bundle
  identifiers. Do not ask for a single parent / monorepo git SHA: the 0xkey
  workspace is not itself a Git repository. Typical source anchors are
  `repos/enclave`, `repos/services`, `repos/web`, the vendored qOS revision,
  the Docker image digest, or a signed release bundle hash.
- build instructions or release bundle
- target AWS account / ECR registry
- output workdir. If the user did not provide one, recommend
  `~/.0xkey-ops/builder`, then wait for confirmation before initializing.
- target environment name and release boundary

Do not ask for `.secret` or `.share` files.

## Execution Style

When source directories, ECR targets, and output paths are known, execute build,
hashing, verification, and handoff-generation commands directly, while briefly
stating the purpose of each command. Do not hand the user copy/paste commands as
the normal workflow.

Stop for user input only when required source revisions, registry/profile, output
paths, or policy confirmations are missing, or when a dirty source tree must be
accepted before it can be included in a release handoff.

## Access Scope

Builder may operate in the build source directories and the Builder `$WORKDIR`,
but should still avoid broad discovery:

- source directories explicitly named by the user, such as `repos/enclave` and
  `repos/services`
- `$WORKDIR/out/**`, `$WORKDIR/metadata/**`, and `$WORKDIR/logs/**`
- target ECR registries/repositories explicitly provided by Terraform output or
  the user

Do not search role workspaces for `.secret`, `.share`, public keys, approvals,
or wrapped shares. Builder does not need member key material. If source
revisions, ECR registry, or output directory are absent, report the missing
inputs rather than guessing from unrelated directories.

## ECR Naming

Use stable component repository paths such as `0xkey/coordinator`,
`0xkey/qos-enclave`, and `0xkey/qos_bridge`. Do not put the environment in the
repository path (for example, do not invent environment-prefixed component
repositories). The environment boundary is the AWS account / registry plus the
release tag and the recorded image digest. This keeps overlays compatible across
environments while changing registry, tag, and digest.

## Initialize Workspace

> `$SKILL_DIR` below is the absolute path of this skill on the agent's local
> filesystem. The agent that loaded this skill already knows it; resolve the
> placeholder before invoking Python.

By default `role_init.py` resolves and downloads the latest stable
`qos_client` from `0xkey-io/qos` GitHub Releases (the same channel
Builder publishes to) into `$WORKDIR/out/qos_client.<host-platform>`,
verifying it against the published SHA256 sidecar. This gives the
Builder workspace an immediately-runnable reference client — useful for
sanity-checking pivot hashes against the previous release before the new
build is published.

If `$WORKDIR` is missing from the prompt, recommend
`~/.0xkey-ops/builder` and ask the user to confirm or override it before
running `role_init.py`.

```bash
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role builder \
  --root "$WORKDIR"
```

Optional flags:

- `--qos-client-release-tag <tag>` to pin a specific previous release
  rather than resolving `latest`.
- `--no-qos-client-fetch` to scaffold the workspace without fetching;
  `out/qos_client.<plat>` will be produced by the build itself instead.

After init, `$WORKDIR/out/` exists but is empty except for `out/README.md`,
`out/qos-release/README.md`, and (unless you passed `--no-qos-client-fetch`)
the auto-fetched `out/qos_client.<host-platform>` reference client. That
is the *prepare-needed* baseline, not an error — Builder is ready to
receive source revisions / ECR config and then build.

Expected output layout (Builder ONLY — Coordinator / Manifest / Share
workspaces use `shared/` instead of `out/`):

```text
$WORKDIR/
  config.json
  out/                                # all Builder products live here
  out/qos_client                      # release/reference client
  out/qos_client.sha256
  out/qos_client.<platform>           # optional native operator clients
  out/qos_client.<platform>.sha256
  out/qos-release/nitro.pcrs
  out/qos-release/aws-x86_64.pcrs
  out/pivots/signer
  out/pivots/policy-engine
  out/pivots/notarizer
  out/pivots/tls-fetcher
  out/pivots/transaction-parser
  out/pivot-hashes/<service>-pivot-hash.txt
  out/images.json                     # ECR tag + digest per image
  out/builder-handoff.{json,md}       # final handoff to Coordinator
  metadata/                           # source revs, build logs, audit
  logs/
```

## State Detection

Before building or handing off, inspect the Builder `$WORKDIR` and classify.
The `config.json` row is the only true "uninitialized" signal; an `out/`
tree that exists but is empty is `prepare-needed` (build inputs missing) or
`build-needed` (inputs known, artifacts not produced yet), not
`uninitialized`.

| State | Directory evidence | Next action |
|-------|--------------------|-------------|
| `uninitialized` | missing `config.json` | run `role_init.py --role builder --root "$WORKDIR"` |
| `prepare-needed` | `config.json` present, no source revision metadata or ECR target recorded under `metadata/` | ask for component revisions, registry, tag, and output root |
| `build-needed` | source / ECR known, missing `out/qos_client`, `out/qos-release/nitro.pcrs`, any pivot binary, any pivot hash, or `out/images.json` | run the relevant build/push checklist |
| `handoff-ready` | all required artifacts and `out/images.json` exist, `out/builder-handoff.{json,md}` not yet written | verify hashes and write `builder-handoff.{json,md}` |
| `handoff-published` | `out/builder-handoff.{json,md}` written and shipped to Coordinator | summarize handoff and stop |
| `blocked` | dirty source without recorded diff, failed build, missing ECR digest, or mixed tags | report blocker and stop |

Every Builder output should include the current state, found artifacts, missing
artifacts, and the next safe action.

Builder does not run K8s, but the Coordinator may quote `/qos/enclave-health`
states when explaining why a hotfix release is needed:

| `enclave-health` state | What it means for this role |
|------------------------|----------------------------|
| `WaitingForBootInstruction` | Coordinator is preparing to boot; if a release fix is needed it must land before `boot-genesis` / `boot-standard` |
| `GenesisBooted` / `WaitingForQuorumShards` | Mid-ceremony; do **not** publish a new operator-client mid-ceremony unless the active ceremony is being aborted |
| `QuorumKeyProvisioned` | Steady state; safe window to publish a new operator-client release for the next ceremony |

## Required Checks

Before handing artifacts to the coordinator:

- record component source revisions / release bundle hash. Do not invent a
  single parent-monorepo SHA when the workspace root is not a Git repository.
- record build command line
- record Docker image digest for every image pushed, including `qos-host`,
  `qos-enclave`, `qos_bridge`, and backend services when they are part of the
  release
- compute SHA256 for `qos_client`
- compute pivot hashes using the same `qos_client`
- ensure `nitro.pcrs` and `aws-x86_64.pcrs` are present
- never embed environment secrets into images

## Build / Push Checklist (prod, default)

> The default env is **prod**. The Builder agent
> must NOT invent an AWS account / region / registry. Treat these as
> required inputs to be collected from the org's deployment runbook before
> any build runs.

Required fields the operator must supply (record under `metadata/build-config.json`):

| Field | Example | Source |
|-------|---------|--------|
| `env` | `prod` | operator |
| `aws_account_id` | `123456789012` | deployment runbook |
| `aws_region` | `ap-southeast-1` | deployment runbook |
| `ecr_registry` | `123456789012.dkr.ecr.ap-southeast-1.amazonaws.com` | derived from account + region |
| `enclave_repo_ref` | `repos/enclave@<git-sha>` | operator |
| `qos_vendored_ref` | `repos/enclave/vendor/qos@<git-sha>` | derived from enclave_repo_ref |
| `services_repo_ref` | `repos/services@<git-sha>` | operator |
| `target_platforms[]` | `["linux/amd64", "darwin/arm64"]` | derived from member-roster operator platforms (Coordinator answers) |
| `tag` | `prod-<yyyymmdd>-<short-sha>` | operator (must encode the release identity) |

With those fields fixed, a Builder run does:

1. Build qOS under `repos/enclave/vendor/qos` at the pinned `qos_vendored_ref` with `make out/common/index.json`, `make out/.common-loaded`, and `make default`.
2. Extract `qos_client`, `nitro.eif`, `nitro.pcrs`, and `aws-x86_64.pcrs` into `$WORKDIR/out/` and `$WORKDIR/out/qos-release/`.
3. For each entry in `target_platforms[]`, build the native `qos_client.<platform>` from the same `qos_vendored_ref` and write its `.sha256` next to it. Apple-Silicon members require `darwin/arm64` as a hard prerequisite — never ask a Mac operator to run `linux/amd64` via Rosetta against a `.secret` path.
4. Push qOS images to `${ecr_registry}/0xkey/qos-host:${tag}` and `${ecr_registry}/0xkey/qos-enclave:${tag}`.
5. Build the five pivots with `make pivot-build` (one per service: signer / policy-engine / notarizer / tls-fetcher / transaction-parser), then generate five `out/pivot-hashes/<service>-pivot-hash.txt` files using the **same** `qos_client` binary produced in step 2.
6. Build and push `${ecr_registry}/0xkey/qos_bridge:${tag}`; the image must contain both `/qos_bridge` and `/qos_net`.
7. Build and push release backend images such as `0xkey/coordinator`, `0xkey/registrar`, `0xkey/api-gateway`, `0xkey/dashboard-gateway`, and `0xkey/auth-proxy` when included in the release (same `${ecr_registry}` / `${tag}` pair).
8. Query digests with `aws --region "${aws_region}" ecr describe-images --repository-name 0xkey/<component> --image-ids imageTag=${tag}` and write a unified `out/images.json` (one entry per pushed component).
9. Write `out/builder-handoff.json` (machine-readable) and `out/builder-handoff.md` (human-readable). Both MUST include every `metadata/build-config.json` field above PLUS: `qos_client.sha256` per platform, all five pivot-hashes, both PCR file hashes, and every image digest from `out/images.json`. Do not ship a handoff that omits any of these.


On Apple Silicon, a linux/amd64 `qos_client` cannot execute directly. Best
practice is to ship a small operator-client matrix from the same qOS revision:
`linux/amd64` for release/reference and cluster/jumpbox use, `darwin/arm64` for
Mac operators, and optionally `linux/arm64` if operators use ARM Linux. For
non-production rehearsals, a fixed-digest `docker run --platform linux/amd64` wrapper is acceptable
when a native client is unavailable, but it should mount only the role workdir and
external key-vault paths needed for the command.

**YubiKey support is mandatory** on every operator-client binary
Builder publishes for a prod ceremony. The 0xkey prod path expects
`qos_client` to accept `--yubikey` on these four subcommands:
`provision-yubikey`, `approve-manifest`, `proxy-re-encrypt-share`,
`after-genesis`. The current prod release
(`sha256:84fce156f2a54a3aeb446c2600fd48e179ee3267a5d570e356f084aaac3082f4`,
end-to-end-verified on 2026-05-16) satisfies this; future revisions must
preserve it. If `--yubikey` regresses, Coordinator's `doctor holder` and
every Member's first-turn vault-mode question will surface it
immediately and the ceremony will be forced back onto file-mode keys
until Builder publishes a fix — flag the regression in the
release-handoff `notes[]` so Coordinator can decide whether to abort or
fall back. The `--yubikey` path assumes default PIV TDES Management Key
(see `SECURITY.md §5.2.1`); Builder does not need to ship a separate
"YubiKey edition" client, but **must** include in the release notes the
PIV-state preconditions members are expected to satisfy before
`provision-yubikey`.

When the ceremony participants use Apple Silicon Macs, treat the native
`darwin/arm64` operator client as a required Builder deliverable for production.
Build it from the same reviewed qOS revision as the release/reference client,
record the exact build command and local toolchain version, and publish the
binary together with its SHA256. The simplified distribution model is acceptable:
send the binary and checksum through an agreed controlled channel, then require
each operator to verify the SHA256 before putting the binary into their role
workspace. Signatures are useful but not required for this simplified path. Each
role config should pin the SHA256 of the client that role will actually execute,
not a cross-platform reference binary that cannot run on the operator's machine.

## Operator-client release channels

The operator client (`qos_client` running on each member / Coordinator
machine) is **not** bundled with this skill. Builder publishes a per-ceremony
release at a fixed, trusted location and tells the Coordinator that location
in the builder-handoff. Layout:

```text
qos-client-release-<qos-revision>/
  qos_client.linux-amd64
  qos_client.linux-amd64.sha256
  qos_client.darwin-arm64
  qos_client.darwin-arm64.sha256
  qos_client.linux-arm64           # optional; only when needed
  qos_client.linux-arm64.sha256    # optional; mirrors the binary
  MANIFEST.json                    # qOS revision, build commit, produced-at, related PCR
```

Trusted channel options, in order of preference:

1. **GitHub Releases on `0xkey-io/qos` (default)**. Tag scheme
   `0xkey-qos_client-vMAJOR.MINOR.PATCH`. The
   `.github/workflows/0xkey-qos-client-release.yml` workflow on that fork:
   - builds linux/amd64 via the upstream stagex buildx pipeline (same
     `make out/qos_client/index.json` target the rest of the qOS release uses,
     bit-for-bit reproducible with `SOURCE_DATE_EPOCH=1`);
   - builds darwin/arm64 natively on a `macos-14` runner with
     `cargo build --release --locked --features smartcard --target aarch64-apple-darwin`
     (reproducible at the toolchain level: same runner image + `rust-toolchain.toml`
     + same git tag → same binary, but not byte-for-byte across runner upgrades);
   - uploads `qos_client.<platform>`, `qos_client.<platform>.sha256`, and
     `MANIFEST.json` to the release;
   - generates a SLSA build-provenance attestation (`actions/attest-build-provenance@v2`)
     so any consumer can verify the binary came from a specific workflow run on
     `0xkey-io/qos` via `gh attestation verify`.

   Verification is one command per platform:

   ```bash
   gh release download "$TAG" -R 0xkey-io/qos -p 'qos_client.*' -p 'MANIFEST.json'
   shasum -a 256 -c qos_client.linux-amd64.sha256
   shasum -a 256 -c qos_client.darwin-arm64.sha256
   gh attestation verify qos_client.linux-amd64  -R 0xkey-io/qos
   gh attestation verify qos_client.darwin-arm64 -R 0xkey-io/qos
   ```

2. Internal artifact server / S3 with signed URL (IAM-controlled). Use
   when GitHub Releases are unavailable (e.g. air-gapped network) or for
   per-customer mirrors.
3. ECR as OCI artifact, alongside the enclave images (same trust domain).
4. Encrypted package + IM/email **only** when the above are unavailable;
   the recipient must independently verify the SHA256 and qOS revision
   before use.

Hard rules:

- **Never** commit a `qos_client` binary into the skill repo (or any git repo
  the agent can read; that defeats SHA256 audit).
- **Never** ship the binary over an unsigned plain HTTP mirror or via a chat
  attachment without the matching `.sha256`.
- **Never** mix qOS revisions inside one ceremony; if a revision change is
  unavoidable, abort the in-progress ceremony first and start a fresh one.

See `SECURITY.md §3` for the trigger table that says **when** Builder must
re-publish a release.

### builder-handoff schema additions

When channel #1 is used, the `builder-handoff.json` MUST include a
`qos_client_release` object so Coordinator and members can wire the
`fetch_qos_client.py` helper without guessing paths:

```jsonc
{
  // ... existing fields ...
  "qos_client_release": {
    "channel": "github_releases",
    "repo": "0xkey-io/qos",
    "tag": "0xkey-qos_client-v0.1.0",
    "manifest_url":
      "https://github.com/0xkey-io/qos/releases/download/0xkey-qos_client-v0.1.0/MANIFEST.json",
    "manifest_sha256": "<hex from MANIFEST.json>",
    "platforms": {
      "linux-amd64":  { "sha256": "<hex>", "size": 12345678 },
      "darwin-arm64": { "sha256": "<hex>", "size": 12345678 }
    },
    "qos_revision": "<git-sha-of-fork-build>",
    "workflow_run_url": "https://github.com/0xkey-io/qos/actions/runs/<id>",
    "attestation_verify": "gh attestation verify qos_client.<plat> -R 0xkey-io/qos"
  }
}
```

When a non-default channel is used (#2 / #3 / #4), set `channel` accordingly
and replace `repo` / `tag` / `manifest_url` with the equivalent
fixed-location URL or storage identifier, plus a published `MANIFEST.json`
sha256 so consumers can verify the manifest itself before trusting any
field inside it.

## Output To User

Return a concise manifest of:

- artifact directory path
- `qos_client` SHA256
- pivot hash file paths
- ECR image digests
- operator-client release channel URL + qOS revision (so Coordinator can
  forward to members)
- any reproducibility caveats
