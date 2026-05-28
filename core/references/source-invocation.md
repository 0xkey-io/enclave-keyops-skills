# Source Invocation (Maintainer / Developer Reference)

This file documents the Python source-mode invocation patterns for the
`keyops` CLI. **Operators should use the self-contained `keyops` binary
(no Python required).** Source invocation is only needed when:

- developing or debugging this repository
- running on a platform where the PyInstaller binary is not available
- CI pipelines that test against the source tree

Source scripts live in `dist/src/` (the PyInstaller build source).
They are **not** distributed inside the operator-facing skill packages.

To enable source-mode, set the environment variable:

```bash
export KEYOPS_SOURCE_MODE=1
```

Without this variable, direct `python3` invocations of any file in
`dist/src/` are rejected with an error message pointing to the binary.

## Translation Table

| Binary form | Source form |
|-------------|------------|
| `keyops --version` | `KEYOPS_SOURCE_MODE=1 python3 dist/src/keyops_main.py --version` |
| `keyops init --role <role> ...` | `KEYOPS_SOURCE_MODE=1 python3 dist/src/role_init.py --role <role> ...` |
| `keyops fetch-qos-client ...` | `KEYOPS_SOURCE_MODE=1 python3 dist/src/fetch_qos_client.py ...` |
| `keyops fetch-keyops ...` | `KEYOPS_SOURCE_MODE=1 python3 dist/src/fetch_keyops.py ...` |
| `keyops --config C --workdir W <subcommand> ...` | `KEYOPS_SOURCE_MODE=1 python3 dist/src/enclave_keyops.py --config C --workdir W <subcommand> ...` |

## Source-mode init examples

Replace `$REPO_ROOT` with the absolute path of this repository on disk.

### Coordinator

```bash
KEYOPS_SOURCE_MODE=1 python3 "$REPO_ROOT/dist/src/role_init.py" \
  --role coordinator \
  --root "$WORKDIR" \
  --account-id "$AWS_ACCOUNT_ID" \
  --region "$AWS_REGION" \
  --cluster "$EKS_CLUSTER" \
  --enclave-role-name "$ENCLAVE_NODE_ROLE_NAME" \
  --kustomize-overlay-path "$ENCLAVE_OVERLAY_ABSOLUTE_PATH"
```

### Builder

```bash
KEYOPS_SOURCE_MODE=1 python3 "$REPO_ROOT/dist/src/role_init.py" \
  --role builder \
  --root "$WORKDIR"
```

### Manifest Set member

```bash
KEYOPS_SOURCE_MODE=1 python3 "$REPO_ROOT/dist/src/role_init.py" \
  --role manifest-set-member \
  --root "$WORKDIR" \
  --alias "$ALIAS"
```

### Share Set member

```bash
KEYOPS_SOURCE_MODE=1 python3 "$REPO_ROOT/dist/src/role_init.py" \
  --role share-set-member \
  --root "$WORKDIR" \
  --alias "$ALIAS" \
  --member-index "$MEMBER_INDEX"
```

## Source-mode enclave_keyops examples

Replace `keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR"` with:

```bash
KEYOPS_SOURCE_MODE=1 python3 "$REPO_ROOT/dist/src/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR"
```

All subcommands and flags remain identical.
