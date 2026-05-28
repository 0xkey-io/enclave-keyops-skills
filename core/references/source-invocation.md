# Source Invocation (Maintainer / Developer Reference)

This file documents the Python source-mode invocation patterns for the
`keyops` CLI. **Operators should use the self-contained `keyops` binary
(no Python required).** Source invocation is only needed when:

- developing or debugging this repository
- running on a platform where the PyInstaller binary is not available
- CI pipelines that test against the source tree

## Translation Table

| Binary form | Source form |
|-------------|------------|
| `keyops --version` | `python3 scripts/keyops_main.py --version` |
| `keyops init --role <role> ...` | `python3 scripts/role_init.py --role <role> ...` |
| `keyops fetch-qos-client ...` | `python3 scripts/fetch_qos_client.py ...` |
| `keyops fetch-keyops ...` | `python3 scripts/fetch_keyops.py ...` |
| `keyops --config C --workdir W <subcommand> ...` | `python3 scripts/enclave_keyops.py --config C --workdir W <subcommand> ...` |

The `$SKILL_DIR` placeholder in source-mode commands is the absolute path of
the installed skill on the agent's local filesystem. The agent that loaded
the skill already knows it; resolve the placeholder before invoking Python.

## Source-mode init examples

### Coordinator

```bash
python3 "$SKILL_DIR/scripts/role_init.py" \
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
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role builder \
  --root "$WORKDIR"
```

### Manifest Set member

```bash
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role manifest-set-member \
  --root "$WORKDIR" \
  --alias "$ALIAS"
```

### Share Set member

```bash
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role share-set-member \
  --root "$WORKDIR" \
  --alias "$ALIAS" \
  --member-index "$MEMBER_INDEX"
```

## Source-mode enclave_keyops examples

Replace `keyops --config "$WORKDIR/config.json" --workdir "$WORKDIR"` with:

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR"
```

All subcommands and flags remain identical.
