# Operator start prompts

Minimal prompts for users who only know their role and a few path inputs. The
agent reads the matching `references/roles/<role>.md`, classifies state,
runs safe commands, and asks only for missing inputs or human gates.

> **Default env is prod.** Operator prompts should collect explicit environment
> identifiers instead of relying on hidden presets.
>
> If the operator does not provide a workdir, recommend the role default and wait
> for confirmation before initializing: `~/.0xkey-ops/coordinator`,
> `~/.0xkey-ops/builder`, `~/.0xkey-ops/manifest-set/<alias>`, or
> `~/.0xkey-ops/share-set/<alias>`. For Manifest / Share roles, `<alias>` must
> come from the Coordinator roster first.

## Builder

```text
I am the 0xkey KeyOps Builder / Release operator.
Use the 0xkey-keyops-builder skill and run only the Builder workflow.
My workdir is: <workdir>
  (recommended default if unsure: ~/.0xkey-ops/builder)
Target environment: <prod or explicit env name>
AWS account / region: <account-id> / <region> (required)
ECR registry host: <host or unknown> (usually <account-id>.dkr.ecr.<region>.amazonaws.com)
Source directories: repos/enclave=<path or unknown>; repos/services=<path or unknown>
Source git refs: repos/enclave=<sha or branch or unknown>; repos/services=<sha or branch or unknown>
Required operator-client platforms: <linux/amd64,darwin/arm64,linux/arm64 or unknown>
(Coordinator's member-roster decides the final list; use unknown if unsure.)
First report state/found/missing/next; execute safe ready steps directly after stating their purpose.
```

## Coordinator

```text
I am the 0xkey KeyOps Deployment Coordinator.
Use the 0xkey-keyops-coordinator skill and run only the Coordinator workflow.
My workdir is: <workdir>
  (recommended default if unsure: ~/.0xkey-ops/coordinator)
Target environment: <account/region/cluster/role-arn>
Existing public materials / builder handoff / member bundles: <paths or unknown>
First report state/found/missing/next; execute non-dangerous ready steps directly after stating their purpose.
```

`<alias>` and `<n>` must be assigned by the Coordinator in
`member-roster.json`. Members must not choose them. If unknown, ask the
Coordinator for the roster row first.

## Manifest Set member

```text
I am a 0xkey KeyOps Manifest Set member. Coordinator assigned my alias: <alias>.
Use the 0xkey-keyops-manifest skill and run only the Manifest Set member workflow.
My workdir is: <workdir>
  (recommended default if unsure: ~/.0xkey-ops/manifest-set/<alias>)
Vault mode for the long-term key: <yubikey | file>
  (prod prefers yubikey; use file only for explicit non-production/debug work; see SECURITY.md section 5.1)
My external secret absolute path: <secret-path or unknown or n/a-yubikey>
  (only for vault mode = file; use n/a-yubikey for YubiKey mode)
Review bundle received: <path or unknown>
First report state/found/missing/next; execute non-dangerous ready steps directly after stating their purpose.
```

## Share Set member

```text
I am a 0xkey KeyOps Share Set member. Coordinator assigned my alias: <alias>,
and member-index: <n>.
Use the 0xkey-keyops-share skill and run only the Share Set member workflow.
My workdir is: <workdir>
  (recommended default if unsure: ~/.0xkey-ops/share-set/<alias>)
Vault mode for the long-term key: <yubikey | file>
  (prod prefers yubikey; use file only for explicit non-production/debug work; see SECURITY.md section 5.1)
My external secret absolute path: <path or unknown or n/a-yubikey>
  (the member long-term key; in file mode this is an external vault .secret such as
   $HOME/0xkey/operator-keys/<alias>/<alias>.secret; use n/a-yubikey for YubiKey mode)
My external share absolute path: <path or unknown; absent before first ceremony share-extract>
  (Coordinator ships it through genesis-output; this member extracts it into the external vault.
   It is separate from the secret and is always an external file, even in YubiKey mode.)
Genesis-output bundle received: <path or unknown; required for first ceremony>
Share-request bundle received: <path or unknown>
First report state/found/missing/next; execute non-dangerous ready steps directly after stating their purpose.
```
