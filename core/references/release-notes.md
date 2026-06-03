# Release notes

The version recorded in this skill bundle is the single line in
[`VERSION`](../../VERSION) at the repository root. Every published
`SKILL.md` carries the same string in its YAML frontmatter
(`version: X.Y.Z`); see [`tools/sync-skills.py`](../../tools/sync-skills.py)
for how the two are kept in lock-step. Git tag `vX.Y.Z` plus the
matching GitHub Release are the canonical artefacts the `npx skills
update` tooling consults.

This file follows [keep-a-changelog](https://keepachangelog.com/) — newest
entries at the top.

## How to check the installed version

- Read the `version:` line in the SKILL.md frontmatter of any role:
  `head -10 $SKILL_DIR/SKILL.md`.
- Or list installed skills with `npx skills list -g`.
- Compare against the latest published tag with
  `gh release view -R 0xkey-io/enclave-keyops-skills` (or
  `git -C <skill-src> ls-remote --tags origin | tail -1`).

## How to upgrade

| Install method | Upgrade command |
|---|---|
| `npx skills add 0xkey-io/enclave-keyops-skills` | `npx skills update 0xkey-keyops-<role>` |
| Local `git clone` of the skill repo | `git -C <skill-src> pull --tags` |

When a release is marked **BREAKING** below, follow the listed migration
step before re-running ceremony commands.

---

## 0.5.8 — 2026-06-04

Hardening round: make the share-set approval a non-optional invariant of the
wrapped-shares bundle so it can never be silently dropped again.

### Fixed

- **`bundle create --kind wrapped-shares` no longer ships without the share-set
  approval.** Previously the approval copy was a silent `if dir exists` step:
  if `share-set-approvals/` was missing, empty, or pointed elsewhere, the bundle
  was built successfully with no approval and the failure only surfaced much
  later as `expected exactly one approval ... found 0` on the Coordinator's
  `ceremony post`. The packager now enforces the invariant *"every service with a
  wrapped share must carry a matching (namespace + nonce) share-set approval"*
  and hard-fails at create time, naming the offending service.

### Added

- **`bundle extract --install` validates the same invariant on the consumer
  side** (defense in depth): a wrapped-shares bundle missing an approval, or
  whose approvals disagree with the new `BUNDLE.json.share_set_approvals`
  manifest, is rejected at install instead of at `ceremony post`.
- **`BUNDLE.json` for wrapped-shares now records a `share_set_approvals`
  manifest** (service → approval filenames) so the consumer can detect drops or
  tampering in transit.
- **Config single source of truth for Share-Set member I/O dirs.** New optional
  `paths.wrapped_shares_out_dir` / `paths.share_set_approvals_dir`. `ceremony
  reencrypt` and `bundle create`/`install` all resolve these from config (with
  the historical `wrapped-shares-out` / `share-set-approvals` defaults), so
  overriding `reencrypt --wrapped-out-dir` / `--share-set-approvals-dir` without
  matching config can no longer silently misplace approvals — it fails loudly.
- **`manifest generate` now prints a pivot-args manifest-impact reminder.**
  pivot args are baked into the attested manifest, so an env-specific value such
  as the notarizer recipient pubkey or the signer / tls-fetcher email parameters
  is part of the manifest hash. Changing one forces a FULL re-ceremony for that
  service (manifest-set re-approval + share-set re-boot-standard +
  proxy-re-encrypt-share + post-share). `manifest generate` always echoes the
  effective `--pivot-args` per service plus this consequence so the agent prompts
  the operator to confirm these values before distributing the review bundle.
  Advisory only — it never blocks the command. The example config gains a
  `$pivot_args_comment` and `coordinator.md` documents the gate.

### Changed (potentially breaking)

- A wrapped-shares bundle built by keyops **< 0.5.8** (or any bundle lacking the
  share-set approval) is now **rejected** by `bundle extract --install`. Rebuild
  it with keyops >= 0.5.8 and resend. This is intentional: such a bundle would
  have failed at `ceremony post` anyway, only later and with a more confusing
  error.

---

## 0.5.7 — 2026-06-02

Role-feedback round after the v0.5.6 production Genesis (Coordinator + Share Set
member). No breaking changes.

### Fixed

- `ceremony reencrypt` no longer accepts `--validation-time-override`. qos_client's
  `proxy-re-encrypt-share` parser has no such token (only `after-genesis` /
  `ceremony share-extract` does), so the wrapper was passing a flag that qos_client
  rejects with an unexpected-input error — the option could never have worked. For
  an expired attestation cert the only supported path is `--unsafe-skip-attestation`;
  see the new delayed-reencrypt runbook. `ceremony share-extract` keeps
  `--validation-time-override` unchanged.

### Added

- `bundle create --archive` no longer requires `--bundle-dir`. When only `--archive`
  is given, the bundle is staged in a throwaway temp directory and removed after
  packing, so re-runs never fail with "bundle dir already exists". Passing
  `--bundle-dir` (with or without `--archive`) behaves as before; passing neither
  now errors clearly.

### Docs

- share-set-member.md: standardized **delayed reencrypt** runbook for
  `InvalidCertChain(CertExpired)` — when to use `--unsafe-skip-attestation`, the
  out-of-band bundle-authenticity check it forces on the member, the audit-log
  trail, and preferring a fresh Coordinator re-attestation when practical. Removed
  the stale "`--validation-time-override` is a forward-compat passthrough" note.
- coordinator.md: documented how the post-share order is resolved
  (`services[].post_share_members_order` → `--post-global-order` → default `[1, 2]`)
  and its accepted formats (JSON int array or comma string, `m` prefix stripped).
- config.prod.example.json: per-service `$post_share_members_order_comment`
  describing the field's format and fallback semantics.

---

## 0.5.6 — 2026-06-01

### Fixed

- `ceremony reencrypt` now names the Share Set Approval it produces by the
  share alias — `share-set-approvals/<service>/<alias>-<namespace>-<nonce>.approval`
  — instead of borrowing a manifest-set approval's filename. The previous code
  ran an `approval_for` lookup and a dead `shutil.copy2`, and required a
  `--approval-alias` that `role_init` never set (so the documented flow errored,
  forcing members to pass their manifest alias by hand and producing a
  `manifest-`prefixed file that collided with the Coordinator's own approval on
  install). `qos_client proxy-re-encrypt-share` only *writes* `--approval-path`
  (it never reads it), so keyops now owns the output filename directly.
  `--approval-alias` is removed from `ceremony reencrypt`.

### Added

- `bundle create --kind wrapped-shares` now packages the matching share-set
  approvals from the dedicated `share-set-approvals/` directory, and
  `bundle install` merges them into the Coordinator's `manifest/approvals/`,
  so `ceremony post` finds the share-set approval automatically. Older bundles
  without an `approvals/` directory still install (backward compatible).
- `host_ip` field documented in `config.prod.example.json` (per-service) for
  choosing between cluster-internal `--resolve-pod-ip` and cluster-external
  `kubectl port-forward`, plus a `manifest_nonce` guidance comment.

### Docs

- WORKFLOWS.md: 3-hour attestation-cert window on Phase 5; a Phase 7–9 **Pod
  atomicity invariant** (container restart is safe, deleting/recreating a Pod
  destroys the ephemeral key; `WaitingForQuorumShards` crash-loops are
  expected); share-set approvals in Phase 8 output and the B4 fan-in barrier;
  `DecryptionFailed` / `CertExpired` recovery entries.
- coordinator.md: Network Topology table, a post-share pre-flight checklist
  (the `--approval-alias` for `ceremony post` must reference a **share-set**
  member), `manifest_nonce` / patch-set preconditions, and new troubleshooting
  rows.
- SECURITY.md and share-set-member.md: post-share approval must be share-set
  signed; `ceremony reencrypt` needs no `--approval-alias`.

## 0.5.5 — 2026-06-01

### Added

- `--validation-time-override` opt-in passthrough for `ceremony share-extract`
  (forwarded to `qos_client after-genesis`) and, for forward compatibility,
  `ceremony reencrypt`. NOTE: the current qos_client `proxy-re-encrypt-share`
  does not accept this flag; for reencrypt cert-expiry use
  `--unsafe-skip-attestation` until a qos_client build adds support.
- Preflight checks in `ceremony share-extract`: verifies that
  `pcr3-preimage.txt` and qos-release PCR files exist before invoking
  `qos_client`, with actionable error messages pointing to
  `bundle extract --install`.
- `bundle install` for `share-request` kind now backfills `manifest_nonce`
  from `BUNDLE.json` when the config value is `null`, and **persists** the
  patched values back to `config.json` on disk so the subsequent, separate
  `ceremony reencrypt` invocation reads the correct nonce. Eliminates manual
  config editing.

### Changed

- `ceremony reencrypt` now **always** passes `--unsafe-auto-confirm` to
  `qos_client proxy-re-encrypt-share`. The per-service interactive
  namespace confirmation (`Is this the correct namespace name?`) was
  blocking non-interactive agent terminals. The keyops wrapper's own
  `[confirm]` log line is the security gate; the qos_client layer's
  redundant prompt is suppressed automatically.
- `--unsafe-auto-confirm` CLI flag removed from `ceremony reencrypt`
  argparser (the behavior is now implicit).

> `manifest_nonce` keeps its `null` default in `config.prod.example.json`
> (a deliberate "force a conscious value" safety property for the
> Coordinator's `manifest generate`). Members no longer need to edit it
> by hand because `bundle install` backfills and persists the bundle's
> nonce automatically.

---

## 0.5.4 — 2026-05-30

### Added

- `bundle extract --install`: after extracting and verifying a bundle,
  automatically distribute files into the workdir paths that downstream
  commands expect. Eliminates 10+ manual `cp` steps per bundle.
- `bundle install --bundle-dir`: install from an already-extracted bundle
  directory. Supports all bundle kinds: review, genesis-output,
  share-request, approvals, wrapped-shares.
- `bundle create --kind review` now includes `aws-x86_64.pcrs` alongside
  `nitro.pcrs` from the qos-release directory.
- `manifest approve` runs preflight checks on qos-release PCR files,
  pcr3-preimage.txt, and quorum_key.pub before invoking qos_client,
  giving clear errors instead of Rust panics.

### Fixed

- `Config.__init__` now resolves `qos_client_path` relative to `--workdir`
  instead of the current working directory. Callers no longer need to `cd`
  into the workdir before running commands like `doctor holder`.

### Changed

- Runbooks for manifest-set-member and share-set-member updated to use
  the new `--install` flag, removing manual find/verify/cp steps.

---

## 0.5.3 — 2026-05-30

### Fixed

- `ceremony share-extract` now passes `--qos-release-dir` to `qos_client
  after-genesis`. Previously the wrapper omitted this flag, forcing operators
  to call `qos_client` directly as a workaround.

### Changed

- `confirm_dangerous()` no longer blocks on `input()`. Dangerous operations
  are logged to stderr and proceed immediately. Safety is enforced by
  argument-level validation and agent pre-approval in the chat UI.
- `--yes` help text reverted to its original scope (non-dangerous prompts
  only).

---

## 0.5.2 — 2026-05-29

### Fixed

- Bundled `certifi` CA certificates into the PyInstaller binary. Without this,
  all HTTPS requests (e.g., auto-fetching `qos_client` from GitHub during
  `keyops init`) failed with `CERTIFICATE_VERIFY_FAILED` on macOS and some
  Linux setups. The frozen binary now sets `SSL_CERT_FILE` at startup to point
  at the bundled `cacert.pem`.

---

## 0.5.1 — 2026-05-29

### Fixed

- Removed the leftover prod/non-prod vault mode restriction from the hand-written
  `SKILL.md` files of the Manifest and Share roles. v0.5.0 cleaned this wording
  from `SECURITY.md` and the role runbooks under `core/references/roles/`, but the
  per-skill `SKILL.md` files are not generated by `sync-skills.py`, so they still
  read "YubiKey PIV slot (prod default)" and "external `.secret` file
  (non-production / dev only)". Agents read `SKILL.md` first and continued to
  block `--secret-path` for production ceremonies. Both vault modes are now
  presented as equal operator choices in `SKILL.md` as well.

---

## 0.5.0 — 2026-05-29

### Headline

Defense-in-depth against agent Python script misuse; vault mode is now a free
operator choice (no longer environment-restricted).

### Added

- `AGENTS.md` at repo root — recognized by Cursor, Codex, Claude Code, Gemini,
  and OpenClaw. Instructs agents to install role skills via `npx skills add` and
  use the `keyops` binary exclusively; explicitly prohibits direct execution of
  Python files.
- Runtime guard in every Python entry point (`dist/src/*.py`): direct invocation
  now exits 1 with a binary-download hint unless `sys.frozen` (PyInstaller) or
  `KEYOPS_SOURCE_MODE=1` (maintainer escape hatch) is set.

### Changed

- **`core/scripts/` renamed to `dist/src/`** — build source is now co-located
  with `keyops.spec` and `build.sh` to communicate "build artefact, not operator
  interface". Updated `dist/keyops.spec`, `tests/_helpers.py`,
  `tests/test_skill_layout.py`, and `role_init.skill_dir()` accordingly.
- **Vault mode restriction removed** — `SECURITY.md §5.1` no longer mandates
  YubiKey for production ceremonies. Both `--yubikey` and `--secret-path` are
  supported for any ceremony type; the operator chooses based on their hardware
  and security posture. Agents no longer block `--secret-path` when the ceremony
  name contains `prod`.
- Role skill packages no longer contain Python scripts (`scripts/` directories
  removed); operators interact exclusively through the `keyops` binary.
- `README.md`: removed `python3 scripts/fetch_keyops.py` auto-fetch section and
  Python files from the "Agents without first-class skill support" file list.
- `core/references/source-invocation.md`: updated paths to `dist/src/` and added
  `KEYOPS_SOURCE_MODE=1` prefix to all maintainer examples.

### Migration

No ceremony data migration required. If you have a local clone of the skill used
as a symlink target (`ln -sfn`), run `git pull` and `npx skills update
0xkey-keyops-<role>` (or restart the agent). The binary interface is unchanged.

---

## 0.4.0 — 2026-05-28

### Headline

Self-contained `keyops` binary introduced; role skill packages are now
binary-first with all Python scripts removed from operator-facing packages.

### Added

- PyInstaller build (`dist/keyops.spec`, `dist/build.sh`) producing
  self-contained `keyops` binaries for `darwin-arm64` and `linux-amd64`.
- GitHub Actions `release.yml`: tag push (`v*`) triggers dual-platform build and
  publishes binaries + `.sha256` sidecars to GitHub Releases.
- `keyops init`, `keyops fetch-qos-client`, `keyops fetch-keyops` unified CLI.

### Changed

- All operator-facing role documentation updated to use `keyops` binary commands
  exclusively.
- Python scripts removed from `skills/*/scripts/` directories.
- `SKILL.md` files updated to reference `keyops` binary download instructions
  instead of Python fallback.

### Migration

Download the `keyops` binary for your platform from GitHub Releases and place it
on `$PATH` before running any ceremony command.

---

## 0.3.0 — 2026-05-19

### Headline

Documentation rewritten in English for international readability; YubiKey
provision preflight added to prevent common first-time provisioning failures.

### Added

- `enclave_keyops.py key yubikey-provision` now runs a preflight that:
  - verifies `ykman` is installed,
  - checks that YubiKey PIV Management key algorithm is TDES (not AES192),
  - detects occupied PIV slots 9C/9D and requires typed confirmation before
    overwriting,
  - prints an explicit two-touch notice so the operator knows to watch the LED.
- Operator prompt templates now include recommended default workspace paths
  (e.g. `~/.0xkey-ops/coordinator`).
- New test file `tests/test_yubikey_provision_preflight.py` covering MGM
  algorithm check, both-slots-occupied, wrong-confirmation-phrase, and
  touch-notice scenarios.

### Changed

- All core documentation (`PRINCIPLES.md`, `SECURITY.md`, `WORKFLOWS.md`,
  role runbooks, `workspace-rules.md`, `operator-prompts.md`) rewritten in
  English. Chinese retained only in README operator-prompt trigger examples.
- Cross-role refusal wording in all four `SKILL.md` files unified to a
  single concise line.
- `_piv_slot_occupied` simplified to fail-closed: any non-empty output that
  does not match known "empty" markers is treated as occupied.
- Roster-first rule in `workspace-rules.md` reworded for clarity.
- `SECURITY.md` section references updated from `§N "中文标题"` to
  `section N` style.

### Notes

- No breaking changes to CLI arguments or config format.
- Existing workspaces do not need re-initialization.

---

## 0.2.0 — 2026-05-17 — BREAKING

### Headline

`role_init.py` now auto-fetches the latest stable `qos_client` from
`0xkey-io/qos` GitHub Releases on first init. The operator no longer
types or pastes a SHA256.

### Breaking

- **Removed** `role_init.py --qos-client-sha256 <hex>`. Letting humans
  type a hash is itself an attack surface (typo, paste-jacking,
  copy-paste from the wrong release notes); the verified hash now comes
  exclusively from the release sidecar plus a local recompute. There is
  no `--unsafe-*` escape.
- **Renamed** `role_init.py --skip-qos-client-fetch` →
  `--no-qos-client-fetch`.
- `enclave_keyops.py doctor *` now always emits a copy-pasteable
  `fetch_qos_client.py` hint when the binary is missing or the SHA does
  not match — defaulting to `--release-tag latest` even on legacy
  workspaces that have no `qos_client_release` block in `config.json`.

### Migration

For each existing role workspace (per operator):

1. Re-run `role_init.py --force --role <role> --root "$WORKDIR" ...`.
2. The default auto-fetch will:
   - resolve the latest stable tag on `0xkey-io/qos`,
   - download `qos_client.<host-platform>` and its `.sha256`,
   - re-verify, install at the role-correct path, and
   - update `config.json.qos_client_sha256_expected` +
     `qos_client_release.{tag,resolved_tag,repo,platform}`.
3. Run `doctor holder` (members) or `doctor coordinator` (Coordinator)
   to confirm the workspace is healthy.

If the host has no network access at init time, pass
`--no-qos-client-fetch` and follow the printed todo command to fetch
the binary from a connected host (or `gh release download` plus
`shasum -a 256 -c`) before re-running `role_init.py --force`.

Old runbooks / scripts that still pass `--qos-client-sha256` will now
exit with an `unrecognized arguments` error from argparse. Update those
call sites to drop the flag (and any expected-hash variable computed
from a Builder handoff sheet); the auto-fetch path now produces the
verified hash itself.

### Added

- `fetch_qos_client.resolve_release_tag()` resolves the literal `latest`
  via `GET /repos/<repo>/releases/latest` (skips drafts and
  prereleases). On 404 (no stable release published yet) it falls back
  to the most recent prerelease and emits a stderr `WARN`. Pin a
  concrete tag with `--release-tag <tag>` to silence the warning.
- New ceremony override: `role_init.py --qos-client-release-tag <tag>`
  to lock all members to the same Builder revision. Communicate the tag
  out-of-band the way you already communicate ceremony id and member
  roster.
- New offline switch: `role_init.py --no-qos-client-fetch` scaffolds
  the workspace, records the platform / repo metadata in
  `config.json.qos_client_release`, and prints an exact
  `fetch_qos_client.py` follow-up command in the init "todo" block.
- `core/references/qos-client-platform.md` rewritten around the default
  path; `SECURITY.md §3.1` codifies the auto-fetch red lines (sha
  mismatch always quarantines; never an `--unsafe-*` opt-out).
- This skill bundle now ships a `VERSION` file, a per-role
  `version: <X.Y.Z>` frontmatter line, and a `## Version & update`
  section in every `SKILL.md`.

### Notes

- Internal API: `role_init.configure_json` now accepts
  `qos_client_release_tag`, `qos_client_release_resolved_tag`,
  `qos_client_release_repo`, and `qos_client_release_platform` keyword
  arguments. The order of `qos_client_release` fields in `config.json`
  has expanded to include `resolved_tag` so re-fetches stay
  reproducible across releases.

## 0.1.x — historical (pre-versioning)

The repository ran without explicit version tags before 0.2.0. The
git history before tag `v0.2.0` is the canonical record; commit
`0dce697` introduced the GitHub Releases auto-fetch path and commit
`978c3e3` made it the default and removed the human-typed
`--qos-client-sha256` flag. There was no separate v0.1.0 release.
