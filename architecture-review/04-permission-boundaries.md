# 04 · 权限边界

> 先给结论：**DevFlow 没有操作系统级沙箱**。所谓"权限"由三部分组成：
> ① CLI 是否允许某命令执行（`MUTATING_COMMANDS` / `ACCEPTED_ALLOWED` / 前置校验）；
> ② CLI 是否接受某份数据（Schema + 语义校验）；
> ③ 事后能否发现越界（Git 实际变更 + `files_scope` + 符号链接检查）。
> 执行 Agent 在文件系统层面**能**写任何文件；系统靠 ②③ 兜底，而不是靠"写不进去"。

## 一、角色定义

| 角色 | 实际身份 | 能做什么 | 不能做什么（约定） |
| --- | --- | --- | --- |
| 主控 Agent | 调用 `df.py` 的 AI | 执行所有 CLI 命令、写需求/计划/CR 文档、验收节点 | 不应手改 `backlog-vN.json`（但技术上可行） |
| 执行 Agent | 被投喂 `prompt.md` 的 AI | 改 `files_scope` 内业务代码、写本节点两份报告、跑测试 | 不应改全局状态、其他节点、需求基准 |
| CLI / 状态机 | `scripts/df.py` | 读写全部 `.devflow` 状态、加锁、事务、校验 | 不执行业务代码、不生成测试证据 |
| 文件系统 / Git | OS + git | 提供原子写、文件锁、变更检测 | 不做业务判断 |
| 用户 | 人 | 确认需求、批准计划、验收交付、决策 CR | — |

## 二、角色 × 数据 × 操作矩阵

图例：**✓** 允许且被强制；**△** 允许但只靠约定/事后校验；**✗** 应禁止（含"CLI 拒绝"或"约定禁止"）。

| 数据 | 主控 | 执行 Agent | CLI | 用户 | 强制手段 |
| --- | --- | --- | --- | --- | --- |
| `project.json` | ✓ 只能通过 `init/project-set/migrate/change-apply/release-*` | ✗ | ✓ 独占 | ✗（只读） | `PROJECT_PROTECTED_FIELDS`（`df.py:98`）+ 命令前置校验 |
| `requirements/traceability*.json` | ✓ `req-import`（**目标：仅首次确认前**；之后只能暂存） | ✗ | ✓ | ✗ | Schema + `acceptance_spec_problems`（`dfcore.py:1898`）；目标加状态门槛 |
| `confirmed-requirements-vN.md` | △ 直接写文件 | ✗ | ✗（只读校验） | △ 口述 | **无强制**：`req-confirm` 只查存在/长度/关键词（`df.py:435-448`） |
| `plans/backlog-vN.json` | ✗ 应只用 CLI | ✗ | ✓ 独占 | ✗ | 无文件级防篡改；依赖"只有 CLI 写"的约定 |
| `plans/board-vN.md` / `dependency-graph-vN.md` | △ 可重渲染 | ✗ | ✓ 渲染 | ✗ | 派生文件；手改会被下次 `refresh` 覆盖 |
| `nodes/<ID>/prompt.md` | ✓ `prompt-init` | ✗（只读） | ✓ | ✗ | 版本头 5 项绑定（`dfcore.py:1258`） |
| `nodes/<ID>/execution-report.md` | △ 可代写 | **△ 直接写** | ✗（只读） | ✗ | 事后校验：`evidence_problems`（`dfcore.py:2086`） |
| `nodes/<ID>/test-report.json` | △ 可代写 | **△ 直接写** | ✗ | ✗ | 同上（统计自洽、`attempt_id` 绑定） |
| `changes/CR-xxx/change-request.json` | ✓ `change-new/set` | ✗ | ✓ | ✗ | Schema + `change-apply` 基准/绑定校验 |
| `changes/CR-xxx/*.md` | △ 直接写 | ✗ | ✗ | ✗ | 无（人读） |
| `regression/final-report.json` | ✗ | ✗ | ✓ `regression-import` | ✗ | Schema + 必需项 + 快照 + 版本绑定 |
| `releases/release-report.{md,json}` | ✗ | ✗ | ✓ `release` | ✗ | `release-ready` 校验章节/版本/占位符 |
| `.devflow/.lock` / `.txn/*` | ✗ | ✗ | ✓ 独占 | ✗ | `ProjectLock` / `Transaction` |
| 业务代码 | ✗（除紧急修复） | ✓ 仅 `files_scope` | ✗ | ✓ | Git 实际变更 ∪ 报告 → `scope_violations`（`dfcore.py:710`） |
| `.gitignore` / 构建产物 | △ | △ | ✗ | ✓ | **无强制**（ignored 文件不在 Git 变更里） |

> 注：`req-import` 的调用权限**不受项目状态约束**（除 accepted），因此"主控可改需求追踪表"实际是
> "在 release_ready 也能改"。见 06 A6。

### 关键缺口（按严重度）

1. **需求基准文档没有写保护**（`confirmed-requirements-vN.md`）：任何人可改内容不改版本，回归/交付都不会失效。
2. **计划文件没有防篡改**：`backlog-vN.json` 手改后 `validate` 不会发现（没有内容哈希、没有签名）。
3. **ignored 文件无法归属**：`git status --porcelain` 不列 ignored 文件（`dfcore.py:952-967`），
   所以"改了一个被 ignore 的配置文件"既不会被报告，也不会被 CLI 发现。
4. **执行证据是"自证"**：`test-report.json` 由 Agent 写，CLI 只能检查自洽性（统计、退出码、`attempt_id`），
   无法证明命令真的跑过。

## 三、命令级权限（谁可以调用什么）

| 命令组 | 命令 | 谁调用 | 前置校验 |
| --- | --- | --- | --- |
| 初始化 | `init` | 用户/主控 | 目录可写；已存在需 `--force` |
| 档案 | `project-set` | 主控 | 字段白名单 + 档位受控（`df.py:330`） |
| 需求 | `req-import` / `req-confirm` | 主控 | 当前：Schema + 验收标准 + 追踪表复查；**目标：`req-import` 只在首次确认前写正式，之后只能写 CR staging**；`req-confirm` 目标仅限计划未激活 |
| 计划 | `plan-import` / `plan-activate` | 主控 | `plan-import` 只产草稿；`plan-activate` 当前无 CR/状态前置（B2/B3），**目标仅允许 v1**，v2+ 只能 `change-apply` |
| 提示词 | `prompt-init` | 主控 | 计划版本；`--force` 归档 |
| 执行 | `start` | 主控（可代执行 Agent 触发） | 计划 active、依赖完成、提示词版本、上下文变更 |
| 同步/验收 | `sync` / `accept` | 主控 | 证据 + 实时 Git + 范围（`df.py:1220`、`1341`） |
| 异常 | `block` / `fail` / `retry` / `cancel` / `supersede` | 主控 | 状态机 + 尝试次数 |
| 变更 | `change-new` / `change-set` / `change-stage`（待实现）/ `change-apply` | 主控（`change-apply` 前需用户确认 CR） | CR 状态 + 基准 + `based_on` + 迁移 + 事务；**`change-apply` 是计划/需求正式切换的唯一入口** |
| 回归 | `regression-check` / `regression-import` | 主控 | 无 open 节点 + 报告自洽 + 快照 |
| 交付 | `release` / `release-ready` / `release-accept` | 主控 / 用户 | 门槛 + 报告完整 + 快照 + 状态 |
| 维护 | `recover` / `migrate` / `doctor` / `validate` | 主控/用户 | 事务存在 / schema 版本 / 只读 |

**accepted 白名单**（`df.py:78`）：`status/next/task/validate/doctor/self-test/board/graph/
change-new/change-set/plan-import/prompt-init/change-apply/recover`。其余写命令一律退出码 2。

## 四、文件系统与 Git 的边界

### 4.1 路径安全

| 攻击面 | 实现 | 证据 |
| --- | --- | --- |
| 绝对路径 / `..` | `normalize_rel` 拒绝（`dfcore.py:618`） | `_ABS_PATH_RE`、`..` 检查 |
| 目录前缀逃逸（`src` vs `src-evil`） | 要求 `dir/` 前缀；`scope_match`（`dfcore.py:676`） | `ScopeTest.test_directory_prefix_cannot_escape` |
| 符号链接逃逸 | `realpath` + `commonpath` 祖先判断（`dfcore.py:686`） | `ScopeTest.test_symlink_prefix_sibling_escape` |
| 通配符绕过 | `_glob_to_regex` + 否定前瞻（`dfcore.py:640`） | `ScopeTest.test_double_star_glob` |
| 不存在的路径 | 检查最近的已存在祖先（`dfcore.py:692-700`） | 同上 |

### 4.2 真实变更识别

- 基准：`git_snapshot`（HEAD + dirty 列表，`dfcore.py:952`）。
- 差集：`git_changes_since`（`dfcore.py:970`）：`当前 dirty ∪ (HEAD..工作区 diff)`，再减去 baseline dirty。
- 归属：**不区分 Agent**；并行节点共享工作区时，A 的改动会出现在 B 的"实际变更"里。
- 盲区：
  1. baseline 就 dirty 的文件，执行期间再改 → 被整份剪掉（`dfcore.py:993`）；
  2. ignored 文件 → 不可见；
  3. 非 Git 仓库 / 项目不是仓库根 → 返回 `None`（production 下阻断验收）。
- 证据：`GitScopeTest`、`AcceptLiveGitTest`、`SourceSnapshotTest`。

### 4.3 失败恢复

| 场景 | 机制 | 证据 |
| --- | --- | --- |
| 多文件提交（仅 change-apply） | 快照 + journal + 回滚 | `dfcore.py:781`、`df.py:1706` |
| 进程崩溃 | `.txn/*` 标记 open → `recover` 回滚 | `df.py:2641` |
| 其他多文件写（req-import / release / regression-import） | **无 journal** | 见 `02-data-model.md` G2/G4/G5 |
| 锁 | OS 文件锁 + 每次 `ProjectLock` 不删锁文件 | `dfcore.py:200-355` |

## 五、威胁模型（Threat Model）

### 5.1 资产与威胁主体

| 资产 | 被破坏的后果 |
| --- | --- |
| `project.json` 生命周期状态 | 未完成的交付被当成已验收 |
| `backlog-vN.json` 计划与节点状态 | 越界改动被标成"已完成" |
| 节点报告（执行/测试） | 伪造验收证据 |
| 需求追踪表与基准文档 | 缩小交付范围、绕过 CR |
| 回归报告与源码快照 | 用过期结论交付 |
| 交付报告 | 未验证功能被写成"已验证" |
| 业务代码本身 | 交付物错误 |

| 威胁主体 | 能力 | 动机 |
| --- | --- | --- |
| 失控/恶意的执行 Agent | 对仓库有完整读写权限（无沙箱） | 让验收通过、少干活、掩盖失败 |
| 误操作的主控 Agent | 可调用所有 CLI | 走捷径、跳步骤 |
| 并发写入 | 同时改同一项目 | 状态覆盖 |
| 外部环境（CI/网络） | 伪造构建结果 | 骗过"测试通过" |

### 5.2 攻击面与控制层次

| 攻击面 | Agent 行为约束（Prompt） | CLI 命令权限 | 文件系统权限 | Git 检测 | 当前是否拦住 |
| --- | --- | --- | --- | --- | --- |
| 直接改 `backlog-vN.json` 状态 | 禁止 | **无校验**（读时信任） | 无 | `.devflow` 被排除在 Git 变更外 | ❌ 拦不住 |
| 直接改 `project.json` | 禁止 | 无校验 | 无 | 同上 | ❌ 拦不住 |
| 改其他节点报告 | 禁止 | 只校验 `node_id` 字段与目录约定 | 无 | 同上 | ❌ 拦不住（可覆盖已完成节点的证据） |
| 改 `confirmed-requirements-vN.md` | 禁止 | 只在 `req-confirm` 查长度/关键词 | 无 | **Git 本身能看到**（若 `.devflow` 已提交且未被 ignore）；**DevFlow 的 `git_changes_since` 显式过滤 `.devflow/`**（`dfcore.py:996-1000`），所以流程不消费 | ❌ 拦不住 |
| 改 `traceability.json` 把需求标 retired | 禁止 | `req-import` 无状态门槛 | 无 | 同上 | ❌ 拦不住（且交付门禁跳过） |
| 越界改业务代码 | 禁止 | `files_scope` + Git 实际变更 | 无 | **检测**而非阻止 | △ 事后可发现（有盲区） |
| 伪造 `test-report.json` | 禁止 | 只校验自洽（统计/退出码/`attempt_id`） | 无 | 报告在 `.devflow` 内，不可见 | ❌ 无法证明 |
| 改代码后重跑旧报告 | 禁止 | 源码快照比对 | 无 | ✅ `snapshot_id` | ✅ 能发现（除非同时改快照） |

### 5.3 四层控制的真实边界

1. **Prompt 约束不是安全边界**：SKILL.md 里所有"不要手改 JSON"都只是对合作型 Agent 的提示。
2. **CLI 命令权限防的是"误用"，不防"绕过"**：它能拒绝 `start`/`accept` 等命令，但阻止不了直接写文件。
3. **文件系统无隔离**：执行 Agent 有整个仓库写权限，因此可以改 `.devflow`、改别的节点报告、改需求文档。
4. **Git 检测是"检测"**：`git_changes_since` 在 `.devflow` 之外生效；`.devflow` 内的篡改不在其视野里。

### 5.4 内容指纹能做什么、不能做什么

- **能做**：发现"只改了被保护文件、没同步改摘要"的篡改（例如改了 `backlog` 却不知道 `project.plan_digest`）。
- **不能做**：抵御同时控制文件与摘要的攻击者 —— 摘要存在同一个可写目录时，攻击者可以一起改。
  因此指纹只能防"误操作/局部篡改"，不能作为对抗恶意 Agent 的安全机制。
- **真正的隔离只能来自进程/文件系统边界**：
  - 执行 Agent 在**独立 worktree** 里工作，`.devflow` 对它只读；
  - 报告由**CLI 代写**（Agent 把结果写到受限的 outbox，CLI 校验后搬入）；
  - 交付证据来自**外部 CI**（绑定 commit SHA + 需求 digest），而不是 Agent 的自述。

### 5.5 身份与审批的信任边界（`--by` / `confirmed_by` 不是身份认证）

**分层声明**：

| 层 | 是什么 | 不是什么 |
| --- | --- | --- |
| `--by` / `confirmed_by` / `approved_by` 字段 | 调用者**自称**的操作者名，写入审计记录 | ❌ 身份认证（CLI 不验证） |
| `ProjectLock`（文件锁） | 防并发写交错 | ❌ 不是身份认证，也不阻止"合法锁内的恶意操作" |
| SHA-256 摘要 | 内容完整性声明（防误改） | ❌ 不是签名：无密钥绑定，任何人可重算 |
| `requirement_acceptance` 证据 | Agent 自述 | ❌ 不是用户验收 |

**区分两类威胁**：

| 威胁 | 例子 | 当前机制 |
| --- | --- | --- |
| **误操作** | 忘了确认就 apply、改了 staging 没重确认、串行跑错命令 | 状态门槛 + staging 冻结 + 门禁重算（有效） |
| **恶意操作者** | Agent 有仓库完整写权限，伪造审批记录、改 CR/manifest/摘要 | **当前无法防御**：无 OS 隔离、无外部审批锚点 |

**目标（production，待实现）**：审批凭据来自仓库外（签名审批文件 / 外部审批系统 /
CI approved review），CLI 核验"凭据 → 审批人 + CR id + staging manifest sha"一致才写
`approval` 记录；执行 Agent 不持有凭据、对正式 CR / 已确认 manifest / 审批记录只读。
没有这些外部边界时，**系统不能抵御恶意 Agent**，文档不得声称相反结论。

### 5.6 并发读取的真实边界（第五轮修订）

| 层 | 当前能力 | 目标能力 |
| --- | --- | --- |
| 写者互斥 | ✅ `MUTATING_COMMANDS` 统一取 `project_lock` | 保持 |
| 读者互斥 | ❌ 读命令不取锁，事务提交期间可观察中间态 | 所有读正式数据的命令取**同一项目锁**（读写互斥，非共享读锁），持锁完成全部正式读取，取锁失败立即拒绝 |
| 崩溃残留 | ❌ 无事务标记概念 | OPEN 标记残留时读者（即使持锁）仍拒绝；只有 `recover --apply` / 严格校验的 `recover --complete` 能解除，恢复前不开放正式读取 |
| 已知实现缺口 | `doctor` 读正式数据且写依赖图、`init` 写 `.devflow`，都不在 `MUTATING_COMMANDS`（INV-025）；读命令复用 `ProjectLock` 的行为（超时拒绝、跨平台 `fcntl`/`msvcrt`、重入深度）**待代码审计** | 见 08 D-02 §5b 命令分类表 |

> OPEN 标记不是读保护：第四轮"检查 OPEN 后无需锁"的结论已被撤销——
> 检查点与读取点之间存在竞态窗口，只有读写互斥能挡住已通过检查的读者。

## 六、权限边界层面的结论

1. **状态写权限收得比较紧**：project/plan 状态字段的写入都在 CLI 内，且 accepted 白名单默认拒绝；
   但**所有"操作者"字段（`--by`/`confirmed_by`）都只是审计声明**，CLI 不验证身份（见 5.5）。
2. **证据、需求基准、以及 `.devflow` 内其他节点的报告，写权限基本没收**：靠"事后校验 + 人读"。
   这是当前最大的权限缺口（对应 INV-002 / 016 / 021 / 023）。
3. **文件系统层没有隔离**：执行 Agent 技术上可改任何文件；`files_scope` 是**检测**而不是**阻止**。
4. **指纹不是安全机制**：它只能发现"改文件不改摘要"，不能对抗同时控制两者的攻击者（见 5.4）。
5. **并行隔离的前提是 worktree**：没有 per-node 工作区时，`git_changes_since` 的结果会互相污染；
   按 **D-03 / D-09**，生产级并行必须每节点独立 worktree，未实现前只允许串行或经确认的隔离执行。
