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

See `SECURITY.md §3 qos_client 更换触发表` for the authoritative rules. Quick
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

The skill does not ship binaries. Operators receive `qos_client` (and its
SHA256) from the Coordinator, who forwards the Builder's release-channel URL.
See `references/roles/builder.md` `Operator-client release channels` for the
authoritative channel list.

### Recommended channel: GitHub Releases on `0xkey-io/qos`

The default Builder workflow publishes operator-client binaries as a GitHub
Release on `https://github.com/0xkey-io/qos`, with three integrity layers:

1. Per-platform `.sha256` sidecar next to each `qos_client.<platform>` asset.
2. A `MANIFEST.json` recording `qos_revision`, `built_at`, `workflow_run_url`,
   and per-platform `sha256` / `build_method`.
3. SLSA build provenance attestation (`gh attestation verify`), proving the
   binary was produced by a specific workflow run on the `0xkey-io/qos`
   repository.

The skill ships a stdlib helper to consume that channel. From a role
workspace:

```bash
# Auto-fetch on first init (records the verified SHA256 in config.json):
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role <role> \
  --root  "$WORKDIR" \
  --qos-client-release-tag 0xkey-qos_client-vX.Y.Z

# Or fetch standalone (e.g. when the binary needs refreshing later):
python3 "$SKILL_DIR/scripts/fetch_qos_client.py" \
  --release-tag 0xkey-qos_client-vX.Y.Z \
  --out "$WORKDIR/shared/qos_client"
```

`fetch_qos_client.py` auto-detects the platform via `uname`, downloads the
binary plus its `.sha256` sidecar, refuses to install on hash mismatch
(quarantining the bad binary at `<out>.tainted`), and prints a manual
fallback recipe (curl + `gh release download` + `shasum -a 256 -c`) when
the network or release path is unreachable. Doctor (`enclave_keyops.py
doctor *`) detects a missing binary and re-prints the exact `fetch_qos_client.py`
command but never auto-runs it.

A private mirror of the same release works the same way: pass
`--repo <org>/<repo>` and export `GH_TOKEN` (or `GITHUB_TOKEN`) in the
fetch environment.
