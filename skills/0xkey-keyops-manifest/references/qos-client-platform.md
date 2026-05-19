# `qos_client` platform rule

Do not assume the Builder's `linux/amd64` `qos_client` can run on every
operator machine. For Manifest / Share members on macOS arm64, prefer an
operator-native `darwin/arm64` `qos_client` built from the same audited qOS
source revision or signed release bundle. The approval / re-encryption output
signs canonical manifest / share-request data, not the local binary
architecture, so the operator client may be platform-native as long as it is
version-pinned and hash-checked.

If only a `linux/amd64` client is available, use a controlled wrapper such as
`docker run --platform linux/amd64` only after confirming the image digest and
mounting only the required workdir / key-vault paths. Record the SHA256 of the
actual executable path or wrapper being used in the role config.

## When to update `qos_client`

See `SECURITY.md` section 3 for the authoritative replacement rules. Quick
summary:

- qOS revision upgrade (PCR / manifest fields change) — Builder republishes;
  Coordinator forwards the new release URL.
- Upstream qOS / qos_client CVE — Builder + security republish during an
  explicit ceremony pause.
- New cross-platform user (e.g. first `linux/arm64` operator) — Builder ships
  the missing platform binary.
- Genesis vs ongoing ceremony revision mismatch — pin to the in-progress
  ceremony's revision until that ceremony finishes.
- Local SHA256 mismatch / corruption — pull the same version again; never
  bypass the SHA check.

A single ceremony **must not** swap `qos_client` mid-flight: `boot-genesis`
→ `approve-manifest` → `boot-standard` → `proxy-re-encrypt-share`
→ `post-share` → `verify` all use the same binary and the same SHA256.

## Where to obtain `qos_client`

The default Builder workflow publishes operator-client binaries as a
GitHub Release on `https://github.com/0xkey-io/qos`, and the skill is
wired so the operator does not have to think about that URL: `role_init.py`
auto-fetches the latest stable release on first init, verifies SHA256
against the published sidecar, and writes the verified hash into
`config.json.qos_client_sha256_expected`.

### Default first-init (recommended)

```bash
# Resolves "latest stable" via GitHub's /releases/latest API, downloads
# qos_client.<host-platform>, verifies SHA256, installs at the role-correct
# path, and records the release tag + verified hash in config.json.
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role <role> \
  --root "$WORKDIR" \
  ...role-specific flags...
```

What "latest" means here: GitHub's `/releases/latest` endpoint returns the
most recent **non-draft, non-prerelease** release. If `0xkey-io/qos` has
not published a stable release yet (only RC tags exist), `role_init.py`
falls back to the most recent prerelease and emits a stderr WARN — that
is the signal to ask Builder for a stable release before running a
production ceremony.

### Pin a specific release (ceremony lock)

When the ceremony rules require a specific qOS revision, the Coordinator
chooses the tag and every member uses the same one:

```bash
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role <role> --root "$WORKDIR" \
  --qos-client-release-tag 0xkey-qos_client-vX.Y.Z
```

### Standalone re-fetch (refresh, repair, audit)

```bash
python3 "$SKILL_DIR/scripts/fetch_qos_client.py" \
  --release-tag latest \
  --out "$WORKDIR/shared/qos_client"
```

Pass `--repo <org>/<repo>` to use a private mirror of the release, and
export `GH_TOKEN` (or `GITHUB_TOKEN`) when the mirror requires
authentication. `--release-tag latest` is the default; pass a concrete
tag to pin.

### Integrity layers

Every published release ships three independent integrity anchors:

1. Per-platform `.sha256` sidecar next to each `qos_client.<platform>` asset.
2. A `MANIFEST.json` recording `qos_revision`, `built_at`, `workflow_run_url`,
   and per-platform `sha256` / `build_method`.
3. SLSA build provenance attestation (`gh attestation verify`), proving the
   binary was produced by a specific workflow run on the `0xkey-io/qos`
   repository.

`fetch_qos_client.py` auto-detects the platform via `uname`, downloads the
binary plus its `.sha256` sidecar, refuses to install on hash mismatch
(quarantining the bad binary at `<out>.tainted`), and prints a manual
fallback recipe (curl + `gh release download` + `shasum -a 256 -c`) when
the network or release path is unreachable. `doctor` detects a missing
binary or SHA mismatch and re-prints the exact `fetch_qos_client.py`
command — defaulting to `--release-tag latest` even on workspaces that
have no recorded release metadata — but doctor never auto-runs the
fetch (`SECURITY.md §3.1`).

### Offline init

On machines with no GitHub network access at init time, scaffold the
workspace with `--no-qos-client-fetch` and run `fetch_qos_client.py`
later from a host that does have access (or copy the verified binary +
`.sha256` in by hand). `role_init.py --force` re-runs the fetch when
the network is back and updates `config.json` with the verified hash.
