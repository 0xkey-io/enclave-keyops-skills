# Coordinator Workflow

Use this file when the user says they are the Deployment Coordinator,
Coordinator, ceremony operator, or the person applying manifests to EKS.

This is the only KeyOps role that should need AWS/EKS/kubectl access.

## Goal

Create canonical five-service manifests, distribute review/share-request
bundles, collect member outputs, apply the enclave K8s overlay, run
boot-standard / attestation / post-share, and verify control plane + data plane.

## Inputs The User Must Provide

Ask for paths and identifiers, not secret contents:

- `workdir`: repo-external directory. If the user did not provide one,
  recommend `~/.0xkey-ops/coordinator`, then wait for confirmation before
  initializing.
- `qos_client` and expected SHA256
- `qos-release/nitro.pcrs`
- five pivot binaries and pivot hashes
- `manifest-set/*.pub`, `share-set/*.pub`, `patch-set/*.pub`
- `quorum_key.pub`
- PCR3 role ARN or `pcr3-preimage.txt`
- K8s context allowlist / target EKS cluster
- member approval bundles in `inbox/`
- member wrapped-share bundles in `inbox/`

Coordinator must not hold member `.secret` files. During Genesis, Coordinator
may temporarily hold generated `.share` files only for distribution to Share Set
members; those shares must stay out of review/share-request bundles and should be
removed from the Coordinator workspace after distribution.

## Execution Style

When inputs are present, execute Coordinator commands directly and state the
purpose briefly before each action. Do not hand the user copy/paste commands as
the normal workflow.

Stop for user input only when required artifacts are missing, legacy/duplicate
materials require confirmation, the K8s context is not explicitly allowed, or a
human gate is reached (`kubectl apply`, `boot-standard`, `post-share`, unsafe
skips).

## Access Scope

Operate only inside the Coordinator `$WORKDIR`, the configured K8s context, and
exact paths the user explicitly provides. For local files, the expected current
round inputs are under:

- `$WORKDIR/shared/**`
- `$WORKDIR/inbox/**`
- `$WORKDIR/bundles/**`
- `$WORKDIR/outbox/**` or other output directories created by this role

Do not search `$HOME`, legacy key archives, old ceremony directories,
Builder outputs, or member workspaces for public keys, quorum keys, shares, or
bundles unless the user gives an exact path or explicitly authorizes importing
legacy material.

## Initialize Workspace

> All commands below use the self-contained `keyops` binary — no Python
> required. If `keyops` is not yet on `$PATH`, fetch it from GitHub
> Releases first:
>
> ```bash
> curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')"
> curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/').sha256"
> shasum -a 256 -c keyops.*.sha256
> install -m 0755 keyops.* ./bin/keyops    # or any directory on $PATH
> ```

By default `keyops init` resolves and downloads the latest stable
`qos_client` from `0xkey-io/qos` GitHub Releases (skipping prereleases),
verifies the SHA256 against the published sidecar, and installs the
binary at `$WORKDIR/shared/qos_client`. The Coordinator does not have to
remember a hash for first-init; the runtime SHA256 lands in
`config.json.qos_client_sha256_expected` automatically.

```bash
keyops init \
  --role coordinator \
  --root "$WORKDIR" \
  --account-id "$AWS_ACCOUNT_ID" \
  --region "$AWS_REGION" \
  --cluster "$EKS_CLUSTER" \
  --enclave-role-name "$ENCLAVE_NODE_ROLE_NAME" \
  --kustomize-overlay-path "$ENCLAVE_OVERLAY_ABSOLUTE_PATH"
```

Optional `qos_client` flags for non-default situations:

- `--qos-client-release-tag <tag>` — pin a specific release (e.g.
  `0xkey-qos_client-v0.1.0`) instead of resolving `latest`. Use this
  when ceremony lock requires a specific Builder revision; communicate
  the same tag to all members.
- `--qos-client-release-repo <owner/name>` — point at a private mirror.
- `--no-qos-client-fetch` — scaffold offline; the printed todo line
  carries the exact `keyops fetch-qos-client` command for follow-up.

Notes:
- The default assumption is **prod**.
- If `$WORKDIR` is missing from the prompt, recommend
  `~/.0xkey-ops/coordinator` and ask the user to confirm or override it before
  running `keyops init`.
- `--account-id`, `--region`, `--cluster`, `--enclave-role-name`, and
  `--kustomize-overlay-path` are required for `--role coordinator`.
- `--kustomize-overlay-path` MUST be an absolute path to the K8s overlay
  directory (e.g. `/Users/you/codes/0xkey/repos/enclave/deploy/k8s/overlays/prod`).
  Relative paths are rejected to keep the skill repo-layout-agnostic.

Then place non-secret inputs:

```text
$WORKDIR/
  shared/qos_client
  shared/qos-release/nitro.pcrs
  shared/qos-release/aws-x86_64.pcrs
  shared/pivots/<service>
  shared/pivot-hashes/<service>-pivot-hash.txt
  shared/manifest-set/*.pub
  shared/manifest-set/quorum_threshold     # single-line decimal int
  shared/share-set/*.pub
  shared/share-set/quorum_threshold        # single-line decimal int
  shared/patch-set/*.pub                   # OR a README disabling patch-set
  shared/patch-set/quorum_threshold        # if patch-set is enabled
  shared/member-roster.json                # alias + member-index assignments
  shared/quorum_key.pub                    # produced by Genesis on first ceremony
  shared/dr-key.pub                        # collected from external DR holder
  shared/pcr3-preimage.txt
  genesis-output/                          # filled by `ceremony genesis-boot`
```

## Alias / member-index assignment

`alias` and (for share-set) `member-index` are **assigned by the
Coordinator before any member submits a `.pub`** and become permanent for
the lifetime of the resulting `quorum_key`. Members never self-pick these
values — they only confirm what the Coordinator publishes. The skill
enforces this via `shared/member-roster.json`, which is a hard input to
`doctor coordinator`, `manifest generate`, and `ceremony genesis-boot`.

Why this matters:
- Two members with the same `alias` → `<alias>.pub` overwrite, approval
  files land in ambiguous slots, and Coordinator commands exit 2 with
  "found 0" / "found 2".
- Two share-set members with the same `member_index` → wrapped-share
  files collide on `member<n>_eph_wrapped.share`; `post_share_members_order`
  loses uniqueness; some services get the wrong share installed.
- Changing `alias` or `member_index` AFTER `ceremony genesis-boot` is
  impossible without redoing Genesis (the share is bound to the slot).

### Workflow

1. Build a roster of real people from the Genesis announcement (IM
   handles, emails, on-call rota — whatever your org uses to identify a
   single human).
2. Assign `(member_index, alias)` deterministically. The strong default
   is **`alias = "share-member<n>"`** so the alias and the index are the
   same number; pick the order from the announcement (alphabetical,
   submission order, lottery — pick one and document it).
3. Write `shared/member-roster.json` (template:
   `shared/member-roster.example.json`). Schema:

```json
{
  "ceremony": "0xkey-2026q2",
  "issued_at": "2026-05-15T12:00:00Z",
  "manifest_set": [
    {"alias": "manifester1", "owner": "Alice (alice@example.com)"}
  ],
  "share_set": [
    {"member_index": 1, "alias": "share-member1", "owner": "Alice"},
    {"member_index": 2, "alias": "share-member2", "owner": "Bob"}
  ],
  "patch_set": []
}
```

(Use a neutral `ceremony` id by default. Add an environment qualifier only
when the deployment runbook requires one.)

4. Broadcast the roster to all members (signed announcement, IM thread,
   email — anything tamper-evident). Each member confirms their assigned
   `(alias, member_index)` and runs `keyops init --alias <s>
   --member-index <n>` with exactly those values.
5. Members hand back `<alias>.pub` files. Coordinator drops them in
   `shared/manifest-set/`, `shared/share-set/`, `shared/patch-set/`
   respecting the same alias as the filename stem.
6. Run `doctor coordinator`. The roster gate enforces:
   - `<alias>.pub` filename stems exactly match aliases in the roster
   - no extra `.pub` files that aren't on the roster
   - share-set `member_index` values are a 1..N consecutive sequence
     with no gaps and no duplicates
   - aliases are filename-safe (`[A-Za-z0-9._-]`, ≤ 64 chars)
7. Roster ships inside `review`, `share-request`, and `genesis-output`
   bundles (file `member-roster.json` + `BUNDLE.json.members` slice for
   the relevant sets) so members can independently verify they were
   assigned the expected alias / index.

### After Genesis: replacing or adding members

A new member CANNOT take over an existing share-set slot by changing
their alias to match. Two recovery paths:

- **Same slot, new key**: run a `key-forward` ceremony for that
  `member_index`; the alias stays the same, the previous member's
  `.share` is invalidated.
- **New slot**: append a new `(member_index = N+1, alias = ...)` entry,
  re-run Genesis or whatever quorum-extension procedure your qOS revision
  supports. Never edit a historical roster entry; create a new
  `ceremony` field and a new roster file.

## Genesis / Pre-Material Collection

When the user asks only for Genesis or prerequisite collection, stop before
manifest generation, deploy, boot-standard, or post-share. The expected
collection outcome is:

- `shared/member-roster.json` published BEFORE collecting any `.pub`
  (alias + member-index assignments; see `Alias / member-index assignment`
  above)
- `shared/manifest-set/*.pub` plus `shared/manifest-set/quorum_threshold`
- `shared/share-set/*.pub` plus `shared/share-set/quorum_threshold`
- either `shared/patch-set/*.pub` plus `quorum_threshold`, or a clear
  `shared/patch-set/README.md` stating patch-set is disabled
- `shared/pcr3-preimage.txt` containing the exact role ARN bytes with no newline
- `shared/dr-key.pub` containing the DR (disaster-recovery) public key from the
  external DR holder; verify shape (260 hex characters representing two
  uncompressed SEC1 P-256 points, `04...04...`), and verify that the DR
  private key / master seed is held only in an external vault (YubiKey / HSM
  / encrypted disk image). If the user does not have a DR key yet, **stop and
  ask** — never run `key file-generate` against a workdir-internal path for
  the DR key, and never accept a DR key whose private half lives anywhere
  inside this workspace.
- `shared/public-key-sets.md` inventory with source paths, thresholds, missing
  members, and validation notes

For a clean multi-role rehearsal, accept member public keys only from one of
these sources:

- paths explicitly provided by the user in the prompt
- current-round role workspaces, for example
  `~/.0xkey-ops/manifest-set/manifesterN/outbox/*.pub` or
  `~/.0xkey-ops/share-set/share-memberN/outbox/*.pub`
- an explicit current-round Coordinator inbox, for example
  `$WORKDIR/inbox/public-keys/<alias>.pub`

Do not broadly search `$HOME`, legacy key archives, old ceremony
directories, or previous build outputs for `*.pub` and silently import them. If
such files are found during investigation, report them as possible legacy
material and ask the user before copying anything into `shared/`.

Public-key validation should check qOS public key shape: 260 hex characters,
representing two uncompressed SEC1 P-256 points (`04...04...`). Also report
duplicate public key material across aliases or sets. Duplicate keys require explicit human confirmation before the ceremony is
treated as ready.

Do not reuse an old `quorum_key.pub` silently. If `quorum_key.pub` comes from a
previous legacy ceremony, record it as an imported legacy quorum key and ask for
confirmation. For a new account / new Genesis ceremony, prefer generating a new
quorum key and `.share` set from the current members.

`doctor coordinator` requires `shared/qos_client`, so it may fail early during
pure public-key collection. In that case, report the missing `qos_client` as a
later Builder dependency rather than continuing into Coordinator deploy steps.

## State Detection

Before running commands, inspect only `$WORKDIR` and classify:

| State | Directory evidence | Next action |
|-------|--------------------|-------------|
| `uninitialized` | missing `config.json` | run `keyops init` with account/region/cluster |
| `waiting-for-qos-client` | missing `shared/qos_client` or `config.json.qos_client_sha256_expected` | re-run `keyops init` (default = auto-fetch latest from `0xkey-io/qos`, the same channel Builder publishes to); on offline machines run `keyops fetch-qos-client --release-tag latest --out $WORKDIR/shared/qos_client`, then re-run `keyops init --force` to record the verified hash. Do NOT download from random mirrors and do NOT reuse a different ceremony's binary. |
| `missing-roster` | no `shared/member-roster.json` or it doesn't list every set member | publish or update the roster (see `Alias / member-index assignment`); members must not submit `.pub` files until roster exists |
| `collecting-genesis-materials` | roster published but missing current-round public-key sets, `quorum_threshold`, PCR3 preimage, or `shared/dr-key.pub` | ask for exact member `.pub` paths (filename = roster alias + `.pub`), the DR public key, or current-round inbox files |
| `waiting-for-builder-artifacts` | public-key sets exist, missing qOS PCR, pivots, pivot hashes, or images.json | ask Builder for handoff artifacts |
| `ready-for-genesis` | public-key sets, thresholds, DR pub, qos_client all present, no `shared/quorum_key.pub` yet | run `ceremony genesis-boot`, then bundle and ship `genesis-output` to Share members |
| `ready-for-manifest` | public-key sets, Builder artifacts, and `shared/quorum_key.pub` are present | run doctor, nonce checks, then manifest generation |
| `waiting-for-approvals` | review bundle exists, not enough approvals | wait for Manifest Set member approval bundles |
| `ready-for-deploy` | manifest envelopes exist | render/apply only after user confirmation |
| `waiting-for-wrapped-shares` | share-request exists, wrapped shares missing | wait for Share Set member wrapped-shares bundles |
| `ready-to-post-share` | wrapped shares satisfy threshold | post-share only after user confirmation |
| `blocked` | legacy material not confirmed, duplicate key not confirmed, threshold not met, bad checksum, or wrong K8s context | report blocker and stop |

Every Coordinator response should include current state, found materials, missing
materials, and one next safe action.

### enclave-health → ceremony phase mapping

`/qos/enclave-health` exposes a small state machine. When the Coordinator
runs into trouble during boot / post-share / verify, look up the reported
state here to find the next safe action:

| `enclave-health` state | Ceremony phase reached | Next coordinator action |
|------------------------|-----------------------|-------------------------|
| `WaitingForBootInstruction` | Genesis target / data-plane pod is up but neither `boot-genesis` nor `boot-standard` has been issued | First-time ceremony: run `ceremony genesis-boot`. Otherwise (rotation, redeploy): run `ceremony boot` |
| `GenesisBooted` | `boot-genesis` succeeded; per-member encrypted shares + `quorum_key.pub` were produced | Bundle `genesis-output`, ship to Share members, wait for their `share-extract` confirmation |
| `WaitingForQuorumShards` | `boot-standard` finished and the enclave is ready to ingest re-encrypted shares | Run `ceremony attestation`, ship the share-request bundle, then `ceremony post` once the threshold of wrapped shares returns. Note: app containers will crash-loop (30+ restarts) because the quorum key is not yet provisioned — this is expected. Do **not** delete Pods. |
| `QuorumKeyProvisioned` | `post-share` completed for this service | Run `verify` (control plane + `:8081/health` + business smoke); only consider the ceremony done when all five services reach this state |

## Run The Role

The commands below describe the implementation sequence. In an agent session,
execute them directly when inputs are present and the step is not blocked by a
human gate; do not ask the user to copy/paste them.

Health check:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  doctor coordinator
```

### Genesis (first ceremony only or when rotating the Quorum Key)

Skip this entire sub-section if `shared/quorum_key.pub` already exists from a
prior ceremony. Otherwise:

1. Confirm the Genesis enclave target is up (this is environment infra and is
   stood up outside this skill; ask the user to confirm the Nitro device
   plugin, the namespace, and the qos-genesis Deployment are healthy).
2. Confirm `shared/dr-key.pub` is the real DR public key and not the
   placeholder file. The DR private key MUST live in an external vault and
   never enter this workspace.
3. Confirm each set under `shared/{manifest-set,share-set,patch-set}/` has a
   `quorum_threshold` file (single-line decimal integer). Recommended values
   live in SECURITY.md threshold recommendations.
4. Run `boot-genesis`:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony genesis-boot --genesis-endpoint "$GENESIS_ENDPOINT"
# OR (when running against a Genesis Deployment in the same EKS cluster):
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony genesis-boot --resolve-pod-ip --genesis-label app=qos-genesis
```

5. Copy `genesis-output/quorum_key.pub` to `shared/quorum_key.pub`.
6. Bundle and distribute the Genesis output to every Share Set member:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle create --kind genesis-output \
  --bundle-dir "bundles/genesis-output-${STAMP}" \
  --archive "bundles/genesis-output-${STAMP}.tgz"
shasum -a 256 "$WORKDIR/bundles/genesis-output-${STAMP}.tgz" \
  > "$WORKDIR/bundles/genesis-output-${STAMP}.tgz.sha256"
```

Wait for each Share member to confirm they ran `ceremony share-extract`
successfully before proceeding to manifest generation. The transport channel
is operator's choice (see SKILL.md `Exchange transport`).

### Manifest

Before generating manifests, ensure:

- `config.json` has `manifest_nonce` set to a non-null integer for every service.
  The template ships `null` intentionally (a safety property that forces a
  conscious first value). For the first ceremony set each service's
  `manifest_nonce` to `0`. For subsequent ceremonies increment by 1.
- `shared/patch-set/quorum_threshold` exists. If the patch-set is disabled,
  create the file with content `0`.

Generate canonical manifests:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  manifest generate
```

Create and distribute review bundle:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle create --kind review \
  --bundle-dir "bundles/manifest-review-${STAMP}" \
  --archive "bundles/manifest-review-${STAMP}.tgz"
shasum -a 256 "$WORKDIR/bundles/manifest-review-${STAMP}.tgz" \
  > "$WORKDIR/bundles/manifest-review-${STAMP}.tgz.sha256"
```

Tell the user to send the review `.tgz` and `.sha256` to Manifest Set members.

After approval bundles return, extract and verify each bundle, then ensure the
`manifest/approvals/<service>/` directories contain exactly this round's
approvals. The script rejects approvals that do not match alias + service
namespace + nonce.

Create envelopes:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  manifest envelope
```

Render/apply K8s:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" deploy render

# After user approval:
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" deploy apply
```

### Network Topology

Choose one connectivity mode before running `ceremony boot`, `ceremony attestation`, and `ceremony post`:

| Scenario | Configuration |
|---|---|
| Running from within the cluster (jumpbox with Pod network routing) | Use `--resolve-pod-ip` on every command; no extra config needed |
| Running from a local machine via `kubectl` | Set `"host_ip": "127.0.0.1"` in each service entry in `config.json`, then open a port-forward for each service before running the command |

Port-forward example (run one per service in background terminals):

```bash
kubectl -n "$K8S_NAMESPACE" port-forward   "$(kubectl -n "$K8S_NAMESPACE" get pod -l app=<service> -o jsonpath='{.items[0].metadata.name}')"   <host_port_qos>:<host_port_qos>
```

When `host_ip` is set in `config.json`, omit `--resolve-pod-ip` from all ceremony commands.

Boot and attest:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony boot --resolve-pod-ip

keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony attestation --resolve-pod-ip
```

Create and distribute share-request bundle:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle create --kind share-request \
  --bundle-dir "bundles/share-request-${STAMP}" \
  --archive "bundles/share-request-${STAMP}.tgz"
shasum -a 256 "$WORKDIR/bundles/share-request-${STAMP}.tgz" \
  > "$WORKDIR/bundles/share-request-${STAMP}.tgz.sha256"
```

After wrapped-share bundles return, extract and verify each bundle:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle extract --install --archive "inbox/<member>-wrapped-shares-<stamp>.tgz"
```

`bundle extract --install` automatically places wrapped shares into
`wrapped-shares-coordinator/<service>/` and merges the share-set approvals into
`manifest/approvals/<service>/`.

Post-share pre-flight checklist (verify before running `ceremony post`):

- [ ] Pods have **not** been deleted or recreated since `ceremony attestation`
      (container restarts are safe; Pod delete/recreate is not)
- [ ] `wrapped-shares-coordinator/<service>/` contains `member<n>_eph_wrapped.share`
      for at least the share threshold count of members
- [ ] `manifest/approvals/<service>/` contains at least one approval with a
      `share-*` filename prefix (share-set approval) for every service
- [ ] Network connectivity confirmed (port-forward running or `--resolve-pod-ip` reachable)

> **NOTE**: `--approval-alias` for `ceremony post` must be a **share-set** alias
> (e.g. `share-torben`), not a manifest-set alias. Using a manifest-set alias
> causes `NotShareSetMember` because the enclave verifies the approval signer
> against `shareSet.members[]` in the manifest.

Post shares:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony post \
  --resolve-pod-ip \
  --approval-alias "$APPROVAL_ALIAS" \
  --post-global-order "$POST_ORDER"
```

Use the documented post-share order from the deployment runbook unless the
active ceremony config says otherwise.

Verify:

```bash
keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR" verify
```

Verification must include:

- Pod Ready
- `/qos/enclave-health` says `QuorumKeyProvisioned`
- `:8081/health`
- business POST smoke for all five pivots

Verification should not exec `sh` or `curl` inside `qos-host` or `app-bridge`;
those containers may be minimal images without shell tooling. Use the skill
`verify` command, which performs temporary `kubectl port-forward` checks from
outside the containers, or use an explicit jumpbox / local port-forward fallback
with equivalent HTTP checks.

## Troubleshooting

| Error | Cause | Resolution |
|---|---|---|
| `ProtocolErrorResponse(DecryptionFailed)` on `ceremony post` | Pod was deleted/recreated after attestation; ephemeral key destroyed | Redo: `ceremony attestation` → new share-request bundle → Share members reencrypt → collect wrapped-shares bundles → `ceremony post` |
| `NotShareSetMember` on `ceremony post` | `--approval-alias` is a manifest-set alias; enclave checks shareSet membership | Use the share-set alias (e.g. `share-torben`) for `--approval-alias` |
| `expected exactly one approval ... found 0` for share-set alias | The wrapped-shares bundle did not carry the member's share-set approval (older keyops, or `--approval-alias` does not match the member's `--alias`) | Confirm the bundle was built with keyops >= 0.5.6; the share-set approval is named `<member-alias>-<namespace>-<nonce>.approval`. Use that member alias for `--approval-alias` |
| `InvalidCertChain(CertExpired)` on `ceremony share-extract` | genesis_attestation_doc leaf cert expired (3-hour validity window) | Ask Share members to add `--validation-time-override <genesis-boot-UTC-timestamp>` to `ceremony share-extract` |
| `Connection Failed / connection timed out` on ceremony commands | Using `--resolve-pod-ip` from outside the cluster network | Switch to `host_ip` + `kubectl port-forward` (see Network Topology section) |

## Output To User

Report:

- review bundle paths
- share-request bundle paths
- final `verify` result
- any service that failed control-plane or data-plane checks
- coordinator cutover values, especially `:8081/v1/<svc>` URLs
