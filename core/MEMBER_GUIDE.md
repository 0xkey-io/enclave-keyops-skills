# 0xkey KeyOps 成员引导

> 面向 Manifest Set Member 和 Share Set Member 的短版引导。
> 如果你不是在操作自己的成员角色，不要继续往下执行。

这份文档给群里的成员使用。你不需要理解部署、Kubernetes、镜像构建或
qOS 内部细节；你只需要按自己的角色，让 agent 使用正确的 skill，完成一
个边界清晰的本地流程。

## 1. 先确认你要读哪一章

先看公共章节，然后只读与你角色相关的章节。

- 如果你是 Manifest Set Member：读第 1-5 章、第 8-10 章。
- 如果你是 Share Set Member：读第 1-4 章、第 6-10 章。
- 如果你同时承担 Manifest 和 Share 两个角色：必须开两个独立 agent 会话。
- 每个角色都使用自己的 workdir、alias、bundle 和回报格式。

不要在同一个 agent 会话里混跑 Manifest 和 Share。Manifest 会话只使用
`0xkey-keyops-manifest`；Share 会话只使用 `0xkey-keyops-share`。

## 2. 这次操作是在做什么

0xkey KeyOps ceremony 会把关键操作拆给不同成员独立完成。成员侧只做可验证
的小步骤：

- Manifest Set Member 审阅 `manifest-review`，确认后生成 `approvals`。
- Share Set Member 使用自己的 `.share`，生成 `wrapped-shares`。

你不会被要求部署服务、运行 `kubectl`、构建镜像、修改服务配置，或替别人
操作密钥。

你必须自己确认三件事：

- `alias` / `member_index` 来自 `member-roster.json`，不是自己取名。
- `.secret` / `.share` 放在 workdir 外部，agent 只能拿到路径。
- 最终产物的文件名和 SHA256 正确回报。

完整流程以 [`core/WORKFLOWS.md`](WORKFLOWS.md) 为准。成员只需要知道自己会
参与其中一段：Manifest 成员在 `manifest-review` 阶段交回 `approvals`；
Share 成员在 Genesis 时提取自己的 `.share`，或在后续 ceremony 交回
`wrapped-shares`。

## 3. 安装 KeyOps Skill

开始任何成员操作前，先按你使用的 agent IDE 安装对应 skill。安装方式以
仓库 README 的
[`Install / use with agents`](../README.md#install--use-with-agents)
为准：

- Manifest Set Member 安装 `0xkey-keyops-manifest`。
- Share Set Member 安装 `0xkey-keyops-share`。
- 同时承担两个角色时，两个 skill 都安装，但仍然分开 agent 会话使用。

如果你的 agent 找不到 `0xkey-keyops-manifest` 或 `0xkey-keyops-share`，
先停止角色操作，回到安装检查。不要让 agent 临时猜流程。

## 4. 所有人都要遵守的安全规则

只记住这几条：

- 不要把 `.secret` 或 `.share` 的内容粘贴到聊天里。
- 不要把 `.secret` 或 `.share` 放进成员 workdir。
- 不要把 `.secret` 或 `.share` 发到群里、issue、PR 或任何共享目录。
- agent 可以拿到文件路径，但不能打印、读取或复制密钥内容。
- `alias` 和 `member_index` 必须来自 `member-roster.json`。

长期密钥有两种形态：

- YubiKey：生产环境推荐。私钥不导出。
- 外部 `.secret` 文件：注意保管，必须在 workdir 外部。

Share 成员还会有 `.share`。注意：`.share` 永远是外部 vault 文件，即使你的
长期密钥在 YubiKey 上，`.share` 也不在 YubiKey 上。

## 5. Manifest Set Member

本章只面向 Manifest Set Member。不要在这个会话里处理 `.share`，也不要运行
Share 或 Coordinator 的操作。

### 5.1 你的职责

你负责审阅 Coordinator 给你的 `manifest-review-*.tgz`。确认摘要符合本次说明
后，用自己的长期密钥生成 `approvals bundle`，再把 bundle 和 SHA256 回报。

Manifest 成员不做这些事：

- 不做 share re-encrypt。
- 不处理 `.share`。
- 不运行 `post-share`。
- 不运行 `kubectl` 或部署命令。
- 不替别人的 alias 签名。

### 5.2 Genesis：首次成为 Manifest 成员

Genesis 对 Manifest 成员来说，重点是初始化自己的长期密钥，并把 public key
交回。它不是每次 ceremony 都要做。

你需要准备：

- 已安装 `0xkey-keyops-manifest`。
- 一个空 workdir。
- `member-roster.json`，或 Coordinator 分配给你的 alias。
- 你的长期密钥形态：YubiKey 或外部 `.secret`。

如果你还没有长期密钥，使用第 10 章的 “Manifest Genesis” prompt 开启一个
新的 agent 会话。正常情况下，agent 会先告诉你缺什么，而不是直接生成材料。
你要确认 alias 后，再让它继续。

Genesis 输出：

- `outbox/<alias>.pub`：可以交回。
- `.secret` 或 YubiKey：自己保管，不交回，不放入 workdir。

回报格式：

```text
alias: <manifesterN>
role: manifest-set-member
phase: genesis
status: done
public_key: <outbox/<alias>.pub>
notes: <可选>
```

### 5.3 后续 ceremony：只做 manifest approval

如果你已经有长期密钥，本轮只是签名审批。

你需要准备：

- 已安装 `0xkey-keyops-manifest`。
- 一个独立 workdir。
- `member-roster.json`，其中包含你的 alias。
- `manifest-review-*.tgz` 和对应 `.sha256`。
- 你的长期密钥：YubiKey 或 workdir 外部 `.secret` 文件路径。

把第 10 章的 “Manifest 后续 Ceremony” prompt 发给 Manifest 专用 agent 会话。

agent 应该做的事：

- 初始化或检查 workspace。
- 核对你的 alias 来自 roster。
- 校验并解包 `manifest-review`。
- 用自然语言摘要本轮服务、nonce、manifest 变化。
- 在你确认后生成 `approvals bundle`。

你需要人工确认：

- alias 正确。
- review bundle 来源正确。
- agent 摘要符合本次操作说明。
- 最终只回报 `.tgz` 和 `.tgz.sha256`。

回报格式：

```text
alias: <manifesterN>
role: manifest-set-member
phase: ceremony
status: done
bundle: <outbox/<alias>-approvals-<stamp>.tgz>
sha256: <outbox/<alias>-approvals-<stamp>.tgz.sha256>
notes: <可选>
```

## 6. Share Set Member

本章只面向 Share Set Member。不要在这个会话里做 manifest approval，也不要运行
Coordinator 的操作。

### 6.1 你的职责

你负责验证 `share-request`，使用自己的长期密钥和 `.share` 生成
`wrapped-shares bundle`，再把 bundle 和 SHA256 回报。

Share 成员不做这些事：

- 不做 manifest approve。
- 不运行 `post-share`。
- 不运行 `kubectl` 或部署命令。
- 不处理别人的 `.secret` 或 `.share`。

### 6.2 Genesis：首次成为 Share 成员

Share 成员的 Genesis 分两步：

1. 初始化自己的长期密钥，并交回 public key。
2. 等 Genesis 输出包到达后，从 `genesis-output-*.tgz` 解出自己的 `.share`。

`.share` 不是自己编出来的，也不是从 YubiKey 里导出的。它来自 Genesis 输出包，
并由你的成员会话写入 workdir 外部 vault。

你需要准备：

- 已安装 `0xkey-keyops-share`。
- 一个空 workdir。
- `member-roster.json`，其中包含你的 alias 和 `member_index`。
- 你的长期密钥形态：YubiKey 或外部 `.secret`。
- 如果已收到：`genesis-output-*.tgz` 和对应 `.sha256`。

把第 10 章的 “Share Genesis” prompt 发给 Share 专用 agent 会话。

agent 正常第一轮会类似：

```text
state: uninitialized + waiting-for-roster + waiting-for-qos-client
found: 当前 workdir 存在但还没有初始化
missing:
- Coordinator 分配给你的 alias 和 member_index，必须来自 member-roster.json
- 你的长期密钥形态：YubiKey 还是外部 .secret 文件
- genesis-output bundle；如果还没发出，就等待 Coordinator
- .share 的外部 vault 保存路径
next: 先提供 member-roster.json 或确认 alias/member_index；我会再初始化 workspace
```

Genesis 输出：

- `outbox/<alias>.pub`：可以交回。
- `<alias>.share`：自己保管，不交回，不放入 workdir。
- `.secret` 或 YubiKey：自己保管，不交回，不放入 workdir。

回报格式：

```text
alias: <share-memberN>
member_index: <N>
role: share-set-member
phase: genesis
status: done
public_key: <outbox/<alias>.pub>
share_status: extracted-to-external-vault / waiting-for-genesis-output
notes: <可选>
```

### 6.3 后续 ceremony：只做 share re-encrypt

如果你已经有长期密钥和 `.share`，本轮只是代理重加密。

你需要准备：

- 已安装 `0xkey-keyops-share`。
- 一个独立 workdir。
- `member-roster.json`，其中包含你的 alias 和 `member_index`。
- 你的长期密钥：YubiKey 或 workdir 外部 `.secret` 文件路径。
- 你的 `.share`：必须是 workdir 外部 vault 文件。
- `share-request-*.tgz` 和对应 `.sha256`。

把第 10 章的 “Share 后续 Ceremony” prompt 发给 Share 专用 agent 会话。

agent 应该做的事：

- 初始化或检查 workspace。
- 核对 alias 和 `member_index` 来自 roster。
- 校验并解包 `share-request`。
- 用自然语言摘要 attestation、policy、服务、nonce。
- 在你确认后生成 `wrapped-shares bundle`。

你需要人工确认：

- alias 和 `member_index` 正确。
- `.share` 路径在 workdir 外部。
- `share-request` hash 校验通过。
- agent 摘要符合本次操作说明。
- 最终只回报 `.tgz` 和 `.tgz.sha256`。

回报格式：

```text
alias: <share-memberN>
member_index: <N>
role: share-set-member
phase: ceremony
status: done
bundle: <outbox/<alias>-wrapped-shares-<stamp>.tgz>
sha256: <outbox/<alias>-wrapped-shares-<stamp>.tgz.sha256>
notes: <可选>
```

## 7. 同时承担两个角色时怎么做

同一个人可以同时是 Manifest 成员和 Share 成员，但必须当作两个身份处理。

规则：

- 开两个独立 agent 会话。
- 使用两个不同 workdir。
- Manifest 会话只使用 `0xkey-keyops-manifest`。
- Share 会话只使用 `0xkey-keyops-share`。
- 不要把 Manifest 的 bundle、alias、输出拿到 Share 会话里混用。
- 不要把 Share 的 bundle、member_index、`.share` 拿到 Manifest 会话里混用。

如果你使用 YubiKey，仍然要分清角色；YubiKey 只承载长期私钥，Share 的 `.share`
仍然是外部 vault 文件。

## 8. 常见卡住点

- agent 找不到 `0xkey-keyops-manifest` 或 `0xkey-keyops-share`：先安装或检查
[enclave-keyops-skills](https://github.com/0xkey-io/enclave-keyops-skills)，
不要继续角色操作。
- 不知道 alias 或 `member_index`：找 Coordinator 要 `member-roster.json` 或你
的 roster 行。不要自己取名。
- agent 说缺少 `qos_client`：默认让 skill 通过 `role_init.py` 拉取
`0xkey-io/qos` 最新 stable release 并校验 SHA256。如果本轮 ceremony pin 了
版本，请提供 Coordinator 给的 release tag。
- `.secret` / `.share` 路径被拒绝：确认路径是绝对路径，并且不在 workdir
里面。不要把文件复制进 workdir。
- `manifest-review`、`share-request` 或 `genesis-output` 校验失败：停止，不要
签名，不要 re-encrypt。把错误摘要发给 Coordinator。
- 看不懂 attestation 或 policy 摘要：让 agent 用自然语言解释差异，然后把摘
要发给 Coordinator 确认。
- agent 想执行 `kubectl`、`deploy` 或 `post-share`：停止当前动作。这不是
Manifest / Share 成员职责。
- agent 混淆 Manifest 和 Share：停止当前会话，重新开启对应角色的独立会话。

## 9. 最终只交回什么

Manifest 后续 ceremony 交回 `outbox/<alias>-approvals-<stamp>.tgz` 和
对应 `.tgz.sha256`。

Share 后续 ceremony 交回 `outbox/<alias>-wrapped-shares-<stamp>.tgz` 和
对应 `.tgz.sha256`。

Genesis 初始化交回 `outbox/<alias>.pub`。

不要交回：

- `.secret`
- `.share`
- 解包后的 `incoming/` 目录
- 本地日志
- agent 截图或聊天记录

## 10. 快速复制 Prompt

下面四段是给成员直接复制给 agent 的启动 prompt。请只复制与你当前角色和阶段
匹配的一段。

### Manifest Genesis

```text
我是 0xkey 的 Manifest Set Member，协助我完成 Genesis 初始化。
```

### Manifest 后续 Ceremony

```text
我是 0xkey 的 Manifest Set Member，协助我完成本轮 manifest approval 操作。

// 提供 Genesis 阶段初始化好的 workdir 继续
```
### Share Genesis

```text
我是 0xkey 的 Share Set Member，协助我完成 Genesis 初始化。

// 后续对话 agent 会要求提供：
// Coordinator 分配给我的 alias 是：<alias 或 unknown>
// Coordinator 分配给我的 member_index 是：<N 或 unknown>
// member-roster.json 在：<路径或 unknown>

// 后续对话 agent 会询问密钥，如果已经生成可以提供路径，如果还没有可以让 agent 协助生成
```

### Share 后续 Ceremony

```text
我是 0xkey 的 Share Set Member，协助我完成本轮 share re-encrypt 操作。
请使用 0xkey-keyops-share skill，只执行 Share Set member 角色流程。

// 提供 Genesis 阶段初始化好的 workdir 继续
```
