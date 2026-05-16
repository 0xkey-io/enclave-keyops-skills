# Operator start prompts

Minimal prompts for users who only know their role and a few path inputs. The
agent reads the matching `references/roles/<role>.md`, classifies state,
runs safe commands, and asks only for missing inputs or human gates.

> **Default env is prod (or any non-staging).** Operator prompts and agent
> replies should NOT mention staging unless the user explicitly says they're
> working on the 0xkey staging cluster. See coordinator.md for `--env staging`
> as an opt-in shortcut.

## Builder

```text
我是 0xkey KeyOps 的 Builder / Release 角色。
请使用 0xkey-keyops-builder skill，只执行 Builder 角色流程。
我的工作目录是：<workdir>
目标环境：<prod | staging>
AWS account / region：<account-id> / <region>（prod 必填；staging 可不填走预设）
ECR registry host：<host or unknown>（一般 <account-id>.dkr.ecr.<region>.amazonaws.com）
我已有的源码目录：repos/enclave=<path or unknown>；repos/services=<path or unknown>
源码 git ref：repos/enclave=<sha or branch or unknown>；repos/services=<sha or branch or unknown>
需要发布的 operator-client 平台：<linux/amd64,darwin/arm64,linux/arm64 or unknown>
（这一项最终由 Coordinator 的 member-roster 决定；若不确定先填 unknown）
请先判断 state/found/missing/next；材料齐全的步骤请说明目的后直接执行。
```

## Coordinator

```text
我是 0xkey KeyOps 的 Deployment Coordinator。
请使用 0xkey-keyops-coordinator skill，只执行 Coordinator 角色流程。
我的工作目录是：<workdir>
目标环境是：<account/region/cluster/role-arn>
我已有的 public materials / builder handoff / member bundles 路径是：<paths or unknown>
请先判断 state/found/missing/next；非危险步骤请说明目的后直接执行。
```

> `<alias>` 与 `<n>` 必须是 Coordinator 在 `member-roster.json` 里分配给你的值，
> 不能自己取。不知道就先去问 Coordinator 要这一行。

## Manifest Set member

```text
我是 0xkey KeyOps 的 Manifest Set 成员，Coordinator 分配给我的 alias 是 <alias>。
请使用 0xkey-keyops-manifest skill，只执行 Manifest Set member 角色流程。
我的工作目录是：<workdir>
Vault mode（长期私钥的承载形态）：<yubikey | file>
  （prod 推荐 yubikey；staging/dev 才用 file。见 SECURITY.md §5.1）
我的 external secret 绝对路径是：<secret-path or unknown or n/a-yubikey>
  （仅在 vault mode = file 时填；yubikey 模式下填 n/a-yubikey）
我收到的 review bundle 路径是：<path or unknown>
请先判断 state/found/missing/next；非危险步骤请说明目的后直接执行。
```

## Share Set member

```text
我是 0xkey KeyOps 的 Share Set 成员，Coordinator 分配给我的 alias 是 <alias>，
member-index 是 <n>。
请使用 0xkey-keyops-share skill，只执行 Share Set member 角色流程。
我的工作目录是：<workdir>
Vault mode（长期私钥的承载形态）：<yubikey | file>
  （prod 推荐 yubikey；staging/dev 才用 file。见 SECURITY.md §5.1）
我的 external secret 绝对路径是：<path or unknown or n/a-yubikey>
  （成员自有的长期私钥。vault mode = file 时是外部 vault 中的 .secret，例如
   $HOME/0xkey/operator-keys/<alias>/<alias>.secret；yubikey 模式下填 n/a-yubikey）
我的 external share 绝对路径是：<path or unknown；first ceremony 时还没有>
  （由 Coordinator 通过 genesis-output bundle 下发，本地用 ceremony share-extract
   解出，写到外部 vault 目录；与 secret 是不同的两件东西。**始终是外部文件**，
   即使 vault mode = yubikey 也不放在 YubiKey 上）
我收到的 genesis-output bundle 路径是：<path or unknown；first ceremony 必需>
我收到的 share-request bundle 路径是：<path or unknown>
请先判断 state/found/missing/next；非危险步骤请说明目的后直接执行。
```
