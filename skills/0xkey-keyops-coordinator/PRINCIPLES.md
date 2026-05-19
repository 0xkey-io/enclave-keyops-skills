# Enclave KeyOps Skill Maintenance Principles

Use this checklist whenever modifying this skill. If a proposed change violates
one of these principles, stop and redesign before shipping it.

## 1. Prod-Like Validation First

- Validate new or changed CLI flows in a prod-like non-production environment
  before using them with production key material.
- Before a production ceremony, validate at least `doctor coordinator`, bundle
  create/extract/verify, five-service `manifest envelope --dry-run`, and
  `verify --dry-run`.
- Any flow involving `deploy apply`, `boot-standard`, or `post-share` requires a
  separate human approval gate.
- Commands that have not been validated in a prod-like environment must not be
  used directly against production keys, manifests, or Kubernetes apply steps.

## 2. No Legacy Naming

- Documentation, config templates, and script defaults must not contain old
  project names or historical namespaces.
- Defaults use `0xkey` and `0xkey-enclave`; environment-specific differences
  must be explicit external configuration.

## 3. Least-Privilege Roles

- `doctor holder` must not require `kubectl`, AWS credentials, or cluster
  access.
- Only `doctor coordinator` checks Kubernetes / AWS dependencies and context
  allowlists.
- Manifest / Share members handle local bundles and their own holder
  credentials only; they do not need Kubernetes access.

## 4. Dangerous Steps Require Exact Confirmation

These steps must keep exact typed confirmations and must not be bypassed by
global `--yes`:

- `approve-manifest`
- `proxy-re-encrypt-share`
- `post-share`
- `kubectl apply -k`
- `unsafe-skip-attestation`
- `unsafe-auto-confirm`

## 5. Approval Matching Must Be Exact

- Never choose "the first `.approval` in a directory."
- Approval selection must match alias, service manifest namespace, and manifest
  nonce.
- `manifest envelope` must reject approvals from other services or other
  nonces.

## 6. Bundles Are The Interface

- Handoff material must use `bundle create`, `bundle extract`, and
  `bundle verify`.
- Every bundle must contain `BUNDLE.json` and `SHA256SUMS`.
- Extraction must defend against tar path traversal and verify checksums before
  use.

## 7. Verification Covers Control Plane And Data Plane

- Pod Ready alone is not sufficient.
- `/qos/enclave-health` HTTP 200 alone is not sufficient.
- Verification must check `QuorumKeyProvisioned`, `app-bridge :8081/health`,
  and business-route POST smoke.

## 8. No Embedded Binaries Or Secrets

- Skill directories must not contain `qos_client`, container images, `.secret`,
  `.share`, wrapped-share plaintext, or real environment configs.
- `qos_client` is distributed externally and verified with the configured
  expected SHA256.

## 9. Fail Explicitly

- PCR, pivot hash, nonce, approval, bundle checksum, Kubernetes context, and
  data-plane verification failures must exit non-zero.
- Do not hide root causes with silent fallbacks, empty catches, or automatic
  retry loops.

## 10. Transport Is Not A Filesystem Contract

- The skill defines the bundle interface: `<name>-<stamp>.tgz` plus outer
  `.tgz.sha256`, with `SHA256SUMS` and `BUNDLE.json` inside.
- It does not prescribe the transport channel between roles. Shared filesystem,
  S3, chat with file attachment, encrypted email, encrypted USB, and private git
  are all possible operator choices.
- `inbox/` and `outbox/` inside a role workdir are local consumer drop points,
  not cross-member protocol paths.
- Fixed debug names such as `coordinator-to-members/<topic>/...` are not part of
  the skill contract.
- Future transport-specific semantics should be modeled as bundle metadata, not
  directory naming conventions.

## 11. Coordinator Owns Alias And Member-Index Assignment

- Members must not choose their own alias or member index. Coordinator issues
  them in `shared/member-roster.json` before any `.pub` is collected.
- Scripts treat the roster as an invariant: `doctor coordinator`,
  `manifest generate`, and `ceremony genesis-boot` validate alias/file stem
  equality, contiguous share-set member indexes, and absence of extra `.pub`
  files.
- After `ceremony genesis-boot`, `(alias, member_index, .pub, .share)` is bound
  to the generated `quorum_key`. Collisions or in-place edits require redoing
  Genesis.
- Member replacement uses `key-forward` for the same index with a new key, or a
  new index in a new ceremony roster. Never edit historical roster entries in
  place.
- Review, share-request, and genesis-output bundles must carry the relevant
  roster slice in `BUNDLE.json.members` plus a `member-roster.json` copy so
  members can verify their assignment locally.
