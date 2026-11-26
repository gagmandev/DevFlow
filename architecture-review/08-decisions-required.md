# 08 · 架构决策

> 本文件分两部分：**已定决策**（本轮采纳，作为第二阶段实施依据）与**仍待定**（需要产品/架构再拍板）。
> 已定决策不因"实现成本"回退；如果实施中发现不可行，必须回到本文件改决策，而不是在代码里留后门。

## 一、已定决策

### D-01 计划激活只有一个入口：v1 用 plan-activate，v2+ 只能 change-apply

**决策**：
1. **首次计划 v1**：允许 `plan-activate`，前置条件是"项目从未激活过任何计划"
   （`project.current_plan_version is None` 且不存在 active 计划）。
2. **v1 激活之后**：所有 v2 及后续计划**只能**通过 `change-apply` 生效；
   `plan-activate` 对 `plan_version > 1` **一律拒绝**（不是"检查 `based_on` 后放行"）。
3. `plan-import` 可以创建任意版本的**草稿**（准备变更用），但草稿不能直接生效。
4. `change-apply` 必须同时满足以下**全部**条件，缺一即拒绝：
   - CR `status == confirmed`；
   - `plan.based_on.change_id == CR.change_id`；
   - `plan.based_on.plan_version == CR.base_plan_version`；
   - `plan.plan_version == CR.target_plan_version`；
   - `CR.base_plan_version == project.current_plan_version`（`base=0` 也参与比较）；
   - `CR.base_requirement_version == project.current_requirement_version`；
   - `plan.requirement_version == CR.target_requirement_version`；
   - `migration_problems(old_plan, new_plan, CR)` 为空；
   - `validate(activating=True)` 0 错误。
5. `change-apply` 是**唯一**把计划置为 `active`、把旧计划置为 `superseded`、把 CR 置为 `applied` 的入口。
   任何其他命令都不得写 `status=active`（目标：只有 `dfstate` 能写）。

**为什么不能只查 `based_on`**：`based_on` 是计划自己声明的字段，计划文件本身可被改写；
必须同时用 CR 的 `target_plan_version` / `base_*` 与项目当前指针做交叉校验，
才能证明"这份计划确实属于这个已确认的 CR，且基于当前生效版本"。

**推论**：
- 若历史项目存在无 `based_on` 的 v>1 计划：`migrate` 标 `legacy_plan_origin`，只读保留，不允许再激活。
- `plan-activate` 的错误信息必须明确指向 `change-apply`，而不是提示"补 based_on"。

**对应**：INV-008；风险 A1 / B2；实施点 `plan-activate` / `change-apply` / `migrate`。

### D-02 需求基线：CLI 指纹 + CR 暂存 + 原子切换

#### 1. 谁能改正式需求（阶段矩阵）

| 项目阶段 | 普通 `req-import`（写正式） | CR 暂存（写 staging） | 说明 |
| --- | --- | --- | --- |
| 尚无正式需求（`current_requirement_version is None` 且无 active 计划） | ✅ 允许 | — | **首次导入只在此阶段** |
| 已首次确认、计划未激活 | ❌ 拒绝 | ✅ 允许 | 修改也必须走 CR |
| 计划已激活（`planned`/`in_progress`） | ❌ 拒绝 | ✅ 允许 | 同上 |
| 回归 / 交付（`regression`/`release_candidate`/`release_ready`） | ❌ 拒绝 | ✅ 允许 | 只有 `change-apply` 能改正式并让旧证据失效 |
| `accepted` | ❌ 拒绝（白名单） | ✅ 允许（change-* 流程） | 通过 `change-apply` 退出终态 |

> 关键：`req-import` 的"写正式"能力**只保留在第一次确认之前**。一旦确认过，
> 它只能以"暂存"模式工作，且暂存目标必须是某个 CR 的 staging 目录。

#### 2. 暂存数据（独立目录，绝不提前覆盖正式文件）

```
.devflow/changes/CR-001/staging/
  manifest.json                     # 文件清单 + 每个文件 sha256 + base_baseline_id + created_by/at
  requirement/
    traceability.json               # 提议的新版全量追踪表（requirement_version = N+1）
    confirmed-requirements-vN.md    # 提议的新版需求基准
    baseline.json                   # {version, traceability_digest, confirmed_doc_digest, baseline_id}
  plan/
    backlog-vN.json                 # 提议的新计划草稿（based_on 指向本 CR）
```

- **staging 不参与**任何正式读取：`load_traceability` / `plan-activate` / 回归 / 交付都不会读它。
- **正式文件在 apply 之前完全不变**：`traceability.json`、`traceability-vN.json`、
  `confirmed-requirements-vN.md`、`baselines.json`、`project.json` 都不动。
- 暂存命令（待实现）：`df.py change-stage --change CR-001 --req <traceability.json> --doc <md>`
  （或 `change-set --stage`）。它只做校验 + 写 staging + 更新 `manifest.json` 与 CR 的
  `requirement_change` 摘要，不改生命周期。

暂存时校验：
- Schema、`acceptance_spec_problems`、`requirement_retirement_problems`（退役凭据）；
- `requirement_version == current + 1`（禁止跳版/回退）；
- 新基准文档长度/章节；
- 计划草稿的 `requirement_version == 新版本`、`based_on.change_id == CR`。

#### 3. 谁来确认：操作者声明 ≠ 用户审批（信任边界）

**先分清两个概念，全文不得混用**：

| 概念 | 含义 | 现在的载体 |
| --- | --- | --- |
| 操作者声明（audit claim） | 调用者自称"我是谁"，CLI 不做任何验证 | `--by` 参数、`confirmed_by` 字段 |
| 用户审批（approval） | 由**可验证的外部凭据**证明"某个真实的人同意了这次变更" | **当前不存在**（目标见下） |

**当前事实**：`change-set --status confirmed --by 用户名` 中的 `--by` 是**调用者自行填写的
字符串**，CLI 不验证它是谁。任何能执行命令的进程（包括执行 Agent）都可以填任意名字。
因此 `confirmed_by` 只能作为**审计线索**，不是身份认证；`ProjectLock` 防并发、
SHA-256 防误改，它们也都**不是**身份认证机制。

**普通本地模式（development）**：`--by` 只作审计声明使用，文档与输出不得把它称为
"已验证的审批人"。

**生产模式（production）必须有独立审批机制**（目标，待实现）：

1. **审批凭据与仓库分离**：审批结果由 CLI 从**外部锚点**取得并核验，例如
   专用审批文件上的数字签名（公钥固定在 CLI 配置或环境，不放在仓库内）、
   外部审批系统回调、或 CI 平台的 approved review 记录（API 取回 + 校验目标 commit）。
2. **CLI 如何验证**：核验"签名/凭据 → 审批人身份 + 审批对象（CR id + staging manifest sha）"
   三者一致才写 `approval = {mechanism, subject, verified_at, ref}`；`confirmed_by` 降级为
   审批记录里的展示字段。凭据核验失败 → 拒绝确认。
3. **执行 Agent 不得持有审批凭据**：签名私钥/审批令牌只存在于用户侧或 CI 侧；
   执行 Agent 的运行环境不提供它们。
4. **执行 Agent 不得直接修改**：正式 CR 文件、已确认的 staging manifest、审批记录
   —— 这些路径在目标架构下对执行 Agent 只读（worktree + `.devflow` 只读，见 D-03）。

**防护能力边界（必须如实声明）**：

- 在**没有** OS 级权限隔离或外部审批锚点的部署里，系统**不能抵御**拥有仓库完整写权限的
  恶意 Agent——它可以同时改 CR、manifest、审批记录和摘要。
- 因此本节机制属于**防误操作**：防止主控/Agent 走错流程、忘确认、改了 staging 没重确认。
  **防恶意操作者**只能靠外部边界：文件系统权限、独立 worktree、外部审批锚点、外部 CI。
- 两类威胁的对应关系见 `04-permission-boundaries.md` §5.5。

#### 4. baseline_id 如何绑定

```
traceability_digest   = sha256(canonical_json(requirements[]))
confirmed_doc_digest  = sha256(confirmed-requirements-vN.md)
baseline_id           = sha256("v{N}|" + traceability_digest + "|" + confirmed_doc_digest)
```

| 数据 | 写入字段 | 校验点 |
| --- | --- | --- |
| CR | `requirement_change.to_baseline_id` | 确认时写；apply 时重算比对 |
| Plan | `requirement_baseline_id` | `change-apply` 要求 == CR 的 baseline_id |
| Project | `requirement_baseline = {version, baseline_id, digests, confirmed_by, at}` | apply 时写 |
| baselines.json | 追加 `{version, baseline_id, digests, cr_id, at}` | 只追加，不覆盖 |
| Regression 报告 | `requirement_baseline_id` | 导入时写；交付时比对 |
| Release 元信息 | `requirement_baseline_id` | `release-ready`/`accept` 比对 |

#### 5. `change-apply` 的提交顺序与「需求变更原子提交文件集合」（目标）

**命名**：不再称"9 文件事务"，也**不使用任何固定数量口径**（"10 个文件"、"9+1"等一律废除）。
统一称**「需求变更原子提交文件集合」（Commit Set）**。
**业务文件数量由 CR 内容与当前项目状态动态决定**：有无旧计划、是否含需求变更，
都会改变集合内容。

**集合的五个类别**：

| 类别 | 文件 | 纳入条件 |
| --- | --- | --- |
| **核心状态文件** | `requirements/traceability.json`（正式指针）；`project.json`；`plans/backlog-v{N}.json`（新计划置 active）；`changes/CR-xxx/change-request.json`（CR → applied） | 所有 CR 都必须 |
| **条件性核心文件** | `requirements/traceability-v{N}.json`（新版追踪表）；`requirements/confirmed-requirements-v{N}.md`（新版基准文档）；`requirements/baselines.json`（追加）；`plans/backlog-v{Old}.json`（旧计划置 superseded） | 仅需求变更 CR 纳入前三个；旧计划**存在才纳入**（首次应用无旧计划则没有这一项） |
| **派生文件** | `plans/board-v{N}.md`、`plans/dependency-graph-v{N}.md`（+ 同版本旧文件重渲染） | 所有 CR 都必须，由提交后的计划重新生成，**不允许遗漏** |
| **事务元数据** | `.devflow/.txn/<id>/journal.json`（清单 + phase + status） | 所有事务都有；属于**事务基础设施，不计入业务 Commit Set 数量** |
| **事务快照** | `.devflow/.txn/<id>/snapshot/**`（begin 时的文件内容备份） | 所有事务都有；同上，不计入业务数量 |

**集合差异示例（仅为说明动态性，不是固定清单）**：

- 需求变更 CR + 已有旧计划：核心 4 + 条件性 4 + 派生 2 = **10 个业务文件**；
- 纯计划 CR + 已有旧计划：核心 4 + 条件性 1（仅旧计划）+ 派生 2 = **7 个业务文件**；
- 纯计划 CR + 首次应用（无旧计划）：**6 个业务文件**。
- journal 与快照在以上每种情况下都存在，但**不计入**业务文件数量。

**清单生成与校验**：

- `change-apply` 在 `Transaction.begin` 前按 CR 内容（是否含 `requirement_change`、
  `current_plan_version` 是否存在）生成**最终完整清单**写入 `journal.json`；
  每条含 `path` + `action`（create / replace / append / delete-derived）+ `existed_before`。
- **每个路径在清单中只能出现一次**；重复 → `begin` 前置检查拒绝。
- 其余 `begin` 前置检查：父目录存在或可创建；按 `action` 校验目标当前状态
  （`replace` 要求存在、`create` 要求不存在）；staging digest 重算通过。

**回滚语义**：快照记录 `existed_before`——true → 按快照内容恢复；false → **删除该文件**
（不留空文件）。**派生文件回滚后必须恢复到旧计划对应的状态**（按旧计划重渲染），
不允许留新计划的看板。

**提交顺序**（指针切换仍只在第 7 步）：

| 序 | 动作 |
| --- | --- |
| 0 | 全部校验（含清单生成、digest 重算）；失败直接返回，不写任何文件 |
| 1 | 获取项目写锁 → `Transaction.begin`：快照 Commit Set，写 journal，**创建 OPEN 标记**（§5b） |
| 2–6 | 写新版追踪表 / 基准文档 / 追加 baselines.json / 激活新计划 / 旧计划 superseded |
| 7 | **切换正式指针** `requirements/traceability.json` |
| 8 | 更新 `project.json` |
| 9 | 关闭 CR |
| 10 | 重渲染派生数据（看板 + 依赖图） |
| 11 | journal 置 `committed` → **删除 OPEN 标记**（最后一步）→ 释放写锁 |

#### 5b. 并发读取一致性：读写互斥 + OPEN 崩溃标记（第五轮修订）

**先承认事实**：现有 `Transaction` 只保证**写入快照回滚**（异常时把文件恢复到 begin 前），
**不提供任何并发读取原子性**。第四轮的"检查 OPEN 后无需锁"结论**已被推翻**：
读者检查完 OPEN、尚未读完文件时，写者可以完成整个事务并删除标记，
读者随后读到新旧混合的数据（检查点与读取点之间的竞态窗口）。
OPEN 标记**只能**充当崩溃残留的哨兵，**不能**保护已通过检查的读者。

**选定方案：读者与写者使用同一互斥锁（读写互斥）**

> 说明：这是**读写互斥**，不是"共享读锁"——读者之间也互斥（串行读）。
> 读 durations 短、频率低，串行可接受；换来的是协议简单、与现有写锁同一机制。

**写入端协议**（`change-apply` 等 `MUTATING_COMMANDS`）：

1. 获取项目写锁（现有 `project_lock`，独占）。
2. 持锁期间完成**全部四件事**：创建 OPEN 标记 → 事务写入 → journal 置 `committed` →
   删除 OPEN 标记。任何一步都不释放锁。
3. 释放锁。

**读取端协议**（所有读取正式项目数据的命令）：

1. 读取任何正式数据（`project.json` / `backlog-vN.json` / `traceability*.json` /
   节点报告 / 回归 / 交付）**之前**，先获取与写者相同的项目锁（同一互斥机制）。
2. **获取失败（超时）→ 立即拒绝**，退出码非 0，提示"项目正在被修改或提交，请稍后重试"；
   不无限等待。
3. **在完成全部正式数据读取之前不释放锁**——锁必须覆盖"读 project + 读计划 + 读追踪表 +
   读节点报告"的整个读取阶段，而不是单次文件读。
4. 读取结束（含派生文件渲染完成）后释放锁。

**OPEN 标记的双重角色**（保留，但不再是读保护的主力）：

1. 正常提交：写者持锁完成全部步骤后删除标记；持锁的读者在提交期间根本进不来，
   进入后看到的必然是 committed 后的完整状态。
2. **崩溃残留**：进程被 kill 后写锁释放但 OPEN 残留 → 读者即使拿到锁，
   检查 OPEN 发现残留也**必须拒绝**；只有 `df.py recover --apply`（回滚到快照并删标记）
   或经过严格校验的 `recover --complete`（确认 journal=committed 后清标记）能解除。
   **恢复完成前不开放正式读取**。读取顺序：取锁 → 检查 OPEN → 无残留才读。

**顺序不变式**：

```
写者：  取写锁 → OPEN → 写盘 → committed → 删 OPEN → 释放锁
读者：  取锁 → 检查 OPEN（残留则拒绝）→ 读取全部正式数据 → 释放锁
恢复：  取锁 → 校验 journal → 回滚或确认 → 删 OPEN → 释放锁
```

**并发行为汇总**：

| 场景 | 读者结果 |
| --- | --- |
| 写者持锁提交中 | 取锁失败 → 拒绝（不观察中间态） |
| 提交已完成、标记已删 | 正常读到新状态 |
| 事务已回滚 | 正常读到旧状态 |
| 崩溃、OPEN 残留 | 取锁成功但检查 OPEN → 拒绝，直到 recover |

**实现约束（必须如实记录，不得假设已支持）**：

- 现有 `ProjectLock`（`dfcore.py`，每路径 `RLock` + OS 文件锁 + 重入深度）是为
  **写命令**设计的独占锁。把它复用到读命令**尚未经过代码审计**：
  ①读命令的超时拒绝路径需要在 `main()` 里对非 MUTATING 命令也调用 `project_lock`
  （当前只对 MUTATING 加锁）；②锁文件以 `O_RDWR|O_CREAT` 打开，读命令复用是可行的，
  但 Windows/macOS 的 `msvcrt`/`fcntl` 行为差异、重入深度与线程归属在"短持有 + 高频读"
  场景下是否表现正确——**待代码审计**；③读者互斥导致读串行，若实测读延迟不可接受，
  再评估按节点分桶锁等优化（不预先设计）。
- 在完成审计前，本节协议是**目标设计**；当前实现只有写互斥，
  并发读在提交期间可能观察中间态，只能靠运维约定缓解。

**命令读取路径分类表**（第六轮修订：按**实际读取与写入内容**分类，命令名与
CLI parser 逐一核对——parser 当前共注册 **33** 个命令，加上 **1** 个待实现目标命令 `change-stage`，
本表共覆盖 **34 条目**（33 个现存 CLI 命令 + `change-stage`），无虚构、无遗漏；
未确认行为标"待代码审计"）：

| 类别 | 判定标准 | 锁协议 | 命令 |
| --- | --- | --- | --- |
| **隔离临时项目** | 全程操作 `tempfile.mkdtemp` 临时目录，不读不写任何正式项目状态 | 不参与本项目锁协议 | `self-test`（注 1） |
| **读取正式状态** | 读 project / plan / requirements / 节点报告；不写 `.devflow` 任何文件 | 取锁 → 查 OPEN → 读 → 释放 | `status` / `next` / `task` / `validate` |
| **读正式状态 + 写派生** | 读正式状态，重渲染看板/依赖图等派生文件 | 取锁 → 查 OPEN → 读 → 写派生 → 释放 | `board` / `graph` / `doctor`（注 2） |
| **读正式状态 + 写 staging** | 读完整正式基准（追踪表 / baseline / 计划版本），计算 staging 基准信息；只写 `changes/*/staging/**`，**不修改正式状态** | 取锁（与正式写者同一互斥锁）→ 查 OPEN → 读完整正式基准 → 生成 staging → **完成后释放锁** | `change-stage`（注 3，待实现） |
| **事务性修改正式状态** | 多文件正式写入，需要 OPEN + journal + Transaction 崩溃恢复 | 取**写**锁 → OPEN → 事务写入 → committed → 删 OPEN → 释放 | `change-apply`（当前唯一；见「OPEN + journal 适用范围」） |
| **非事务写正式状态** | 读 + 写正式文件，但当前未纳入 Transaction，**只有锁互斥、无崩溃恢复** | 取**写**锁（`MUTATING_COMMANDS`，现有协议）；崩溃后可能留下部分写入，恢复依赖 `doctor` + 人工 | `init`（注 4，首次初始化锁）/ `migrate` / `project-set` / `prompt-init` / `req-import` / `req-confirm` / `plan-import` / `plan-activate` / `change-new` / `change-set` / `start` / `sync` / `accept` / `retry` / `block` / `fail` / `cancel` / `supersede` / `regression-check` / `regression-import` / `release` / `release-ready` / `release-accept` |
| **事务恢复** | 读 journal / 快照 / OPEN，按恢复协议回滚或确认 | **独立恢复协议**（注 5），不走普通读取路径 | `recover` |

> `scripts/dependency_check.py` 是**内部模块**（被 `doctor` / `validate` 内部调用，
> `df.py:3338`），**不是 CLI 命令**——parser 里没有 `dependency-check`，本表不再列出。

**注**：

1. **`self-test`**：全部操作发生在 `tempfile.mkdtemp` 临时目录（`df.py:2730`），与任何正式
   项目状态隔离，**当前不读取、不修改正式项目数据**，因此不参与本项目锁协议、不列入
   "读取正式状态"类。**若未来支持对既有项目执行 self-test，必须重新进行锁协议分类**。
2. **`doctor`**：读正式数据（`validate_all` + 各项检查，`df.py:3328`）且内部模块
   `dependency_check` 会写依赖图（`df.py:3338`），但**不在 `MUTATING_COMMANDS` 里**——
   当前实现"读正式 + 写派生却不加锁"，是已知实现缺口（与 INV-025 一致）；
   目标归入"读正式状态 + 写派生"类。
3. **`change-stage` 与 `change-apply` 的职责边界**：`change-stage` = **读正式状态 + 写 staging**
   （读取期间必须持有与正式写者相同的互斥锁——staging 虽不是正式状态写入，但读取正式基准
   需要一致的快照视图，不能凭命令名豁免）；`change-apply` = **事务性修改正式状态**
   （唯一使用 OPEN + journal + Transaction 的命令）。两者都不直接改正式状态的部分分别
   是"零"（stage 只写 staging）与"事务内"（apply 的正式修改全在 Commit Set 内）。
4. **`init` 与首次初始化锁**：`init` 创建 `.devflow` 本身，而现有 `ProjectLock` 的锁文件
   在 `.devflow/.lock`——**锁位置不能依赖尚未创建的目录**。目标约束：
   ①首次初始化必须在创建 `.devflow` **之前**建立并取得"初始化互斥锁"；
   ②锁文件位置独立于 `.devflow`（候选：项目根的固定文件，具体位置、跨平台实现
   （`fcntl`/`msvcrt`）与初始化完成后锁文件的生命周期（保留/删除/迁移入 `.devflow`）
   ——**待实现设计 + 代码审计**，不得假定现有 `ProjectLock` 已支持）；
   ③并发 `init` 必须串行化（同一项目不能同时初始化）；
   ④初始化过程必须持锁直到项目基础状态完整写入。
   **两个锁要区分**：已初始化项目的普通写锁（现有 `project_lock`）vs
   尚未初始化项目的首次初始化锁（待设计）。`init` 当前不在 `MUTATING_COMMANDS`
   （INV-025 已记录）。
5. **`recover`**：在 `MUTATING_COMMANDS` 里（取写锁），但必须额外校验恢复状态
   （journal phase / status）才能执行；`--apply` 回滚到快照并删 OPEN；
   `recover --complete`（只允许在 journal=committed 时清标记）**尚未在 parser 与 handler 中实现**
   （当前仅支持 `--apply`）。它不走普通读取/写入路径。
6. parser 当前实际注册 **33** 个命令（`accept/block/board/cancel/change-apply/change-new/
   change-set/doctor/fail/graph/init/migrate/next/plan-activate/plan-import/project-set/
   prompt-init/recover/regression-check/regression-import/release/release-accept/
   release-ready/req-confirm/req-import/retry/self-test/start/status/supersede/sync/task/
   validate`），加上待实现的 `change-stage`，本表 7 类共覆盖 **34 条目**（33 个现存 CLI 命令 +
   1 个目标命令）。**未列出的未来新增命令默认按最严格处理**：
   只要读取正式数据就纳入互斥协议；是否有例外以本表 + 代码审计为准。

**互斥锁 / 事务原子性 / 崩溃恢复：三个概念严格区分（第六轮新增）**

| 概念 | 是什么 | 提供什么 | 不提供什么 |
| --- | --- | --- | --- |
| A. 互斥锁（`project_lock`） | 并发写交错防护 | 同一时刻只有一个写者 | ❌ 不等于事务原子性；❌ 不等于崩溃恢复 |
| B. OPEN + journal（事务型写入） | 多文件写入的崩溃恢复机制 | 崩溃后 `recover` 回滚到 begin 前状态 | ❌ 不是所有写命令都有；❌ 不提供并发读原子（读原子靠 A + OPEN 哨兵） |
| C. 非事务型写入 | 持锁但无 Transaction 的正式写入 | 只有互斥 | ❌ 崩溃后可能留下部分写入（如"旧计划已 superseded、project 未更新"），**不能声称具备崩溃恢复能力** |

**「OPEN + journal」的适用范围（必须严格限定，不得推广到所有写命令）**：

- **必须创建 OPEN + journal 的（事务型写入）**：当前只有 **`change-apply`**——
  它提交「需求变更原子提交文件集合」（多文件、指针切换，部分写入不可接受）。
- **目标应纳入事务但当前未纳入**（记录为后续实现阶段工作，当前崩溃后有部分写入风险，
  风险清单见 `02-data-model.md` §四 G2–G7）：`plan-activate`（G2：旧计划/新计划/project/
  看板，3+ 次写）、`req-import`（G3：2 写）、`release`（G7：4 写）、
  `regression-import`（G6：2 写 + 归档）、`accept`（G4：计划 + 看板 + deliverables）。
  这些命令在纳入事务之前，**不得被描述为"具备事务恢复能力"**。
- **单文件写、无需事务**：`req-confirm` / `project-set` / `migrate`（单次 `project.save`）、
  `change-new` / `change-set`（CR 目录内写入）、`prompt-init`、`release-ready` /
  `release-accept`（单次 project.save + 派生输出）。它们持锁互斥即可，
  崩溃风险限于单文件原子性（`atomic_write` 保证）。
- **journal 生命周期**：`begin` 创建（phase=open）→ 写入过程中逐步更新 →
  全部写完置 `status=committed` → 删 OPEN。崩溃后 journal 停在 open →
  `recover --apply` 校验 phase 后回滚到快照并清 OPEN；journal=committed 但 OPEN 残留 →
  `recover --complete` 校验后清标记。

**结论一句话**：**互斥锁只能防止并发交错，不等于事务原子性，也不等于崩溃恢复能力。**
事务原子性需要 OPEN + journal + Transaction（当前仅 `change-apply`）；
崩溃恢复需要 recover 协议（校验 journal phase/status）；三者缺一不可、不可互相替代。

**当前实现**：本节目标协议（读端取锁、OPEN、分类表中的目标归类、首次初始化锁）**均未实现**。
现状：写命令有 `project_lock`（互斥，非事务）；只有 `change-apply` 用 Transaction；
读命令不加锁、不检查 OPEN；`doctor`/`init` 存在"读正式甚至写派生却不持锁"的缺口；
`self-test` 操作临时目录、天然隔离。收口前只能靠运维约定（提交期间不跑读命令）缓解。

#### 6. 失败与回滚

- 第 0 步失败：什么都没写，staging 保留，CR 仍 `confirmed`，可修复后重试。
- 第 1–10 步任一失败：`Transaction.rollback` 按 `existed_before` 恢复文件集合
  （existed_before=true 按快照恢复；false 删除），CR 回到 `confirmed`，`applied_at` 不写；
  rollback 完成后删除 OPEN 标记（§5b），重新开放读取。
- 进程被 kill：journal 停在 open、OPEN 标记残留 → **所有读命令持续拒绝**；
  `df.py recover --apply` 回滚并清标记；`doctor` 报告残留事务。
- **不允许**"指针切了但 project 没更新"这种半提交在无人干预下长期存在。

#### 7. 回归/发布证据何时失效

| 时点 | 失效对象 | 机制 |
| --- | --- | --- |
| apply 成功瞬间 | 旧回归结论 | 报告的 `requirement_baseline_id` ≠ 新 baseline → `regression_problems` 阻断 |
| apply 成功瞬间 | 旧交付报告 | 元信息 baseline 不匹配 → `release_report_problems` 阻断 |
| apply 成功瞬间 | 受影响节点的验收证据 | CR 必须把受影响节点置回 `ready`；交付时 `requirement_acceptance_problems` 按新验收标准比对，旧的会失败 |
| apply 成功瞬间 | 旧计划 | `superseded`，节点不再参与统计 |

> 这里的关键是：**证据失效不是"删文件"，而是"绑定值对不上"**。旧报告保留为历史，但不再能作为有效证据。

#### 8. 明确边界

SHA-256 证明"内容与摘要一致"，**不证明**"这份内容经过谁确认"——它没有密钥绑定，
任何人可重算，不是签名。`confirmed_by` / `--by` 只是操作者**声明**，CLI 不做身份验证；
`ProjectLock` 只防并发交错。**确认动作的可信度只能来自外部审批锚点**（签名审批文件 /
外部审批系统 / CI review，production 目标见 §3）；没有锚点的部署只能防误操作，
不能防恶意操作者。

**对应**：INV-002 / INV-003 / INV-020 / INV-029；风险 A2 / A3 / A6；待实现。

### D-03 / D-09 生产并行必须每节点独立 worktree

**决策**（两条合并为一条，避免互相矛盾的默认策略）：
1. 生产环境的多 Agent 并行**必须**每节点独立 Git worktree（或等效的独立工作区），
   节点在自己的工作区改代码、跑测试、产出证据；主控负责合并与验收。
2. **Worktree 未实现前，禁止共享工作区的"生产级"并行执行**：只允许
   - 串行执行；或
   - 经过验证的隔离执行（例如容器/独立 clone），并在项目档案里显式记录隔离方式。
3. 共享工作区下的 Git 变更归属不可靠（`git_changes_since` 会把别的节点改动算进来），
   因此**不能**作为生产并行方案的默认实现。

**推论**：`parallel_group` 在生产档位下若没有 `isolation: worktree` 声明，`plan-activate` 应拒绝
（或降级为串行并记录）。development 档位可保留共享工作区，但必须在看板/报告里标注"非隔离并行"。

**对应**：INV-010 / INV-011 / INV-012；风险 B1 / B3；决策 D-09 不再单独存在。

### D-04 ignored 文件：production 默认 deny（已定）

**决策**：
1. `production` 档位默认 `git.ignored_policy = deny`：
   `git status --ignored --porcelain` 中出现**新增/修改**的 ignored 文件即阻断验收与交付。
2. 确需纳入的路径必须写进 `node.ignored_allowlist` 或项目级 `git.ignored_allowlist`，每条必须包含
   `path` + `purpose`（用途说明）+ `approved_by`；缺任一项视为未授权。
3. `development` 档位允许 `off`，但必须在项目档案里标 `non_production_verification=true`，
   且交付报告与看板要显示"未做 ignored 文件校验（非生产级验证）"。
4. **不能**用"交付报告里声明没有检测"代替生产门禁：production 下未开启 deny 视为配置错误，
   `doctor` 报错、`release-ready` 阻断。

**待实现**：`git status --ignored` 的噪音处理（`dist/`、缓存目录默认加入 allowlist 模板）。

**对应**：INV-011；风险 B2；实施点 `dfcore.git_changes_since` / 配置 / `release*`。

### D-05 Agent 自报只是过程记录，生产回归以 CI 为准

**决策**：
1. `execution-report.md` / `test-report.json` 由 Agent 提交的内容，**只作为过程记录**，
   不具备独立验收权。
2. 生产项目的关键回归必须使用**可核验的 CI 结果**，且结果需绑定：
   - `commit_sha`（被验证的代码版本）；
   - `baseline_id`（需求基线，见 D-02）；
   - CI 运行标识（run id / URL / 环境）+ 退出码。
3. 信任边界必须写清：
   - SHA-256 与 CI 报告只能证明"这份结果对应这个 commit 和这份需求基线"，
     **不能证明"命令确实在无人干预下执行过"**；
   - 若 CI 结果由被审计方自行生成且无可信锚点（如平台签名、独立存储），它仍然只是"自报"的升级版。
4. 交付校验：无 CI 绑定的生产回归 → `release-ready` 阻断（development 可降级为警告并留痕）。

**对应**：INV-023 / INV-024；风险 D5。

### D-06 发布门禁失败不改状态

**决策**：
1. `release` 只负责**生成报告**，不因"报告生成成功"而改变生命周期状态。
2. 门禁不满足时：
   - 报告与元信息照常写入（便于对照修）；
   - `release-report.json.blockers` 记录未通过项；
   - **`project.status` 保持原状态**（不得进入 `release_candidate`）。
3. 只有**全部门禁满足**时，`release` 才把状态置为 `release_candidate`。
4. 不使用同一个 `status` 字段同时表示"报告已生成"和"具备候选资格"；两者分别是
   `release-report.json` 的存在性与 `project.status`。

**推论**：`release-ready` 的准入仍是 `release_candidate`；`release` 变成可重复执行的幂等操作
（每次都重算门禁）。旧项目若已处于 `release_candidate` 但门禁不满足，`release-ready` 会拒绝并要求重跑 `release`。

**对应**：INV-015 / INV-024；风险 D2。

## 二、仍待定

### D-07 旧项目兼容路径的退场时间（待定）

- `legacy_release_shortcut` / `legacy_regression_accepted` / `release --legacy-regression` 目前无退出计划。
- 需要产品给出"哪个版本移除"；在此之前 `doctor` 应把 legacy 项目标为需要处理。

### D-08 示例项目是否重做（待定）

- 现状：`examples/sample-project` 是 schema 1.1 + accepted，缺机器可读回归/交付报告，当前代码无法复现。
- 选项：迁移补齐 / 用 `self-test` 生成新示例 / 降级为"纯业务代码示例"。
- 影响：CI 里的 doctor 提示、文档引用、新人基线。

### D-10 是否拆分 `df.py`（待定）

- 建议拆 `dfstate.py` / `dfintegrity.py` / `dfpolicy.py`（见 07 §一），但本轮不决策；
  如果 D-01/02/06 的实施量较大，拆分可以作为同期重构。

## 三、决策 → 不变量 → 实施点

| 决策 | 不变量 | 唯一实现入口（目标） | 对现有代码的改动性质 |
| --- | --- | --- | --- |
| D-01 计划激活唯一入口 | INV-008 | `plan-activate` 只允许 v1；v2+ 仅 `change-apply` | 收紧（新拒绝路径） |
| D-02 需求暂存 + 原子切换 | INV-002/003/017/020/029 | `change-stage`（暂存）+ `change-apply`（唯一提交） | 新增 staging 目录/字段 + 事务扩展 |
| D-03/09 worktree 并行 | INV-010/011/012 | `plan-activate`/`change-apply` 校验隔离声明 | 约束 + 新拒绝路径 |
| D-04 ignored deny | INV-011 | `dfcore.git_changes_since` + 配置 + `release*` | 新增检测 + 门禁 |
| D-05 CI 证据 | INV-023/024 | `regression-import` 接收 CI 结果；`release*` 校验 | 新增字段 + 门禁 |
| D-06 失败不改状态 | INV-015/024 | `release` 状态写入点 | 行为变更（可能影响旧脚本） |

## 四、实施顺序（与 07 §四一致）

1. D-01（计划激活唯一入口）—— 改动小、堵最大绕过。
2. D-06（release 状态语义）—— 决定状态机表，越早越省事。
3. D-02（staging + digest + 原子切换）—— 依赖 D-01 的 CR 绑定；含事务扩展（C7）。
4. D-04（ignored deny）—— 与 D-03 的 worktree 约束一起完成"真实变更"闭环。
5. D-03/09（worktree 约束）—— 文档 + 激活校验。
6. D-05（CI 证据）—— 需要外部 CI 配合，放最后。

> 每一步都必须同时补 `tests/test_invariants.py` 的对应用例，并在 `09-audit-index.md` 更新状态。
