# Enclave KeyOps Security And Audit Rules

This document defines the production-ready safety rules that agents and human
operators must follow. Any violation should stop the ceremony and escalate to
human review.

## 1. Key Material Classification

| Material | Sensitivity | Git / chat / CI logs |
|---|---|---|
| `.secret`, `master-seed` | Highest | Never upload or paste |
| `.share`, ephemeral wrapped share | Highest | Same as `.secret` |
| `dr.secret` / `dr-master-seed` (DR private key) | Highest | Never upload; must stay in an external vault outside every role workdir |
| `*.approval` | High | Controlled encrypted archive only |
| `*-manifest.json` / envelope | Medium-high | Controlled storage; multi-party review before release |
| `quorum_key.pub`, `*.pub` | Low | May be distributed to reviewers |
| `dr-key.pub` (DR public key) | Low | Required Genesis input; same class as `quorum_key.pub` |
| `nitro.pcrs`, `pivot-hash.txt` | Low | Build artifacts; must match the reviewed image set |

The disaster-recovery key is the last-resort quorum recovery credential. An
independent DR holder generates it in an external vault and gives only
`dr-key.pub` to the Coordinator for `ceremony genesis-boot`. DR private-key
exposure is quorum compromise. This skill does not model a DR-holder workspace
and provides no command that reads, copies, or transports the DR private key.

## 1.1 Holder-Only Secret And Share Use

Each `member-roster.json` row maps one `(alias, member_index?)` assignment to
exactly one human operator. That operator alone may hold and use the `.secret`
and `.share` for that alias in their external vault.

Cross-member borrowing is forbidden, including convenience cases such as:

- "The other person is away; I can sign for them."
- "We share a workstation; I can reuse their secret path."
- "They sent me their secret path and asked me to run `manifest approve` or
  `ceremony reencrypt` for them."
- "I am also Coordinator / Builder, so I can process member secrets too."

If a `.secret` or `.share` leaves the assigned holder's external vault, even
temporarily, treat it as key compromise:

- Revoke the affected alias material immediately.
- For Manifest Set members, run `key-forward` for that slot and redo affected
  service approvals.
- For Share Set members, have the Coordinator run `key-forward` for the same
  `member_index`, or append a new `(member_index = N+1, alias)` and redo
  Genesis if that is the clean recovery path.
- Record the incident, time, and impact in the audit log.

Agent enforcement:

- Any role skill must refuse requests to borrow another member's secret/share
  and route the requester to that alias holder's own role session.
- Holder credentials for `manifest approve`, `ceremony reencrypt`,
  `ceremony share-extract`, and `key file-generate` must belong to the calling
  alias:
  - file mode: `--secret-path` points at that alias's external
    `<alias>.secret`;
  - YubiKey mode: the caller's own YubiKey is present locally and the caller
    enters the PIV PIN;
  - `--share-path` always points at that alias's `.share`, even in YubiKey mode.
- The scripts cannot cryptographically prove alias-to-holder identity, but the
  agent must not run commands on behalf of another holder.
- There is no override: `--yes` and `--unsafe-auto-confirm` cannot bypass the
  "holder equals roster assignee" rule.

This rule exists because path sharing can look less dangerous than pasting key
bytes, but it still exposes signing or share-decryption capability to a second
person. After Genesis, `(alias, member_index, .pub, .share)` is permanently
bound to the resulting `quorum_key`; audit cannot reconstruct the correct
holder after cross-use.

## 2. Agent And Audit-Log Rules

- Never request or display `.secret`, `.share`, wrapped-share plaintext, or
  full key-file contents in chat.
- `--audit-log` records only phase names, redacted argv, exit codes, and key
  output basenames with SHA256.
- Redaction applies to flag values whose flag name starts with `-` and contains
  sensitive markers such as `secret`, `share`, `.pem`, `password`, `token`, or
  `seed`. Positional arguments and public `.pub` paths are not redacted because
  they are operational metadata.

## 3. `qos_client` Version Gate

`qos_client` has two uses:

- release/reference client: Builder's verifiable build artifact, usually
  `linux/amd64`, used for release SHA, pivot hash, and container-side checks;
- operator client: the binary actually run by members and the Coordinator. It
  must come from the same qOS source revision / signed release bundle, but it
  should match the operator machine platform, such as macOS arm64.

Apple Silicon members should not run a `linux/amd64` operator client directly.
Prefer a same-revision native `darwin/arm64` client. A fixed-digest
`docker run --platform linux/amd64` wrapper is acceptable only for controlled
non-production rehearsals, with minimal mounts and preferably no network.

Each role config must pin the SHA256 of the operator client that role actually
executes, and handoff material must record the qOS revision / release digest.
If `doctor` fails, do not continue into manifest or ceremony commands.

### 3.1 Auto-Fetch Rules

First init defaults to fetching the latest stable `qos_client` from
`0xkey-io/qos` GitHub Releases. The helper downloads the binary and `.sha256`
sidecar, computes the local SHA256, and installs the binary only if they match.
This default path does not bypass verification.

Rules:

- `/releases/latest` normally returns a non-prerelease release. If only
  prereleases exist, the helper may resolve `latest` to the newest prerelease
  and prints a warning. Do not use that warning path for production ceremonies.
- SHA mismatch writes the binary to `<out>.tainted`, refuses installation, and
  exits non-zero.
- If builder-handoff provides an independent expected SHA256, verify that too
  with `fetch_qos_client.py --expected-sha256 <hex>`.
- Never use a binary first and "fill in SHA later." There is no unsafe override.
- Auto-fetch runs during init / setup only. `doctor` is read-only: it prints a
  copy-pasteable fetch command but does not run it.
- Offline init uses `--no-qos-client-fetch`. The printed todo tells the
  operator how to fetch and verify later; after that, re-run `role_init.py
  --force` to record the verified hash.

### qos_client Replacement Triggers

| Trigger | Owner | Required action |
|---|---|---|
| qOS revision upgrade or PCR / manifest semantic change | Builder | Publish all operator-client platforms and SHA256; notify all operators |
| qOS / qos_client CVE or critical bug fix | Builder + security contact | Same; pause any active ceremony first |
| New operator platform, such as linux/arm64 | Builder + Coordinator | Add that platform binary and forward it |
| Genesis and later ceremony phases disagree on qOS revision | Coordinator | Use the version tied to the active ceremony manifest |
| Local binary corruption / SHA mismatch | Individual + Coordinator | Re-fetch the same version; never bypass SHA |

One ceremony uses one binary revision from `boot-genesis` through `verify`.
Changing qOS / `qos_client` mid-ceremony invalidates the round.

## 4. Human Confirmation Gates

The following steps must never run fully unattended:

- `approve-manifest`
- `proxy-re-encrypt-share`
- `post-share`
- `kubectl apply -k`
- `unsafe-skip-attestation`
- `unsafe-auto-confirm`

Global `--yes` must not bypass these gates. Dangerous steps require exact typed
phrases such as `approve-manifest`, `kubectl-apply`, `reencrypt-share`, or
`post-share`. `--yes` is only for non-sensitive steps such as `doctor` or
`deploy render`.

## 5. Workdir And Key Vault

- Ceremony workdirs should live outside repositories and outside `.cursor/`.
- Workdirs are disposable; key vaults are durable. Public material may be
  copied into a workdir. Private material must be referenced by absolute path or
  hardware PIV operation and must never be copied into the workdir.
- Public material includes `.pub` files, `quorum_key.pub`, PCR/pivot hashes,
  review/share-request bundles, and Coordinator public inputs.
- Member long-term keys (`master-seed` / `.secret`) and Genesis `.share` files
  must live outside the role workdir. The CLI rejects sensitive paths inside the
  role workdir.

### 5.1 Vault Modes

Choose the long-term secret carrier by environment. Production defaults to
YubiKey. File secrets are for non-production rehearsals or local development
only.

| Environment | Recommended vault | CLI shape | Backup policy | Downgrade policy |
|---|---|---|---|---|
| prod | YubiKey 5-class device / equivalent HSM (PIV slot, non-exportable) | `--yubikey` | Provision at least two YubiKeys; lost device uses `key-forward` | Do not downgrade; delay the ceremony if hardware is not ready |
| non-prod rehearsal | encrypted disk image / encrypted USB `.secret` | `--secret-path /Volumes/<vault>/<alias>.secret` | one local encrypted backup plus one offline encrypted backup | short rehearsals only; never reuse for prod |
| local dev | local encrypted directory `.secret` | `--secret-path ~/0xkey/operator-keys/<alias>/<alias>.secret` | one local backup is enough; destroy after rehearsal | regenerate before any real ceremony |

DR private keys follow the same production requirement: YubiKey or independent
HSM in production, encrypted-disk only for non-production debug work.

### 5.2 YubiKey Operational Rules

- Generation: `key yubikey-provision` creates the long-term key directly in the
  YubiKey PIV slots. The private key is non-exportable; `.pub` lands in
  `outbox/<alias>.pub`.
- Use: `manifest approve`, `ceremony reencrypt`, and `ceremony share-extract`
  accept exactly one of `--yubikey` or `--secret-path`; passing both is an
  error. PIV PIN/PUK is handled by `qos_client` on its own TTY and never passes
  through this skill's prompts, stdout, or audit log.
- Share files: `--share-path` is always an external `.share` file. YubiKey does
  not store the Genesis share.
- Upstream dependency: production requires `qos_client` support for
  `--yubikey` on `provision-yubikey`, `approve-manifest`,
  `proxy-re-encrypt-share`, and `after-genesis`. Builder releases must preserve
  that support.

### 5.2.1 First-Time YubiKey Checklist

`provision-yubikey` assumes a clean PIV applet using the default TDES
Management Key. If this assumption is false, provisioning can fail and leave
slot 9C/9D in a half-written state. Reset PIV before retrying.

Do not blindly run `key yubikey-provision`. The wrapper preflights `ykman`:
it verifies `Management key algorithm: TDES` and checks whether slots 9C/9D
already contain key material. If either slot is occupied, the agent must warn
the user and require the exact confirmation phrase before proceeding.

1. Inspect PIV state:

   ```bash
   ykman piv info
   ykman piv keys info 9c
   ykman piv keys info 9d
   ```

   Expected:

   - `Management key algorithm: TDES`. YubiKey 5.7+ may factory-default to
     `AES192`, which is incompatible with the current `qos_client` assumption
     and causes `GenerateSign(FailedToAuthWithMGM)`.
   - `PIN tries remaining: 3/3` is preferred.
   - Default PIN and default Management Key warnings are expected on a clean
     device. If they are absent, the PIN or MGM key may have changed.
   - Slots 9C and 9D should be empty. If either contains key/cert material
     (smart-card login, PIV SSH, email signing, or a half-failed qos_client
     attempt), get explicit user confirmation before overwriting or reset PIV.

2. If the state is wrong, reset only the PIV applet:

   ```bash
   ykman piv reset --force
   ```

   This irreversibly destroys PIV private keys and certificates in slots
   9A/9C/9D/9E/82-95. It does not affect FIDO2/passkeys/OpenPGP/OTP.

3. If firmware still reports AES192, switch the Management Key algorithm back
   to TDES:

   ```bash
   ykman piv access change-management-key \
     --algorithm TDES \
     --management-key 010203040506070801020304050607080102030405060708 \
     --new-management-key 010203040506070801020304050607080102030405060708 \
     --force
   ```

   Re-run `ykman piv info` and confirm `Management key algorithm: TDES`.

4. Before provisioning, explain the touch sequence. `qos_client` writes slot 9C
   (SIGNATURE) and slot 9D (KEY_MANAGEMENT), requiring two touches after PIN
   entry:

   - first blink: touch once to create the slot 9C signing-key certificate;
   - second blink: touch again to create the slot 9D key-management / ECDH
     certificate.

   Do not leave after entering the PIN. Missing either touch window can cause
   `GenerateSign(FailedToGenerateSelfSignedCert)` and leave a half-written slot.

5. After success, verify:

   ```bash
   ykman piv info
   ykman piv keys info 9c
   ykman piv keys info 9d
   ```

   Both slots should show generated keys, PIN required, touch required, and a
   `CN=QuorumOS` certificate.

6. Production should provision at least two YubiKeys for the same alias where
   operationally possible, so a lost device does not force immediate
   `key-forward`.

`qos_client provision-yubikey` does not accept custom `--mgm-key` or PIN
arguments. If an existing YubiKey must keep its current PIV credentials, use a
dedicated YubiKey for 0xkey instead.

### 5.3 File-Mode Constraints

- Agents receive absolute paths only, never key contents.
- `key file-generate --master-seed-path` must point outside the role workdir;
  `--pub-path` may write into `outbox/`.
- `manifest approve`, `ceremony reencrypt`, and `ceremony share-extract`
  reject sensitive paths inside the role workdir.
- After generation, set permissions and backups: `chmod 700 <alias-dir>`,
  `chmod 600 *.secret *.share`, and keep at least one offline encrypted backup.

## 6. post-share Order And Failure

Some environments are sensitive to member share posting order. Coordinator
runbooks should record `post-share` order and redo the affected service
ceremony if order-sensitive posting fails.

## 7. Data Plane vs Control Plane

Pod readiness and `QuorumKeyProvisioned` are not sufficient by themselves.
Verification must also include `:8081/health` and a business-route smoke check.
Do not assume minimal containers have `/bin/sh`, `curl`, or debug tooling; use
the skill's `verify` command, local port-forward, or a controlled jumpbox.

## 8. Handoff Integrity

- Review, share-request, approvals, wrapped-shares, and genesis-output bundles
  must include `SHA256SUMS`.
- Receivers run `bundle verify --bundle-dir <dir>` before signing, re-encrypting,
  or posting.
- Prefer `bundle create --kind ... --archive ...` and `bundle extract --archive
  ...` over manual directory assembly.
- `post-share` and `proxy-re-encrypt-share` must select approvals explicitly via
  `--approval-alias` or `config.approval_alias`; matching includes service
  namespace and nonce.

## 9. Threshold Recommendations

`quorum_threshold` is a single-line decimal integer in the same directory as
the relevant `*.pub` files.

| Set | Small rehearsal | Production default |
|---|---|---|
| Manifest Set | 2/3 | 3/5 |
| Share Set | 2/3 | 4/10 |
| Patch Set | May be disabled with README | Same class as Manifest Set, or explicitly disabled |

Selection rules:

- Keep threshold high enough to avoid single-party unlock.
- Keep threshold low enough that normal absences do not deadlock ceremonies.
- Changing set membership requires a new manifest/share-set flow; do not
  silently change thresholds after Genesis.

## 10. Member Roster

- Coordinator assigns aliases and share-set member indexes in
  `shared/member-roster.json` before collecting any `.pub` files. Members
  confirm assignments but do not choose them.
- Collisions are severe: alias collision overwrites `<alias>.pub` and misroutes
  approvals; member-index collision collides wrapped-share filenames and
  post-share ordering.
- `doctor coordinator`, `manifest generate`, and `ceremony genesis-boot` all
  validate roster shape, alias safety, contiguous share indexes, and
  `shared/<set>/*.pub` filename-to-roster correspondence.
- Review, share-request, and genesis-output bundles include the relevant roster
  slice in `BUNDLE.json.members` and copy `member-roster.json` into the bundle
  root so members can verify their assignment.
- Replacing a member uses `key-forward` for the same index or appends a new
  index with a new ceremony roster. Never edit historical roster entries in
  place.
