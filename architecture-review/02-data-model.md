# 02 · 数据模型

> 所有字段名以 `schemas/*.schema.json` 为准；"写入者"以 `scripts/*.py` 实际写盘为准。

## 一、文件清单与定位

| 数据 | 路径 | 定位 | 唯一事实来源 | 写入者 | 是否版本化 |
| --- | --- | --- | --- | --- | --- |
| 项目档案 | `.devflow/project.json` | **正式数据** | 自身 | `df.py`（init/project-set/migrate/release-\*/change-apply） | 否（单文件，`updated_at`） |
| 需求追踪（当前） | `.devflow/requirements/traceability.json` | 正式数据（指针副本） | `traceability-vN.json` | `req-import` | 指针，无版本号字段校验 |
| 需求追踪（历史） | `.devflow/requirements/traceability-vN.json` | **正式数据** | 自身 | `req-import` | 是（按版本号） |
| 需求基准文档 | `.devflow/requirements/confirmed-requirements-vN.md` | **正式数据** | 自身 | **主控/Agent 直接写**（CLI 不写） | 是（文件名） |
| 澄清记录 | `.devflow/requirements/clarification.md` | 过程记录 | 自身 | 主控 | 否 |
| 计划（各版本） | `.devflow/plans/backlog-vN.json` | **正式数据** | 自身 | `plan-import` / `plan-activate` / `start` / `sync` / `accept` / `change-apply` | 是 |
| 看板 | `.devflow/plans/board-vN.md` | **派生** | `backlog-vN.json` | `render_board`（`refresh`） | 跟随计划版本 |
| 依赖图 | `.devflow/plans/dependency-graph-vN.md` | **派生** | `backlog-vN.json` | 内部模块 `dependency_check.py`（由 `plan-import` / `doctor` 调用；非独立 CLI 命令） | 跟随计划版本 |
| 变更申请 | `.devflow/changes/CR-xxx/change-request.json` | **正式数据** | 自身 | `change-new/set/apply` | 目录即版本 |
| 需求变更暂存（目标） | `.devflow/changes/CR-xxx/staging/**`（traceability / 基准文档 / plan 草稿 / manifest） | **草稿（不参与正式读取）** | 自身 | `change-stage`（待实现） | 随 CR |
| 需求基线清单（目标） | `.devflow/requirements/baselines.json` | **正式数据** | 自身 | `change-apply`（追加） | 只追加 |
| 变更说明/影响评估 | `changes/CR-xxx/{change-request.md, impact-assessment.md}` | 人读报告 | JSON 为准 | 主控/Agent | 否 |
| 提示词 | `.devflow/nodes/<ID>/prompt.md` | 正式输入 | 自身 + 版本元数据 | `prompt-init` | 是（`history/` + `prompt_version`） |
| 执行报告 | `.devflow/nodes/<ID>/execution-report.md` | **证据** | 内含 `devflow-execution` JSON | **执行 Agent 直接写** | 返工时归档 `history/` |
| 测试报告 | `.devflow/nodes/<ID>/test-report.json` | **证据** | 自身 | **执行 Agent 直接写** | 同上 |
| 回归报告（机器） | `.devflow/regression/final-report.json` | **正式数据** | 自身 | `regression-import` | 导入前归档 `regression/history/` |
| 回归报告（人读） | `regression/final-report.md` | 派生 | `final-report.json` | `regression-import` 渲染 | 同上 |
| 偏回归前置检查 | `regression/precheck.md` | 过程记录 | — | `regression-check` | 否 |
| 回归代码快照 | `regression/source-snapshot.json` | 证据 | 自身 | `regression-check` | 否（每次覆盖） |
| 交付报告 | `releases/release-report.md` | **报告** | 由计划/证据/回归汇总 | `release` | 覆盖前复制到 `releases/history/` |
| 交付报告元信息 | `releases/release-report.json` | 证据（版本/章节/门槛） | 自身 | `release` | **否（无归档）** |
| 事务日志 | `.devflow/.txn/<id>/journal.json` + `files/` | 恢复用 | 自身 | `Transaction` | 保留最近 20 个（`prune_transactions`） |
| 写锁 | `.devflow/.lock` | 运行时 | — | `ProjectLock` | 不删 |
| 决策记录 | `.devflow/decisions.md` | 过程记录 | 自身 | 主控 | 否 |
| 会话记录 | `.devflow/sessions/` | 过程记录 | — | （目录已建，代码中未见写入点） | — |

> `sessions/` 在 `Paths` 中定义并被 `init` 创建（`dfcore.py:555`、`df.py:253`），但全仓库没有写入它的代码 ——
> **文档/结构声明，代码未证实其用途**。

## 二、关系图

```mermaid
erDiagram
  PROJECT ||--|| REQUIREMENTS_CURRENT : "current_requirement_version → vN"
  REQUIREMENTS_CURRENT ||--|| REQUIREMENTS_VN : "同一内容副本"
  PROJECT ||--|| PLAN_ACTIVE : "current_plan_version → vN"
  PLAN_ACTIVE ||--o{ NODE : "nodes[]"
  PLAN_ACTIVE }o--|| REQUIREMENTS_VN : "requirement_version"
  NODE ||--o{ PROMPT : "prompt_file + prompt_version"
  NODE ||--o{ EXEC_REPORT : "attempt_id 绑定"
  NODE ||--o{ TEST_REPORT : "attempt_id 绑定"
  CHANGE ||--|| PLAN_ACTIVE : "based_on{change_id,plan_version} → target_plan_version"
  PLAN_ACTIVE ||--|| REGRESSION : "plan_version + requirement_version"
  REGRESSION ||--|| SOURCE_SNAPSHOT : "source_snapshot.snapshot_id"
  PROJECT ||--|| RELEASE_REPORT : "release_candidate → release_ready → accepted"
  RELEASE_REPORT ||--|| REGRESSION : "版本一致性校验"
```

## 三、版本号如何关联

| 版本 | 存放 | 谁引用 | 一致性校验位置 |
| --- | --- | --- | --- |
| `plan_version` | 计划文件名 + `backlog.plan_version` | 节点报告、回归报告、交付报告、CR、project | `validate.py:270`（文件名=内容）；`_binding_problems`（`dfcore.py:1818`）；`_regression_data_problems`（`df.py:2091`）；`release_report_problems`（`df.py:2484`） |
| `requirement_version` | 追踪表 + 计划 + project | 计划、报告、CR、回归、交付 | 计划→追踪表存在性（`df.py:598`）；报告比对（`dfcore.py:1837`）；**project ↔ 追踪表内容版本未比对** |
| `prompt_version` | 节点 + 提示词头 | `start` | `prompt_binding_problems`（`dfcore.py:1258`）五项比对 |
| `attempt_id` | 节点 + 两份报告 | `sync`/`accept` | `_binding_problems`（`dfcore.py:1818`） |
| `snapshot_id` | 回归报告 + `source-snapshot.json` | 交付 | `snapshot_problems`（`df.py:1844`） |
| `baseline_id`（目标） | 根 `project.requirement_baseline`、`baselines.json`、计划、CR、回归、交付 | 全链路 | 目标：`change-apply` 写入，交付时逐项比对（当前**无此字段**） |
| CR `change_id` | 目录 + JSON + 计划 `based_on` | `change-apply` | `df.py:1665-1685` |

## 四、必须原子一致的数据组（逐组核对写盘调用）

> 口径：**原子** = 同一组文件要么全部生效、要么全部可回滚（`Transaction`）。
> 只调用 `atomic_write_json` 是"单文件原子"，**不是**"数据组原子"。
> 本轮逐组核对了实际写盘调用，修正了上一版把 `change-apply` 的事务误记为 `plan-activate` 的错误。

| 组 | 涉及文件 | 写盘调用（准确到函数） | 是否有事务 | 中断后果 |
| --- | --- | --- | --- | --- |
| G1 计划生效（变更流程） | `backlog-vNew.json` → active；`backlog-vOld.json` → superseded；`project.json`；`board-vNew.md` | `cmd_change_apply`：`core.Transaction`（`df.py:1706-1714`）→ 逐 phase 写（`df.py:1717-1750`） | ✅ **唯一使用事务的地方** | 回滚到应用前（`recover --apply`） |
| G2 计划激活（普通流程） | 旧计划 superseded；新计划 active；`project.json`（`current_plan_version` + `status=planned`）；`board` | `cmd_plan_activate`：三次 `atomic_write_json`/`project.save` + `refresh()`（`df.py:610-632`） | ❌ 无事务 | 可能"旧计划已 superseded、新计划未 active"或"计划 active、project 未更新"；`doctor` 能发现部分（多 active / 指针无文件），需人工修 |
| G3 需求导入 | `requirements/traceability-vN.json` + `requirements/traceability.json` | `cmd_req_import`：两次 `atomic_write_json`（`df.py:422-426`） | ❌ 无事务 | 指针与版本文件不一致；无校验能发现 |
| G4 节点验收（成功） | `backlog-vN.json`（node→completed + deliverables）；`board-vN.md` | `cmd_accept`：`transition()` 改内存 → `refresh()`（`save_plan` + 渲染，`df.py:152-158`） | ❌ 无事务（两次单文件原子写） | 计划已更新、看板未更新（状态仍可从 JSON 恢复） |
| G4' 节点验收（拒绝） | `backlog-vN.json`（实时 `scope_violations` / `git_*`） | `cmd_accept` 拒绝前 `project.save_plan(plan)`（`df.py:1366`） | ❌ 单文件写 | 只落盘证据性字段，状态不变 |
| G5 节点开工 / 同步 | `backlog-vN.json`（in_progress/attempt_id 或 needs_review） + `board-vN.md` | `cmd_start` / `cmd_sync` → `transition()` + `refresh()` | ❌ 无事务 | 计划已变、看板未变 |
| G6 回归导入 | `regression/final-report.json` + `regression/final-report.md`（+ 旧报告移入 `history/`） | `cmd_regression_import`：`_archive_regression()`（`df.py:1819-1840`）+ 两次写（`df.py:1866-1880`） | ❌ 无事务；**不改 `project.json`**（状态由 `regression-check` 设置） | 可能出现 JSON 已换、Markdown 仍旧 |
| G7 交付报告 | `releases/release-report.md`（旧版复制到 `history/`）+ `releases/release-report.json` + `project.json`（status=`release_candidate`） | `cmd_release`：`shutil.copy2` → `atomic_write_text` → `atomic_write_json` → `project.save`（`df.py:2409-2437`） | ❌ 无事务，共 4 次写 | 报告与元信息/状态可能不一致 |
| G8 交付验收 | `project.json`（status=`release_ready` / `accepted` + 时间戳/验收人） | `cmd_release_ready` / `cmd_release_accept`：单次 `project.save` | ✅ 单文件写（无需事务） | — |

**目标扩展（D-02，待实现）**：G1 在"含需求变更的 CR"场景下扩展为
**「需求变更原子提交文件集合」（Commit Set）**——**业务文件数量由 CR 内容与项目状态
动态决定，不使用固定数量口径**。集合分五类：核心状态文件（指针 / project / 新计划 / CR，
所有 CR 必有）、条件性核心文件（新版追踪表 / 基准文档 / baselines.json 仅需求变更 CR；
旧计划存在才纳入）、派生文件（看板 + 依赖图，所有 CR 必须重渲染）、事务元数据
（journal）与事务快照（后两者是基础设施，不计入业务数量）。
清单生成、`begin` 前置检查与回滚语义见 `08-decisions-required.md` D-02 §5；
**事务只保证写回滚；并发读一致性由读写互斥（读者取同一项目锁）+ OPEN 崩溃标记保证
（D-02 §5b），是另外的机制**。

**结论修正**：

* 上一版把 `plan-activate` 也标为"走 Transaction"是**错误**的；事务只存在于 `change-apply`。
* 只有 G8 是天然原子的（单文件）；G1 是唯一"多文件 + 事务"；其余 6 组都是"多次单文件原子写"，
  中断后需要人工/`doctor` 发现，没有自动回滚协议。

## 五、失效关系（谁变了，谁必须失效）

| 变化 | 应失效 | 代码是否实现 |
| --- | --- | --- |
| 源代码变化 | 回归结论、交付报告 | ✅ `snapshot_problems`（`df.py:1844`，仅比对回归报告记录的快照） |
| 计划内容/版本变化 | 旧提示词、旧回归、旧交付报告 | 部分：提示词 ✅（`prompt_binding_problems`）；回归 ✅（版本比对）；交付报告 ✅（`release_report_problems` 比对版本） |
| 需求内容变化（同版本） | 回归、交付、节点证据 | ❌ 无内容指纹，只在版本号变化时失效（见 INV-020） |
| 需求被标成 `deferred/removed/out_of_scope`（同版本） | 交付门禁对该需求的检查 | ❌ `delivery_blockers` 直接跳过非 active 需求（`df.py:2231-2232`），退役凭据也不在交付阶段校验（见 A6） |
| `baseline_id` 变化（目标） | 旧回归、旧交付报告、受影响节点证据 | 目标：靠字段比对失效；当前无该字段（见 08 D-02 §7） |
| 节点验收后交付物被改 | 下游 `start` 需重新评估 | ✅ `context_changes`（`dfcore.py:1106`） |
| 契约文件被改 | 下游 `start` 需重新评估 | ✅ 同上（`deliverables.contracts`） |
| 看板被手改 | 无 | ❌ 无哈希校验，只能靠重渲染覆盖 |

## 六、多份副本 / 一致性风险（实测结论）

### R1 `traceability.json` 与 `traceability-vN.json` 是同一事实的两份副本

- 写入点：`df.py:423-426` 先写版本文件再写指针文件。
- 无一致性校验：`validate.py:290-300` 只校验 schema 与"文件存在"，不比对内容或 `requirement_version` 与
  `project.current_requirement_version`。
- 后果：手工改 `traceability.json`（或中断在两次写之间）后，`regression-check`/`release` 都读指针文件，
  历史版本文件与当前事实可能不一致，且无人发现。**P1**

### R2 需求基准文档没有内容指纹

- `req-confirm` 只检查文件存在 + 文本长度 ≥ 200 + 三个关键词（`df.py:435-448`）。
- 之后任何人都可以改 `confirmed-requirements-v1.md` 内容而版本号不变；回归报告只绑定
  `requirement_version=1`，不会失效。
- **Git 可见性要说清**：如果 `.devflow/` 已被提交，`git status`/`git diff` 本身**能看到**这个改动；
  但 DevFlow 的变更检测 `git_changes_since` 显式过滤 `.devflow/`（`dfcore.py:996-1000`），
  所以**流程不会消费**这条信息。两者不是一回事。
- **这直接违反"正式需求基准只能通过变更流程修改"的设计目标**（见 INV-002 / INV-019）。**P0**

### R3 `req-import` 同版本重复导入会静默覆盖历史

- `plan-import` 有 `--force` + `.history/` 归档（`df.py:501-509`）。
- `req-import` 无守卫、无归档，`--version 1` 再导一次即覆盖 `traceability-v1.json`（`df.py:423`）。
- **P1**（历史可被静默覆盖）

### R4 `release-report.json` 没有历史归档

- `release-report.md` 覆盖前复制到 `releases/history/`（`df.py:2409-2416`）。
- 元信息 JSON 直接覆盖（`df.py:2433`）。
- 影响有限（md 有历史），但"版本一致性证据"只剩最新一份。**P2**

### R5 示例项目与当前交付门禁不自洽

- `examples/sample-project/.devflow/project.json`：`schema_version=1.1`、`status=accepted`。
- 但目录中**没有** `regression/final-report.json`、没有 `releases/release-report.json`、
  也没有 `legacy_regression_accepted` / `legacy_release_shortcut` 标记。
- 用当前代码对同一状态执行 `release-ready` 会因缺机器可读回归报告而失败（`df.py:2067-2089`）。
- 即：**仓库的"标准示例"无法用当前代码复现**，示例测试也只跑 `validate` 与业务逻辑（
  `examples/sample-project/tests/*` 不涉及交付门禁）。**P1**（示例可信度）

### R6 看板与计划可能短期不一致

- `accept` 在拒绝路径先 `project.save_plan(plan)`（`df.py:1366`）但不渲染看板；看板内容仍是上一次 `refresh` 的结果。
- 因为状态未变（仍是 `needs_review`），看板语义上不错误；但 `scope_violations` 等字段不会出现在看板上，
  用户从看板看不出"验收被拦了"。**P2**

### R7 `release_ready` 之后仍可改写需求追踪表（P0）

- **写入路径**：`cmd_req_import`（`df.py:399`）没有项目状态前置，只有 accepted 被 `main()` 白名单挡住；
  `release_ready` / `release_candidate` 下都可以导入。
- **影响**：导入同版本追踪表、把某条 active 需求改成 `removed`（不写 `reason`/`confirmed_by`），
  交付门禁会**跳过**该需求（`df.py:2231-2232`），而 `release-ready` / `release-accept` 都**不**重新跑
  `validate_all`、也**不**检查退役凭据（`cmd_release_ready` `df.py:2565`、`cmd_release_accept` `df.py:2602`）。
- **与 R2 的区别**：R2 是"改内容不改版本"；R7 是"改状态让需求退出交付范围"，两件事都不需要 CR。
- **完整链路与复现步骤**见 `06-cross-module-risks.md` A6。

## 七、数据模型层面的结论

1. 事实来源划分总体清晰：状态在 JSON、展示在 Markdown、证据在节点目录。
2. 但存在 **两类"第二事实来源"**：`traceability.json` 与需求基准 Markdown，二者都缺少防漂移机制。
3. 所有"版本一致性"校验都基于**版本号**，没有基于**内容指纹**；因此"同版本改内容"是系统性盲区。
4. 原子性**只覆盖变更流程的 `change-apply`（G1）**；`plan-activate` 虽然同样改三四个文件，
   但**没有事务**。其余 G2/G3/G4/G5/G6/G7 都是"多次单文件原子写"，中断后会留下不一致。
5. 需求侧还有一条独立通道：`req-import` 可在任意非 accepted 状态改写追踪表（含 `release_ready`），
   而交付门禁跳过非 active 需求 —— 数据模型上"需求是否在范围内"没有一个不可事后修改的锚点（R7 / A6）。
