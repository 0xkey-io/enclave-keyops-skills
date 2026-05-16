# Enclave KeyOps — 安全与审计

本文件定义 **prod-ready** 下 agent 与操作人员必须遵守的红线。违背任一条应中止 ceremony 并升级人工复核。

## 1. 密钥材料分级

| 材料 | 敏感度 | 可否进 git / chat / CI 日志 |
|------|--------|---------------------------|
| `.secret`、`master-seed` | 最高 | 永不上传；禁止粘贴到 IM |
| `.share`、eph-wrapped share | 最高 | 同上 |
| `dr.secret` / `dr-master-seed` (DR 私钥) | 最高 | 永不上传；与 `.secret` 同等约束；必须保存在 skill 之外的 vault（YubiKey / HSM / 加密磁盘镜像），永不进入任何 role workdir |
| `*.approval` | 高 | 可加密归档；禁止公开仓库 |
| `*-manifest.json` / envelope | 中高 | 可入受控存储；发布前需多人审阅 |
| `quorum_key.pub`、`*.pub` | 低 | 可分发给审阅者 |
| `dr-key.pub` (DR 公钥) | 低 | 可分发；Genesis 必需输入；与 `quorum_key.pub` 同级敏感度 |
| `nitro.pcrs`、`pivot-hash.txt` | 低 | 构建产物，需与镜像一致 |

> **DR (Disaster-Recovery) 密钥说明**：DR 密钥是 quorum 灾难恢复的最后兜
> 底，由独立的"DR 持有者"在外部 vault 生成，并把 `dr-key.pub` 交给
> Coordinator 作为 `ceremony genesis-boot` 的必需输入。DR 私钥泄漏 = quorum
> 安全失效；本 skill 不为 DR 持有者建模 role workspace，也不提供任何读取 /
> 复制 / 传输 DR 私钥的命令。

## 1.1 一把 `.secret`/`.share` 只能由 roster 上对应的那一个成员本人持有

这是 quorum 安全模型的硬红线，独立于"不上传到 IM/git"那一条；后者是传输信
道纪律，本条是 **持有权** 纪律。

- `member-roster.json` 每一行 `(alias, member_index?)` 对应**恰好一个**人类
  操作员。该 alias 的 `.secret`、`.share` 只能由这个人在自己的外部 vault
  里持有并使用。
- **不允许跨成员借用**，无论场景看起来多合理：
  - "对方出差，我帮 ta 签一下" — 拒绝。
  - "我们两个共用一台机器，secret 我顺手拿来用" — 拒绝。
  - "对方把 secret 路径发给我了，让我替 ta 跑 `manifest approve` / `ceremony reencrypt`" — 拒绝。
  - "Coordinator / Builder 我都做，我把成员 secret 也接过来一起处理" — 拒绝；这甚至破坏角色分离（见 §4 / PRINCIPLES §3）。
- 一旦 `.secret` 或 `.share` 离开过该 alias 持有者的外部 vault（包括短暂
  复制、临时挂载、通过任何渠道发给第二个人），视同 **key compromise**：
  - 立即作废该 alias 对应的 `.secret` / `.share`；
  - 对 Manifest Set 成员：通过 `key-forward` 给该槽位重发新 key（alias 保
    持），并重做受影响 service 的 approvals；
  - 对 Share Set 成员：由 Coordinator 走 `key-forward` 给同一 `member_index`
    重发新 key（alias 保持），或追加新 `(member_index = N+1, alias)` 后
    重做 Genesis（见 §10 与 coordinator.md "After Genesis: replacing or
    adding members"）；
  - 在审计日志里记录 compromise 事件、时间、影响范围；不可静默忽略。
- agent 层强制：
  - 任何 role skill 在收到"借用他人 secret/share"请求时，必须**拒绝**并
    把请求者重定向到该 alias 持有者本人的 role skill session。
  - `manifest approve` / `ceremony reencrypt` / `ceremony share-extract`
    / `key file-generate` 的 **持有人凭据** 必须是调用者本人 alias 的凭
    据，无论凭据形态是哪一种：
    - 文件路径模式 (`--secret-path`)：必须指向调用者本人在外部 vault 中
      的 `<alias>.secret`；
    - YubiKey 模式 (`--yubikey`)：必须是调用者本人插在本机的 YubiKey，
      PIV PIN 由本人输入，**不得** 转交他人的 YubiKey 来"代签"；
    - `--share-path` 始终指向调用者本人 alias 的 `.share` 文件（即使
      `--yubikey` 也一样，因为 share 不存在 YubiKey 上）。
  - 脚本不校验 alias↔凭据绑定（无从核验），但 agent 层不得替别人调用。
- 这一条**没有**例外开关：`--unsafe-auto-confirm` / `--yes` 不能用来跳过
  "持有人 = roster 指定的那一个人" 的口头确认。

为什么这条要单独立条：跨成员借用是真实运维里最常见的便利型违规，且容易
被"不上传/不粘贴"那条规则蒙混过去——粘贴绝对路径不算"上传"，但替别人
跑命令本身就已经把"该 alias 的签名能力"暴露给了第二个人。Genesis 之后
`(alias, member_index, .pub, .share)` 永久绑定到 `quorum_key`
（PRINCIPLES §11），事后审计无法把这一把签名追回到正确的人，只能整组重
做 Genesis。

## 2. agent 使用约束

- **永不**在对话中请求或展示 `.secret` / `.share` / wrapped share 的十六进制或文件全文。
- **脚本审计日志**（`--audit-log`）只记录：阶段名、命令 argv（脱敏后）、退出码、关键产物文件的 **basename** 与 **SHA256**。
- 默认脱敏：CLI 中**以 `-` 开头**且名字包含 `secret` / `share` / `.pem` /
  `password` / `token` / `seed` 子串的 flag（例如 `--secret-path`、
  `--share-path`、`--master-seed-path`、`--token`），其紧邻的值或
  `--name=value` 形态里的 value 段会被替换为 `[REDACTED]`。位置参数
  （子命令名、binary 路径、含敏感子串的目录名如 `shared/qos_client`、
  公开 `.pub` 路径里碰巧含 `share` 的 alias 名）一律**不脱敏**——它们
  本身就是公开操作元信息，脱敏会反过来掩盖审计线索。

## 3. `qos_client` 版本门禁

- `qos_client` 分两类使用：
  - **release/reference client**：Builder 产出的可验证构建产物，通常是 `linux/amd64`，用于记录 release SHA、容器内 pivot-hash / release 校验等。
  - **operator client**：成员/协调员本机实际执行签名、display、重加密等操作的工具。它必须来自同一 qOS source revision / signed release bundle，但应匹配操作者机器平台（例如 macOS arm64）。
- 成员在 Mac arm 上不应直接执行 `linux/amd64` `qos_client`。优先提供同 revision 的 native `darwin/arm64` operator client；仅在 staging 或受控环境中使用 `docker run --platform linux/amd64` wrapper，并且容器需固定 image digest、只挂载必要目录、尽量禁用网络。
- 配置里必须填写本角色实际执行的 operator client SHA256；同时在 handoff 中记录它对应的 qOS revision / release digest。
- `doctor` 失败则禁止继续 manifest / ceremony。

### 3.1 自动 fetch 红线

`scripts/fetch_qos_client.py` 与 `role_init.py --qos-client-release-tag` 提供
GitHub Releases 自动下载，**但不会绕过 SHA256 校验**。任何"网络抖动重试更
快"或"先跳过 sha 校验等下次再补"的便利路径都视同破坏门禁：

- 下载来的 `.sha256` 与本地实算 hash 不一致 → 二进制必须**隔离**（脚本写到
  `<out>.tainted`），绝不安装到 `qos_client_path`，并 `exit 2`。
- 若 builder-handoff 里另带了独立的 expected SHA256，必须再做第二次比对（
  `--expected-sha256`）；任一不通过都判失败。
- 任何形态的"先用着、后面补 sha"流程**不允许**；这一条没有 `--unsafe-...`
  开关。
- 自动 fetch 仅运行在 init / 升级 setup 阶段；`doctor` 永远只读，发现 binary
  缺失也只**打印**可粘贴的 fetch 命令而**不**自执行。

### qos_client 更换触发表

| 触发 | 谁负责 | 动作 |
|------|--------|------|
| qOS revision 升级（PCR / manifest 字段语义变） | Builder | 重新发布全平台 operator client + sha256；通知所有 operator |
| 上游 qOS / qos_client CVE 或关键 bug fix | Builder + 安全联络人 | 同上，并在 ceremony 暂停期间强制升级 |
| 新成员 / 跨平台启动（如新增 linux/arm64 用户） | Builder + Coordinator | Builder 补该平台 binary；Coordinator 转发 |
| Genesis 与后续 ceremony qOS revision 不同 | Coordinator | 以正在跑的 ceremony manifest 对应版本为准 |
| 二进制损坏 / 本机 sha256 不匹配 | 个人 + Coordinator | 重新拉同版本，不要绕过 sha 校验 |

> **同一 ceremony 内不更换**：`boot-genesis` → `approve-manifest` → `boot-standard`
> → `proxy-re-encrypt-share` → `post-share` → `verify` 全程使用同一份 binary
> 与同一份 sha256；中途升级会导致 PCR / manifest 语义错位，直接报废本轮 ceremony。

## 9. Threshold 推荐

`quorum_threshold` 文件是单行明文整数，与 `*.pub` 文件位于同一目录
（`shared/<set>/`）。下表为 0xkey 项目的默认推荐，最终值由 Coordinator 与
Quorum 集合所有者共同确认：

| 集合 | staging | prod |
|------|---------|------|
| Manifest Set | 2/3 | 3/5 |
| Share Set | 2/3 | 4/10 |
| Patch Set | 可禁用（写 README 说明） | 与 Manifest Set 同档或显式禁用 |

挑选原则：
- **threshold ≤ ⌈ N/2 ⌉ + 1**：避免极端情况下少数成员就能单边批准。
- **threshold ≥ 2**：永远不允许单签解锁（除非 staging 单方 dev 演练）。
- 调高 threshold 之前先确保所有 active 成员都能稳定到场；否则 ceremony 会
  因为「一个成员不可达就卡住」而被迫降低标准重做。
- 集合人数变化（成员离职 / 新增）必须重新签发 manifest 与 share-set；threshold
  不能在不重做 Genesis / re-share 的情况下静默改变。

## 4. 人工确认门禁

以下步骤 **禁止** 无人值守全自动：

- `approve-manifest`
- `proxy-re-encrypt-share`
- `post-share`
- `kubectl apply -k`（除非你司另有批准流程且 CI 已门禁）
- `unsafe-skip-attestation`（仅抢险；事后必须恢复合规流程）

脚本里全局 `--yes` **不得**用于跳过上述门禁；危险步骤必须输入精确确认短语，例如 `approve-manifest`、`kubectl-apply`、`reencrypt-share`、`post-share`。`--yes` 仅用于如 `doctor`、`deploy render` 等非秘密步骤。

## 5. 工作目录与 key vault

- Ceremony 工作目录应在 **repo 外** 或至少不在 `.cursor/`、`repos/` 提交路径下。
- 判断原则：**workdir 可以随时删除重建；key vault 必须长期可靠保存。Public material 可以复制进 workdir；private material 只能通过绝对路径引用或硬件 PIV 调用，永不复制其内容到 workdir。**
- Public material 包括成员 `.pub`、`quorum_key.pub`、PCR/pivot hashes、review/share-request bundles，以及 Coordinator 操作所需的公开输入。丢失后应能从成员私钥、硬件 key 或构建产物重新生成/分发。
- 成员长期 key（`master-seed` / `.secret`）与 Genesis 下发的 `.share` **不得放在 role workdir 内**；脚本会主动拒绝 workdir 内部的敏感路径。

### 5.1 Vault 形态推荐分层

按运行环境选择 long-term secret 的承载形态。**prod 路径默认 YubiKey**，
仅在硬件未就绪时短期回落到加密磁盘文件；dev 路径允许明文文件以便快速演
练，但绝不能用 dev workspace 的 `.secret` 去签 prod manifest。

| 环境   | 推荐 vault                                       | 调用方式                                | 备份策略                                       | 何时可降级 |
|--------|--------------------------------------------------|-----------------------------------------|------------------------------------------------|-----------|
| prod   | YubiKey 5 系列 / 同档 HSM（PIV slot，硬件不可导出） | `--yubikey`（passthrough 到 qos_client） | 至少 provision 2 把 YubiKey；丢失走 `key-forward` 重发 | 不允许降级；硬件未就绪应推迟 ceremony |
| staging| 加密磁盘镜像 / 加密 USB 上的 `.secret` 文件        | `--secret-path /Volumes/<vault>/<alias>.secret` | 一份本地加密备份 + 一份脱机加密备份               | 允许 dev 形态短期演练，但禁止 dev 文件回流到 staging |
| dev    | 本机加密目录中的 `.secret` 文件                   | `--secret-path ~/0xkey/operator-keys/<alias>/<alias>.secret` | 单份本地备份足够，演练完即销毁                     | 任何 ceremony 改正式用途前必须重新生成 prod key |

DR 私钥（`dr.secret`）与 Coordinator 的"备份份额持有人"位置同理：prod
环境**必须**走 YubiKey 或独立 HSM；staging/dev 可以走加密磁盘镜像，但绝
不与该角色其它 key 同槽位、同备份介质。

### 5.2 YubiKey 路径下的操作约束

- 生成：`key yubikey-provision`（不是 `key file-generate`）在 YubiKey 的
  PIV slot 中直接生成 long-term key；私钥不可导出，`.pub` 输出到本角色
  `outbox/<alias>.pub`，由 Coordinator 收集进 roster。
- 调用：`manifest approve` / `ceremony reencrypt` / `ceremony share-extract`
  接受 `--yubikey` 与 `--secret-path` **二选一**，同时给会被脚本拒绝；
  脚本只透传给 `qos_client`，PIV PIN/PUK 由 qos_client 直接向操作员索取，
  **永不**经过本 skill 的 prompt、stdout 或 audit log。
- Share 文件：`--share-path` 始终是外部 vault 中的 `.share` 文件路径；
  YubiKey 不存 share，share 由 Coordinator 在 Genesis 之后下发到该成员
  的外部 vault（见 §1.1 表）。
- 上游依赖：`approve-manifest` / `proxy-re-encrypt-share` /
  `after-genesis` / `provision-yubikey` 这四条 qos_client 子命令全部接
  受 `--yubikey` flag——在 2026-05-16 用 prod release qos_client
  `sha256:84fce156f2a54a3aeb446c2600fd48e179ee3267a5d570e356f084aaac3082f4`
  做过端到端实测确认。后续 Builder release 应保持这条约束；新版本若
  回退（去掉 `--yubikey` 支持）会被 `doctor holder` 与各成员 first-turn
  的 vault-mode 询问立刻暴露，需要回到 file 形态做 ceremony 并由
  Builder 修复。

### 5.2.1 YubiKey 首次准备清单（必读）

`provision-yubikey` 内部硬编码使用 PIV TDES default Management Key，
假设 YubiKey 是"出厂干净 PIV"状态。**任何不满足都会让 provision
失败、且失败后 slot 9C/9D 可能处于"有 key 没 cert"的半完成状态，
必须 reset 整盘 PIV 才能 retry**。首次用 YubiKey 跑 0xkey ceremony 前，
按下面顺序逐项验证：

1. **检查 PIV 当前状态**：

   ```bash
   ykman piv info
   ```

   重点看 4 行：

   - `Management key algorithm:` 必须是 `TDES`。**YubiKey 5.7+
     firmware 出厂 default 是 AES192**，与 qos_client 不兼容，
     会报 `GenerateSign(FailedToAuthWithMGM)`。
   - `PIN tries remaining:` 应为 `3/3`；若 `< 3` 说明之前输错过，
     不一定阻塞但要注意（错 3 次会锁 PIN）。
   - `WARNING: Using default PIN!` / `Using default Management key!`
     —— 期望都在；若不在，说明 PIN/MGM 被改过，需要 reset。
   - PIV slot 列表（9A/9C/9D/9E/82-95）应**为空**。若已有 cert
     （例如 `CN=codex-test-yubikey` 这种他处工具留的残骸），决定
     是否可清。

2. **若不满足，整盘重置 PIV applet**（**只重置 PIV，不影响 OTP /
   FIDO2 / OpenPGP / U2F 等其它 applet——也就是 GitHub passkey、
   Google 2FA、FIDO2 SSH key 全部不受影响**）：

   ```bash
   ykman piv reset --force
   ```

   ⚠️ 不可逆地销毁 9A/9C/9D/9E/82-95 slot 上现有 X.509 cert 与
   private key。先确认这些 slot 上没有你别处在用的凭据
   （macOS smart-card login / PIV SSH cert / 邮件签名）。

3. **重置后 firmware 仍把 default MGM 标成 AES192**，要手动切回 TDES：

   ```bash
   ykman piv access change-management-key \
     --algorithm TDES \
     --management-key 010203040506070801020304050607080102030405060708 \
     --new-management-key 010203040506070801020304050607080102030405060708 \
     --force
   ```

   再跑 `ykman piv info` 验证 `Management key algorithm: TDES`。

4. **跑 `key yubikey-provision`**。qos_client 会顺序处理 slot 9C
   (SIGNATURE) 与 slot 9D (KEY_MANAGEMENT)：

   - 提示 `Enter your pin:` → 输入 PIN（出厂 default `123456`）。
   - YubiKey 指示灯开始闪烁 → **立刻用手指轻触金属触点**。一次
     触摸完成 slot 9C 的 self-signed cert 生成。
   - 灯再次闪烁 → **再触摸一次**，完成 slot 9D。
   - 两次触摸窗口各约 15-20 秒。**没及时触摸**会报
     `GenerateSign(FailedToGenerateSelfSignedCert)`，且 slot 9C
     上会留下"有 key 没 cert"的孤儿，必须回到第 2 步整盘 reset
     再 retry。

5. **provision 成功后验收**（一次性确认 PIV 状态正确，写进 handoff）：

   ```bash
   ykman piv info
   ykman piv keys info 9c
   ykman piv keys info 9d
   ```

   期望两个 slot 都是 `Origin: GENERATED` + `PIN required for use:
   ALWAYS` + `Touch required for use: ALWAYS`、`Subject DN:
   CN=QuorumOS`、cert 有效期约 10 年。

6. **prod 路径推荐 provision 至少 2 把 YubiKey 给同一 alias**
   （`shared/<set>/<alias>.pub` 仍然是同一份），任一把丢失/损坏
   时另一把可继续签名，避免单点失效逼着 ceremony 走 `key-forward`
   重发新 alias。

> ⚠️ qos_client `provision-yubikey` **不接受** `--mgm-key` 或 custom
> PIN 参数；这是 qos_client 的设计选择（"provision = 把一把干净 PIV
> 卡绑定到 0xkey"），不是本 skill 的限制。若你的 YubiKey 必须保留
> 某些 PIV 现有凭据，唯一办法是另买一把 YubiKey 专给 0xkey 用。

### 5.3 文件形态的强制约束

- Agent/LLM 只能接收 **绝对路径**，不能接收 key 内容。不要把 `.secret` /
  `.share` 粘贴到聊天、IM、文档或 ticket。
- `key file-generate`：`--master-seed-path` 必须是 workdir 外的绝对路径；
  `--pub-path` 可以写到本角色 `outbox/` 用于交给 Coordinator。
- `manifest approve` / `ceremony reencrypt`：`--secret-path` / `--share-path`
  必须是 role workdir 外的绝对路径；脚本会拒绝 role workdir 内的敏感路径。
- key 生成后立即设置权限并备份：`chmod 700 <alias-dir>`、`chmod 600 *.secret *.share`；
  至少一份离线加密备份，备份位置和恢复口令由人保管，不交给 agent。

## 6. post-share 顺序与故障

- 已知部分环境存在成员顺序敏感问题；协调员应在 runbook 中记录 **post-share 顺序**，并在失败时按灾难恢复流程重做该服务 ceremony（见 [WORKFLOWS.md](WORKFLOWS.md) §恢复）。

## 7. 数据面 vs 控制面

- **仅** `kubectl get pods` / `QuorumKeyProvisioned` **不足**；必须再验 `:8081/health` 与业务路由 smoke，避免「状态机虚标完成」类故障。
- 验证不得假设 `qos-host` / `app-bridge` 容器内有 `/bin/sh`、`curl` 或其它调试工具。优先用 `kubectl port-forward`、jumpbox，或本机/受控环境的 HTTP 客户端从容器外验证：
  - control plane: `/qos/enclave-health` 包含 `QuorumKeyProvisioned`
  - data plane: `:8081/health` 返回 2xx
  - business smoke: `POST :8081/v1/<svc>` 返回 `<500`，最好为 200

## 8. 交接包完整性

- Review bundle、Share request bundle、成员 approvals bundle、wrapped shares bundle 都必须带 `SHA256SUMS`。
- 收包方先执行 `bundle verify --bundle-dir <dir>`，再进行签名、重加密或 post-share。
- 打包/解包优先使用 `bundle create --kind ... --archive ...` 与 `bundle extract --archive ...`；不要手工拼目录，避免漏文件或混入其它轮次产物。
- `post-share` 与 `proxy-re-encrypt-share` 必须通过 `--approval-alias` 或 `config.approval_alias` 显式选择 approval；脚本会同时匹配 service namespace 与 nonce，禁止"取目录里第一个 approval"。

## 10. Member roster（alias / member-index 唯一性）

- **alias 与 member-index 由 Coordinator 单方面分配**，写在 `shared/member-roster.json`，成员只能确认不能自取。详细分配协议见 [coordinator.md `Alias / member-index assignment`](references/roles/coordinator.md#alias--member-index-assignment)。
- 冲突后果：alias 撞名 → `<alias>.pub` 互相覆盖、approval 文件匹配错位；member-index 撞号 → wrapped-share 文件名冲突、`post_share_members_order` 出现重复。`ceremony genesis-boot` 之后 (alias, index, .pub, .share) 永久绑定，必须重做 Genesis 才能改。
- 脚本硬门：`doctor coordinator` / `manifest generate` / `ceremony genesis-boot` 都会调用 `check_member_roster`，校验 (a) JSON 合法、(b) alias 文件名安全且 set 内唯一、(c) share-set member_index 是 1..N 连续整数、(d) `shared/<set>/*.pub` 文件名 stem 与 roster alias 一一对应（不允许多余 `.pub`）。
- 分发：`review` / `share-request` / `genesis-output` bundle 在 `BUNDLE.json.members` 字段中携带相关 set 的 roster slice，并把整份 `member-roster.json` 拷进 bundle 根，便于成员独立核对自己的 (alias, member_index)。
- 成员替换：换人不能改 alias 占同一 index；要么走 `key-forward` 给该 index 重发新 key（alias 保持），要么追加新 index、新建一份 ceremony field 不同的 roster。**绝不就地修改历史 roster 条目。**
