# Share Set Member Workflow

Use this file when the user says they are a Share Set member, quorum share holder,
or need to re-encrypt a share from a share-request bundle.

This role is deliberately local-only. Do not ask the user for AWS credentials,
kubectl access, kubeconfig, EKS context, VPC details, or Cloudflare details.

## Goal

Verify the coordinator's share-request bundle, use the member's local
`.secret/.share` to run `proxy-re-encrypt-share`, and return a wrapped-shares
bundle to the coordinator.

## Inputs The User Must Provide

Ask for paths, not file contents:

- `share-request bundle`: coordinator-provided `share-request-*.tgz`
- `member secret`: absolute path to local `*.secret` for this alias, stored
  outside `$WORKDIR`
- `member share`: absolute path to local `*.share` from Genesis for this alias,
  stored outside `$WORKDIR`
- `qos_client`: operator-runnable binary or wrapper from the same audited qOS
  source revision / signed release bundle. On macOS arm64, prefer a native
  `darwin/arm64` client; do not require the member to execute a `linux/amd64`
  release binary directly.
- optional `qos_client_sha256_expected`: expected SHA256
- `alias`: assigned by the Coordinator in `member-roster.json` (e.g.
  `share-member2`); members must NOT pick this themselves
- `member_index`: integer slot assigned by the Coordinator in
  `member-roster.json` (e.g. `2`); becomes permanent after Genesis
- `workdir`: repo-external directory, e.g. `~/0xkey/keyops/share-member2`
  (默认不带环境分段；如果用户明确说自己在 staging，可以把 `/staging/` 加到
  这一段做隔离：`~/0xkey/keyops/staging/share-member2`)

Never print or request the contents of `.secret` or `.share`.

## Execution Style

When inputs are present and the action is inside the member `$WORKDIR` or an
explicit user-provided path, execute the command directly and state its purpose
briefly. Do not hand the user copy/paste commands as the normal workflow.

Stop for user input only when a required artifact is missing, the external
`.secret` / `.share` absolute paths are needed, or a human confirmation gate is
reached (`proxy-re-encrypt-share`).

## Access Scope

Operate only inside this member's `$WORKDIR` and exact paths the user explicitly
provides. For a clean ceremony, accepted inputs are:

- `$WORKDIR/shared/qos_client`
- `$WORKDIR/inbox/share-request-*.tgz`
- `$WORKDIR/outbox/<alias>.pub`
- exact source paths named by the user for copying non-sensitive inputs into the
  workdir

Do not search `$HOME`, Coordinator workspaces, legacy staging key archives, old
ceremony directories, or other member directories to find `.secret`, `.share`,
`.pub`, or share-request bundles. If the expected input is absent, stop and tell
the user where to place it.

Member `.secret` and `.share` must not be copied into `$WORKDIR`. The user
provides only absolute paths to the external key location, for example:

```text
~/0xkey/operator-keys/share-member2/share-member2.secret
~/0xkey/operator-keys/share-member2/share-member2.share
```

(Default layout has no environment segment — prod is the implicit default.
If the user explicitly says they're working on staging, insert
`/staging/` between `operator-keys/` and `<alias>/` for clarity.)

The agent may pass those paths to `qos_client` through this skill's script, but
must never read or print the file contents. Store the public key in
`$WORKDIR/outbox/<alias>.pub`.

## Initialize Workspace

> `$SKILL_DIR` below is the absolute path of this skill on the agent's local
> filesystem. The agent that loaded this skill already knows it; resolve the
> placeholder before invoking Python.

```bash
python3 "$SKILL_DIR/scripts/role_init.py" \
  --role share-set-member \
  --root "$WORKDIR" \
  --alias "$ALIAS" \
  --member-index "$MEMBER_INDEX" \
  --qos-client-sha256 "$QOS_CLIENT_SHA256"
```

Then ensure:

```text
$WORKDIR/
  config.json
  shared/qos_client
  inbox/share-request-*.tgz
  outbox/<alias>.pub
```

Keep the external key directory private:

```bash
chmod 700 "$HOME/0xkey/operator-keys/$ALIAS"
chmod 600 "$HOME/0xkey/operator-keys/$ALIAS/$ALIAS."{secret,share}
```

## First-turn reply shape

When the user has only said "I'm a Share Set member, $WORKDIR is X" (no
alias, no paths yet), DO NOT just dump a list of placeholders to fill.
Reply in this order:

1. **state**: pick from the State Detection table below; the most common
   first-turn states are `uninitialized + waiting-for-roster +
   waiting-for-qos-client`
2. **found**: what the agent could verify by listing $WORKDIR (e.g.
   "workdir exists, empty")
3. **missing**: each item names what is missing AND who to ask for it
   (Coordinator vs user vs external vault)
4. **vault mode**: ask the user one line — "你的 long-term key 在
   YubiKey (`--yubikey`) 还是加密磁盘文件 (`--secret-path`)? prod 推荐
   YubiKey，见 SECURITY.md §5.1"。后续 `share-extract` / `reencrypt`
   的命令形态取决于这个答案；`.share` 仍然是外部 vault 中的文件，与凭据
   形态无关。
5. **next**: 1-line concrete next step

For alias / member_index specifically:
- ❌ Don't write "alias (例如 share-member2)" — that suggests the user can
  pick. Use "Coordinator 分配给你的 alias (来自 `member-roster.json`)；
  如果不知道请先去问 Coordinator".
- ❌ Don't say "or you can drop it at .../shared/qos_client" without first
  saying where it comes from. Use "向 Coordinator 索取本 ceremony 的
  operator-client 包 (binary + SHA256 + qOS revision)；不要从公网下载，
  也不要复用其他 ceremony 的旧 binary，然后告诉我落在哪个绝对路径".
- For the missing `.share` case: explicitly mention "如果没有 .share，说明
  是首次 ceremony，agent 会走 genesis-output → share-extract 分支".

## Vault mode: YubiKey vs file secret

This skill supports two equivalent shapes for the long-term credential.
**Default to the YubiKey path for prod**; only fall back to the file
path when the user explicitly says staging/dev or hardware is not
available. Never silently switch modes between commands inside one
session — pick one at first-turn and use it consistently.

| Topic                | YubiKey path (prod preferred)                                                                                       | File path (staging / dev only)                                                                  |
|----------------------|---------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| Generation command   | `key yubikey-provision` (writes `outbox/<alias>.pub`; private key stays in PIV slot, never exported)                | `key file-generate --master-seed-path <ext>/<alias>.secret`                                    |
| Where `.share` lives | **Always** an external vault file (`<ext>/<alias>.share`). YubiKey does NOT store the share; only the long-term key. | Same — `.share` is independent of `.secret` form.                                              |
| State evidence (`key-init-needed` cleared) | `outbox/<alias>.pub` present AND user confirms the YubiKey is provisioned for this alias                  | `outbox/<alias>.pub` present AND external `.secret` exists                                     |
| share-extract        | `ceremony share-extract --yubikey --alias <ALIAS> --member-index <N> --share-path <ext>/<alias>.share ...`         | `ceremony share-extract --secret-path <ext>/.secret --alias <ALIAS> --share-path <ext>/.share ...` |
| reencrypt            | `ceremony reencrypt --yubikey --alias <ALIAS> --member-index <N> --share-path <ext>/<alias>.share`                | `ceremony reencrypt --secret-path <ext>/.secret --alias <ALIAS> --share-path <ext>/.share`     |
| PIN/PUK handling     | qos_client prompts on its own TTY; **never** quoted in chat, audit log, or this skill's stdout                      | n/a; protect `.secret` with `chmod 600` and disk encryption                                    |
| Backup / loss policy | Provision at least 2 YubiKeys per alias up-front; if one is lost, use the spare and run `key-forward` to retire the lost slot | Encrypted offline copy of `.secret`; loss requires `key-forward`                               |
| qos_client requirement | Builder-released `qos_client` revision must ship with PIV support; `doctor holder` reports this                   | Any audited release works                                                                      |

> ⚠️ Mutual exclusion: passing both `--yubikey` and `--secret-path` is a
> hard error in `enclave_keyops.py`. If the user starts the session
> saying "我用 YubiKey", do not also fill in `--secret-path` "为了保险"
> — the script will refuse and you'll have to ask again.
>
> ⚠️ First-time YubiKey use: **before** running `key yubikey-provision`,
> walk the user through `SECURITY.md §5.2.1 "YubiKey 首次准备清单"`. The
> three failure modes observed in real-world testing on 2026-05-16 were
> (a) PIV `Management key algorithm: AES192` (YubiKey 5.7+ factory
> default) vs qos_client's hard-coded TDES expectation → MGM auth
> failure; (b) operator not touching the YubiKey within ~15s of the
> `Enter your pin:` prompt → `FailedToGenerateSelfSignedCert`; (c) any
> failure leaves slot 9C/9D with an orphan key but no cert, so the next
> `provision-yubikey` will collide on `WillNotOverwriteSlot` and the
> only recovery is `ykman piv reset --force` + re-switch to TDES. If the
> user has never run provision against this exact YubiKey before, ask
> them to run `ykman piv info` first and confirm the four properties
> §5.2.1 step 1 lists.
>
> ⚠️ `.share` is NEVER on the YubiKey. Even in YubiKey mode the agent
> still requires an external `--share-path` outside the role workdir for
> both `share-extract` (write) and `reencrypt` (read).

## State Detection

Before running commands, inspect only `$WORKDIR` and classify the state.

> **Precedence rule (roster-first, see
> `references/workspace-rules.md` "Roster-first rule")**: if state includes
> `waiting-for-roster`, do **not** run `role_init.py --alias <user-claim>
> --member-index <user-claim>`, do **not** generate
> `outbox/<user-claim>.pub`, and do **not** treat the user-claimed
> `(alias, member_index)` pair as authoritative. Stop and ask the
> Coordinator to publish `member-roster.json` first. This rule overrides
> the literal "run `role_init.py` for this alias/member index" cell in the
> `uninitialized` row below whenever the two states co-occur.
>
> Special case for first ceremony: `.share` only exists AFTER the
> Coordinator's `ceremony genesis-boot` ships a `genesis-output-*.tgz`
> bundle AND this member runs `ceremony share-extract`. Because
> `ceremony share-extract` requires `--secret-path`, the external `.secret`
> must already exist before `waiting-for-genesis-output-bundle` can be
> resolved. Sequence for a brand-new first-ceremony member is therefore:
> roster → qos_client → key-init → genesis-output bundle → share-extract.

| State | Directory evidence | Next action |
|-------|--------------------|-------------|
| `uninitialized` | missing `config.json` | run `role_init.py` for this alias/member index — but only if `waiting-for-roster` is NOT also active (see precedence rule above) |
| `waiting-for-roster` | user said `<alias>` or `<n>` is `unknown`, OR alias/index does not appear in any received `BUNDLE.json.members.share_set` slice, OR no Coordinator-issued roster has been provided yet | ask Coordinator for the assigned alias and member-index from `member-roster.json`; do not let the user pick values themselves (collisions break Genesis irreversibly); do not bake the user-claimed pair into `config.json` |
| `waiting-for-qos-client` | missing `shared/qos_client` or `config.json.qos_client_sha256_expected` | tell user to ask Coordinator to forward the Builder's operator-client release for this platform (binary + SHA256 + qOS revision); do not download from random sources, do not reuse a different ceremony's binary |
| `key-init-needed` | missing `outbox/<alias>.pub`; for file mode also missing external `.secret` path | YubiKey path: confirm slot is provisioned and run `key yubikey-provision`. File path: propose `$HOME/0xkey/operator-keys/<env>/<alias>/<alias>.secret` and run `key file-generate` after confirmation. (See "Vault mode" above.) |
| `waiting-for-genesis-output-bundle` | has the chosen holder credential (YubiKey OR external `.secret`) AND `outbox/<alias>.pub`, missing `inbox/genesis-output-*.tgz` and missing external `.share` (cannot precede `key-init-needed` — `ceremony share-extract` needs `--yubikey` OR `--secret-path`) | ask Coordinator to send the Genesis-output bundle |
| `ready-to-extract-share` | has `shared/qos_client`, the chosen holder credential, and `inbox/genesis-output-*.tgz` | run `bundle extract`, `bundle verify`, then `ceremony share-extract` (with `--yubikey` OR `--secret-path`) to write `.share` to the external key vault |
| `waiting-for-share-request` | has holder credential AND external `.share`, missing `inbox/share-request-*.tgz` | ask user to place Coordinator share-request bundle in `inbox/` |
| `ready-to-reencrypt` | has `shared/qos_client`, holder credential, external `.share`, and share-request bundle | run holder doctor, extract, verify, summarize |
| `wrapped-shares-ready` | wrapped-shares bundle exists under `outbox/` | tell user to send only `.tgz` + `.sha256` to Coordinator |
| `blocked` | checksum mismatch, missing qos_client, bad bundle, or user has not approved | report blocker and stop |

Share members do not run `kubectl`, but the Coordinator may quote
`/qos/enclave-health` states when explaining why a step is delayed. Reference:

| `enclave-health` state | What it means for this role |
|------------------------|----------------------------|
| `WaitingForBootInstruction` | Genesis target / data-plane pod is up; nothing for this role to do yet |
| `GenesisBooted` | The Coordinator just finished `boot-genesis`; the next thing to expect is a `genesis-output-*.tgz` bundle for `ceremony share-extract` |
| `WaitingForQuorumShards` | `boot-standard` finished; the Coordinator will (or already has) ship a `share-request-*.tgz` bundle for `ceremony reencrypt` |
| `QuorumKeyProvisioned` | This service is fully provisioned; nothing further is required from this role |

If the member does not already have a key, this skill supports generating
one in either a YubiKey PIV slot (prod default) or a file outside
`$WORKDIR` (staging / dev only). Pick the form that matches what the user
declared at first-turn under "Vault mode" — do not silently swap.

**YubiKey path (prod default).** Private key stays on the YubiKey and is
not exportable; only the public key lands in this workdir.

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  key yubikey-provision \
  --alias "$ALIAS" \
  --pub-path "outbox/$ALIAS.pub"
```

**File path (staging / dev only).** Write the secret outside `$WORKDIR`
and only write the public key into this workdir:

```bash
KEY_DIR="$HOME/0xkey/operator-keys/$ALIAS"
mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR"
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  key file-generate \
  --master-seed-path "$KEY_DIR/$ALIAS.secret" \
  --pub-path "outbox/$ALIAS.pub"
chmod 600 "$KEY_DIR/$ALIAS.secret"
```

Before running generation, state the paths/slot that will be created and
ask the user to confirm if either output already exists. Genesis `.share`
is **not** generated by either command; it is produced inside the Genesis
enclave by Coordinator and decrypted by this member through
`ceremony share-extract` (see Genesis below).

## Genesis: extract this member's share (first ceremony only)

Skip this section if `$KEY_DIR/$ALIAS.share` already exists from a prior
ceremony. Otherwise:

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle extract \
  --archive inbox/genesis-output-*.tgz \
  --bundle-dir incoming/genesis-output

GEN_ROOT=$(find "$WORKDIR/incoming/genesis-output" -name SHA256SUMS -maxdepth 3 -type f -print -quit | xargs dirname)
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle verify --bundle-dir "$GEN_ROOT"

Use exactly one holder-credential flag — either `--yubikey` (prod) or
`--secret-path <ext>/.secret` (staging / dev). Passing both is a hard
error.

YubiKey path:

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony share-extract \
  --alias "$ALIAS" \
  --member-index "$MEMBER_INDEX" \
  --yubikey \
  --share-path "$KEY_DIR/$ALIAS.share" \
  --namespace-dir "$GEN_ROOT/genesis-output"
chmod 600 "$KEY_DIR/$ALIAS.share"
```

File path (staging / dev only):

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony share-extract \
  --alias "$ALIAS" \
  --member-index "$MEMBER_INDEX" \
  --secret-path "$KEY_DIR/$ALIAS.secret" \
  --share-path "$KEY_DIR/$ALIAS.share" \
  --namespace-dir "$GEN_ROOT/genesis-output"
chmod 600 "$KEY_DIR/$ALIAS.share"
```

The `--share-path` MUST point at the external key vault, not the role
workdir; the script will refuse a workdir-internal path. After extraction,
confirm the file size and SHA256 are recorded in the audit log; never paste
the share contents into chat.

## Run The Role

The commands below describe the implementation sequence. In an agent session,
execute them directly when inputs are present; do not ask the user to copy/paste
them.

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  doctor holder

python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle extract \
  --archive inbox/share-request-*.tgz \
  --bundle-dir incoming/share-request
```

Find and verify the extracted root:

```bash
REQUEST_ROOT=$(find "$WORKDIR/incoming/share-request" -name SHA256SUMS -maxdepth 3 -type f -print -quit | xargs dirname)
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle verify --bundle-dir "$REQUEST_ROOT"
```

Before re-encrypting, summarize for the user:

- five service names
- manifest namespace and nonce per service
- attestation file names and SHA256
- PCR3 preimage role ARN basename/account
- approvals included per service

Ask for explicit approval. Do not use `--unsafe-auto-confirm` unless the user
explicitly says this is staging/test and wants non-interactive re-encryption.

Run re-encryption. **Use exactly one** holder-credential flag — either
`--yubikey` (prod) or `--secret-path <ext>/.secret` (staging / dev).
`--share-path` is always required and always points to the external
vault.

YubiKey path:

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony reencrypt \
  --alias "$ALIAS" \
  --member-index "$MEMBER_INDEX" \
  --yubikey \
  --share-path "$KEY_DIR/$ALIAS.share"
```

File path (staging / dev only):

```bash
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  ceremony reencrypt \
  --alias "$ALIAS" \
  --member-index "$MEMBER_INDEX" \
  --secret-path "$KEY_DIR/$ALIAS.secret" \
  --share-path "$KEY_DIR/$ALIAS.share"
```

Create the return bundle:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
python3 "$SKILL_DIR/scripts/enclave_keyops.py" \
  --config "$WORKDIR/config.json" --workdir "$WORKDIR" \
  bundle create --kind wrapped-shares \
  --bundle-dir "outbox/${ALIAS}-wrapped-shares-${STAMP}" \
  --archive "outbox/${ALIAS}-wrapped-shares-${STAMP}.tgz"
shasum -a 256 "$WORKDIR/outbox/${ALIAS}-wrapped-shares-${STAMP}.tgz" \
  > "$WORKDIR/outbox/${ALIAS}-wrapped-shares-${STAMP}.tgz.sha256"
```

## Output To User

Tell the user to send only these files to the coordinator:

- `outbox/<alias>-wrapped-shares-<stamp>.tgz`
- `outbox/<alias>-wrapped-shares-<stamp>.tgz.sha256`

Do not include `.secret`, `.share`, extracted bundle directories, or local audit
logs unless the user explicitly requests an internal audit handoff.
