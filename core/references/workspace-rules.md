# Workspace, agent execution, and key storage rules

These apply to **every** role. Each role doc inherits them; if a role doc
contradicts this file, this file wins.

## Global workspace rules

Every role run starts by identifying the role workspace (`$WORKDIR`) and
reading only:

- files under that role's `$WORKDIR`
- files under a role-specific `inbox/` / `outbox/` / `shared/` directory for
  the current ceremony round
- exact paths explicitly provided by the user in the prompt

Do **not** broadly search `$HOME`, legacy key archives, old ceremony
directories, previous build outputs, or unrelated role directories to "find"
key material or bundles. If such files are discovered while diagnosing a
problem, report them as possible legacy material and ask before importing or
copying.

Every role response should first classify the workspace state and then
recommend the next action. At minimum report:

- `state`: e.g. `uninitialized` / `waiting-for-qos-client` / `ready-to-run`
  / `output-ready` / `blocked` (per the role's State Detection table)
- `found`: relevant files present in the allowed directories
- `missing`: exact files or user inputs needed next
- `next`: one safe next action; if runnable, execute it instead of only
  printing the command

## Agent execution rule

Do not merely print commands for the user to run. Once required inputs are
known and the action is inside this role's access scope, the agent should
execute the relevant command itself via tools, while briefly explaining the
command's purpose before running it.

Stop and ask the user only when:

- a required input path or artifact is missing
- a sensitive key path is needed (`.secret` / `.share`); ask for the
  absolute path, never for file contents
- the step requires a human gate from `SECURITY.md` (`approve-manifest`,
  `proxy-re-encrypt-share`, `kubectl apply`, `post-share`, unsafe skips)
- the command would operate outside the role's `$WORKDIR`, allowed
  inbox/outbox/shared paths, or explicitly provided paths

Command snippets in the role docs are reference material for auditability and
debugging. During a role workflow, prefer direct execution with a short
purpose statement over instructing the user to copy/paste commands.

## Member key storage rule

Manifest / Share member long-term key files (`.secret`, master seed) and
Genesis shares (`.share`) must live outside the role workdir, for example
under `~/0xkey/operator-keys/<env>/<alias>/` or a mounted encrypted key
vault. The role workdir is for current-round bundles, public keys, approvals,
wrapped-share outputs, and logs; it should be disposable.

Rule of thumb: **workdir may be disposable; key vault must be durable.
Public material may be copied into workdir; private material must only be
referenced by absolute path.** Public material includes member `.pub` files,
quorum public keys, hashes, review / share-request bundles, and other
coordinator inputs that can be recreated or redistributed.

Agents must never ask the user to paste key contents. The user may provide an
absolute path to the external key file; the agent passes that path to the
KeyOps script / `qos_client` but must not read the file. The CLI rejects
sensitive paths inside the role workdir for `key file-generate`, `manifest
approve`, `ceremony reencrypt`, and `ceremony share-extract`.

DR (disaster-recovery) private keys are subject to the same rule and must
live in an external vault, never in any role workdir. Only `dr-key.pub`
enters a Coordinator workdir.

## Roster-first rule (alias must come from Coordinator)

`alias` 与（Share 角色的）`member_index` 由 Coordinator 在
`shared/member-roster.json` 中单方面分配（详见
`references/roles/coordinator.md` `Alias / member-index assignment` 与
PRINCIPLES §11）。成员**只能确认不能自取**。在 agent 这一层落实为：

- 不允许用户自报的 alias 在 **没有任何 roster 背书** 时被烧进
  `config.json`、写入 `outbox/<alias>.pub`、或作为命令行参数固化到
  `role_init.py`。
- "有 roster 背书" 指以下任一：
  1. 用户在 prompt 里直接给出了 `member-roster.json` 的内容或绝对路径；
  2. 用户给出了一个含 `BUNDLE.json.members.<set>` slice 的 bundle（review
     / share-request / genesis-output），且该 slice 包含用户口报的
     `(alias, member_index?)`；
  3. 用户引用了一个由 Coordinator 签名的 roster 公告，agent 可以核对其中
     该 alias 是否存在。
- 在以上任何一种背书到位之前，state 必须叠加 `waiting-for-roster`，且：
  - **不要**调 `role_init.py` 时携带 `--alias <用户自报值>`；可以先停下
    要 roster，也可以仅做 `--role <role>` 不带 alias 的最小初始化（脚本
    会落一个明确的角色默认值如 `manifester1` / `share-member1`，配合
    README 提示"等 roster 到位后用 `--force --alias <roster 值>`
    覆写"）。优先选择"先停下要 roster"。
  - **不要**生成 `outbox/<user-alias>.pub`；先让 Coordinator 公布
    roster，再以 roster 上的 alias 为唯一文件名生成。
  - **不要**接受用户"我自己起个 alias 就行" 的提议——alias 撞名会让
    `<alias>.pub` 互相覆盖、approval 落进错误槽位，且 Genesis 之后无法修
    复（见 PRINCIPLES §11）。

这条规则对 Manifest Set member、Share Set member、（以及任何未来新增的成
员角色）都适用，是 state-detection 表的**前置约束**：即便表里 `uninitialized`
行的 evidence 看起来只问 "missing `config.json`"，只要 `waiting-for-roster`
同时叠加，就**不要**进 init。

## Member key generation support

Manifest / Share members may start without an existing `.secret` / `.pub`. If
the user says the secret is `unknown`, absent, or asks the agent to generate
it, the agent should offer the default external key-vault path and, after the
user confirms creation, run `key file-generate` directly:

```text
$HOME/0xkey/operator-keys/<env>/<alias>/<alias>.secret
$WORKDIR/outbox/<alias>.pub
```

The agent must:

- create the external key directory with `0700`
- generate the secret outside `$WORKDIR`
- generate the `.pub` in `$WORKDIR/outbox/`
- set the secret mode to `0600` after generation
- never generate `.secret` inside role workdir
- never overwrite an existing `.secret` or `.pub` without explicit user
  confirmation

`key file-generate` only produces a member long-term key. Genesis `.share`
files are produced in a different way: the Coordinator runs `ceremony
genesis-boot`, ships a `genesis-output` bundle, and each Share member runs
`ceremony share-extract` to write their `.share` into the same external
vault directory.
