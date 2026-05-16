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
