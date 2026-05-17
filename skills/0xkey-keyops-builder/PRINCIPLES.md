# Enclave KeyOps Skill — 维护原则

本文件用于后续修改本 skill 时做自检。任何改动若违反这些原则，应先停下来重新设计。

## 1. Prod-like 验证先行

- 新增或修改 CLI 流程后，先在 prod-like 非生产环境完成一次非破坏性验证。
- 真实生产 ceremony 前，至少验证：`doctor coordinator`、bundle create/extract/verify、五服务 `manifest envelope --dry-run`、`verify --dry-run`。
- 完整演练顺序：`doctor coordinator` → prod-like 配置 `manifest envelope --dry-run` → `bundle create/extract/verify` → `verify`。涉及 `deploy apply`、`boot-standard`、`post-share` 前必须单独取得人工确认。
- 未经 prod-like 验证的命令不得直接用于生产密钥、manifest 或 K8s apply。

## 2. 不出现历史命名

- 文档、配置模板、脚本默认值中不得出现旧项目名或历史 namespace。
- 默认命名使用 `0xkey` / `0xkey-enclave`，真实环境差异必须通过外部配置显式声明。

## 3. 角色最小权限

- `doctor holder` 不应要求 `kubectl`、`aws` 或集群访问。
- `doctor coordinator` 才检查 K8s/AWS 依赖与 context allowlist。
- Share/Manifest 成员只处理本地 bundle 与自己的 `.secret` / `.share`；不要求他们接入 K8s。

## 4. 危险步骤不可自动确认

以下步骤必须保留精确短语确认，不能被全局 `--yes` 绕过：

- `approve-manifest`
- `proxy-re-encrypt-share`
- `post-share`
- `kubectl apply -k`
- `unsafe-skip-attestation`
- `unsafe-auto-confirm`

## 5. Approval 必须精确匹配

- 不允许“取目录里第一个 `.approval`”。
- 选择 approval 时必须同时匹配：`alias`、service manifest namespace、manifest nonce。
- `manifest envelope` 前必须拒绝混入其它服务或其它 nonce 的 approval。

## 6. Bundle 优先于手工目录

- 交接材料必须通过 `bundle create` / `bundle extract` / `bundle verify` 标准化。
- 每个 bundle 必须包含 `BUNDLE.json` 与 `SHA256SUMS`。
- 解包必须防止 tar path traversal，校验 checksum 后再使用。

## 7. 验证必须覆盖控制面和数据面

- 仅 Pod Ready 不足。
- 仅 `/qos/enclave-health` HTTP 200 不足。
- 必须检查 `QuorumKeyProvisioned`，再检查 `app-bridge :8081/health` 和业务路由 POST smoke。

## 8. 不内置二进制和秘密

- skill 目录不得包含 `qos_client`、镜像、`.secret`、`.share`、wrapped share、真实环境配置。
- `qos_client` 通过外部分发，并用 `qos_client_sha256_expected` 校验。

## 9. 失败要显式停止

- PCR、pivot hash、nonce、approval、bundle checksum、K8s context、数据面验证失败都必须退出非零。
- 不用静默 fallback、空 catch 或自动重试掩盖根因。

## 10. 交接信道不绑定 FS 命名

- skill 只规范 **bundle 接口**：`<name>-<stamp>.tgz` + 外层 `.tgz.sha256`，bundle 内
  含 `SHA256SUMS` 与 `BUNDLE.json`（kind / services / namespaces / nonces 元数据）。
- skill **不规定** bundle 经由什么渠道在角色之间流转。本地共享 FS、S3、IM 群聊、
  加密邮件、加密 U 盘、私有 git 仓库都是合法实现。
- 角色 workdir 内的 `inbox/` 与 `outbox/` 目录是**消费者本地落点**，不是跨成员
  的协议路径；发送方不需要知道接收方把 bundle 放在哪个子目录。
- 任何调试实现里的 `coordinator-to-members/<topic>/...` 之类的固定 FS 命名
  不属于 skill 契约，不应作为 skill 文档的默认假设。
- 如果未来需要为某种渠道引入特殊语义（例如签名邮件附带 PGP 签名），通过新增
  bundle 元数据字段实现，而不是通过约定目录名。

## 11. (alias, member-index) 由 Coordinator 单方面分配并永久绑定

- 成员**不能**自取 alias 或 member-index；这两个字段由 Coordinator 在
  `shared/member-roster.json` 中签发，发布在任何 `.pub` 被收集**之前**。
- 脚本将 roster 视为强不变量：`doctor coordinator` / `manifest generate` /
  `ceremony genesis-boot` 都会调 `check_member_roster`，校验文件名 stem 与
  alias byte-equal、share-set member_index 是 1..N 连续整数、不存在多余
  `.pub`。
- `ceremony genesis-boot` 之后 (alias, member_index, .pub, .share) 永久绑定
  到生成的 quorum_key；冲突或就地修改只能通过重做 Genesis 修复，不能事后
  打补丁。
- 替换成员走 `key-forward`（同 index, 新 key）或追加新 index（新建一份
  ceremony field 不同的 roster），绝不修改历史 roster 条目。
- `review` / `share-request` / `genesis-output` bundle 必须随附 roster
  slice（`BUNDLE.json.members`）与 `member-roster.json` 副本，让成员可在
  接收端独立核对自己的 (alias, member_index)。

