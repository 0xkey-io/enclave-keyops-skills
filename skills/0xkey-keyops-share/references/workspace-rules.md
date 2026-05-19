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

If the user has not provided a workspace, do not create one silently and do
not run `role_init.py` yet. Recommend exactly one default path for the role,
then wait for the user to confirm or override it:

- Coordinator: `~/.0xkey-ops/coordinator`
- Builder: `~/.0xkey-ops/builder`
- Manifest Set member: `~/.0xkey-ops/manifest-set/<alias>`
- Share Set member: `~/.0xkey-ops/share-set/<alias>`

For Manifest / Share roles, only fill `<alias>` after the Coordinator roster
backs it. If the alias is not known yet, show the placeholder path and ask for
`member-roster.json` (or a Coordinator-signed roster announcement) first.

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

Coordinator assigns `alias` and, for Share Set members, `member_index` in
`shared/member-roster.json` (see `references/roles/coordinator.md` and
PRINCIPLES section 11). Members confirm assignments but do not choose them.

Do not write a user-claimed alias into `config.json`, `outbox/<alias>.pub`, or
`role_init.py` arguments until it is backed by a Coordinator roster.

Roster backing means one of:

1. the user provided `member-roster.json` contents or an absolute path;
2. the user provided a review / share-request / genesis-output bundle whose
   `BUNDLE.json.members.<set>` slice contains the claimed assignment;
3. the user references a Coordinator-signed roster announcement that the agent
   can check.

Until roster backing exists, add `waiting-for-roster` to state and:

- do not run `role_init.py`; the script refuses member init without
  roster-backed `--alias` and, for Share, `--member-index`;
- do not generate `outbox/<user-alias>.pub`;
- do not accept proposals to "just pick an alias"; collisions can overwrite
  `.pub` files, mis-slot approvals, and become unrecoverable after Genesis.

This rule applies to Manifest Set members, Share Set members, and future member
roles. It is a precedence constraint over state-detection rows: even if
`config.json` is missing, do not initialize while `waiting-for-roster` is also
active.

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
