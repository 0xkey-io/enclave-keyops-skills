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
