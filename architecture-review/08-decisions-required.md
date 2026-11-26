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

#### 3. 谁来确认

- **主控**负责提议（写 staging、填 CR 的影响评估）。
- **用户**负责确认：`change-set --change CR-001 --status confirmed --by 用户名 --reason "..."`。
  确认时 CLI 重算 staging 的双 digest，并与 `manifest.json` 比对；不一致 → 拒绝确认
  （防止确认后又被改）。
- 确认写入 CR：`requirement_change = {from_version, to_version, from_baseline_id,
  to_baseline_id, staging_manifest_sha256, confirmed_by, confirmed_at}`。
- 确认后的 staging 视为**冻结**：再改 staging 必须重新确认（`manifest_sha256` 变化 → CR 回到 draft）。

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

#### 5. `change-apply` 的事务文件组与提交顺序（目标）

| 序 | 动作 | 文件 | 说明 |
| --- | --- | --- | --- |
| 0 | 全部校验（含 staging digest 重算） | — | 不动文件；失败直接返回 |
| 1 | 开启事务快照 | 下表全部文件 | `Transaction.begin` |
| 2 | 写入新版追踪表 | `requirements/traceability-v{N}.json` | 从 staging 复制 |
| 3 | 写入新版基准文档 | `requirements/confirmed-requirements-v{N}.md` | 从 staging 复制 |
| 4 | 追加基线清单 | `requirements/baselines.json` | **追加**，不覆盖历史 |
| 5 | 激活新计划 | `plans/backlog-v{N}.json` | `status=active` + `requirement_baseline_id` |
| 6 | 旧计划置为 superseded | `plans/backlog-v{Old}.json` | 无旧计划则跳过 |
| 7 | **切换正式指针** | `requirements/traceability.json` | 从这一刻起正式读的是新版本 |
| 8 | 更新项目档案 | `project.json` | `current_requirement_version` / `requirement_baseline` / `current_plan_version` / `status` |
| 9 | 关闭 CR | `changes/CR-xxx/change-request.json` | `status=applied` + `applied_at` + `applied_baseline_id` |
| 10 | 重渲染派生数据 | `plans/board-v{N}.md`、`plans/dependency-graph-v{N}.md` | 由新计划生成 |
| 11 | 提交事务 | `.devflow/.txn/<id>/journal.json` | `status=committed` |

**指针切换时机**：只在第 7 步、且在整个事务内。对外语义是"要么全切、要么全不切"。

**并发读的问题（已知限制）**：读命令不持锁，理论上可能在第 2–10 步之间读到中间态。
目标增强：`change-apply` 开始时写 `.devflow/.txn/<id>/OPEN`，所有读命令检测到 open 事务
就提示"项目正在提交，请稍后"，或短暂等待锁。**当前实现没有这个机制**，只靠写锁互斥写者。

#### 6. 失败与回滚

- 第 0 步失败：什么都没写，staging 保留，CR 仍 `confirmed`，可修复后重试。
- 第 1–10 步任一失败：`Transaction.rollback` 按快照恢复全部文件（含指针与 project）；
  CR 回到 `confirmed`，`applied_at` 不写。
- 进程被 kill：journal 停在 open → `df.py recover --apply` 回滚；`doctor` 会报未完成事务。
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

SHA-256 证明"内容与摘要一致"，**不证明**"这份内容经过谁确认"。确认动作的可信度来自
`confirmed_by` + 项目锁 + 审计记录，不来自哈希。

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
