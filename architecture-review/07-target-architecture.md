# 07 · 目标架构建议

> 原则：**保留**文件驱动、JSON 事实来源、Markdown 展示、Python CLI 状态机、单主控 + 多执行 Agent；
> **不引入**数据库/服务端/消息队列；**不重写**，只加"收口点"和"指纹"。
> 所有建议都标注：与现状的差异、为什么值得做、代价。

## 一、核心思路：把"分散的规则"收成三个收口点

当前问题不是缺少校验，而是校验分散在 `df.py`（28 个命令）、`dfcore.py`（40+ 函数）、`validate.py` 里，
同一条不变量在不同入口的实现不一致（例：`plan-import` 归档历史，`req-import` 不归档）。

建议新增 3 个薄模块，把跨命令的规则集中，CLI 只负责参数与输出：

| 新模块 | 职责 | 收口的规则 |
| --- | --- | --- |
| `dfstate.py` | 所有状态迁移的唯一入口 | Node/Project/Plan/CR 迁移表、状态前置校验、accepted 守卫 |
| `dfintegrity.py` | 所有"内容指纹/快照"的唯一入口 | 需求基准 digest、traceability 指针 digest、报告 digest、源码快照 |
| `dfpolicy.py` | 所有"能不能进下一步"的判定 | 验收门禁、回归门禁、交付门禁（从 `df.py` 抽出 `delivery_blockers`/`release_report_problems`） |

> 不是"为了分层而分层"：这三块正是过去 4 轮 bug 的集中地（每次修都是"某个入口漏了一次判断"）。

## 二、十项具体变更（现状 → 目标）

### C1 计划版本变更必须可追溯（对应 INV-008）

- **现状**：`plan-import` 允许 `based_on` 为空；`plan-activate` 不校验来源。
- **目标**：
  - `plan_version == 1`：允许无 `based_on`（首次）。
  - `plan_version > 1`：`based_on.change_id` 必填，且该 CR 必须 `confirmed`；
    `plan-activate` 校验 CR 存在且 `target_plan_version == version`。
  - 提供显式逃生口 `plan-import --replan --reason "..."`：写入 `based_on.kind="replan"` + 责任人，
    仍产生审计记录（但不是 CR）。
- **代价**：低（校验 + schema 字段）。**收益**：堵住当前最大绕过。

### C2 需求基准内容指纹（INV-002 / INV-020）

- **现状**：只校验存在/长度。
- **目标**：`req-confirm` 计算 `sha256(confirmed-requirements-vN.md)` 写入
  `project.requirement_baseline = {version, file, sha256, confirmed_by, at}`；
  - `regression-import` 把 `requirement_digest` 写进报告；
  - `release-ready`/`release-accept` 重算 digest，不一致即"需求基准已变更，需重跑回归"；
  - 变更需求必须走 CR 生成 v(N+1)，旧 digest 自然失效。
- **代价**：低。**收益**：把"改文档不改版本"这条暗路封死。

### C3 traceability 单一事实 + 指针校验（INV-003 / INV-018）

- **目标**：
  - `traceability.json` 增加 `content_sha256`（对 `requirements` 数组规范化后哈希）；
  - `req-import` 同版本重复导入必须 `--force`，并归档到 `requirements/history/`；
  - `validate` 比对：`traceability.json.content_sha256` == `traceability-vN.json` 计算值，
    且 `requirement_version` == `project.current_requirement_version`。
- **代价**：低-中（要迁移旧文件，可容忍缺失时 warn 一次）。

### C4 Git 变更归属修正（INV-012）

- **现状**：`changes -= protected` 整份剔除 baseline dirty 文件。
- **目标**：baseline 记录 **每个 dirty 文件的内容指纹**（复用 `file_fingerprint`）；
  事后逐个比较：
  - 指纹未变 → 不算本次改动；
  - 指纹变化 → 算本次改动（并标记 `preexisting_dirty=True`，报告里提示"这个文件开工前就有改动"）。
- **代价**：中（每次 start 多算几个哈希）。**收益**：解决最隐蔽的漏报。

### C5 ignored 文件策略（INV-011）

- **目标**：配置 `git.ignored_policy`：
  - `deny`（production 默认）：`git status --ignored --porcelain` 里出现**新增/修改**的 ignored 文件即阻断，
    除非在 `node.ignored_allowlist` 显式声明（写清原因）；
  - `allowlist`：只允许白名单目录（如 `dist/`）；
  - `off`：不管（development）。
- **代价**：中（要处理 ignored 目录噪音）。**收益**：补上 `.env`/构建产物这类盲区。

### C6 项目状态迁移表（INV-015）

- **目标**：`PROJECT_TRANSITIONS`，与节点表同构：

  | from | 允许 to |
  | --- | --- |
  | initialized | requirements_confirmed |
  | requirements_confirmed | planned, requirements_confirmed |
  | planned | in_progress, planned |
  | in_progress | regression, in_progress |
  | regression | release_candidate, in_progress（节点返工） |
  | release_candidate | release_ready, regression（重跑） |
  | release_ready | accepted, release_candidate（复核失败回退） |
  | accepted | in_progress（仅 change-apply） |

  - 所有写入改走 `dfstate.set_project_status(project, to, cause)`；表外迁移直接抛错。
  - `release` 在 blockers 非空时**不再**改状态（或改为 `release_generated` 中间态，见 D-06）。
- **代价**：中（要改 8 处调用点 + 测试）。**收益**：消灭跳阶/回退。

### C7 多文件写统一事务（INV-017）

- **目标**：把 `Transaction` 提升为 `apply_multi(paths, name, files, mutate)`：
  - `req-import`（traceability-vN + 指针）
  - `release`（md + json + project）
  - `regression-import`（json + md + project）
  - `plan-activate`（旧计划 + 新计划 + project + board）
- **代价**：中（要保证每个 mutate 幂等）。**收益**：中断不再留下"半套状态"。

### C8 报告完整性指纹（INV-018 / D3）

- **目标**：
  - `release` 在 `release-report.json` 写入 `report_sha256`；
  - `release-ready` 重算并比对，正文被手改就拒绝；
  - 节点报告同理：`accept` 时把两份报告的 sha256 记进 `node.deliverables.reports`，
    后续若被替换，`validate` 与 `start`（下游）能发现。
- **代价**：低。**收益**：把"验收后改证据"变成可检测。

### C9 证据可信度分层（INV-023）

无法做到"证明命令真跑过"，但可以分层降低伪造收益：

| 层 | 机制 | 成本 |
| --- | --- | --- |
| L1（现有） | 报告自洽、`attempt_id`、统计一致 | 0 |
| L2（建议） | `commands[]` 记录 `stdout_sha256` + 耗时 + 环境（OS/Python 版本） | 低 |
| L3（建议，可选） | `test-report.json` 由测试运行器直接产出（提供 `df.py record-test --cmd ...` 包装） | 中 |
| L4（流程） | 抽检：交付前人工重跑 1-2 条命令 | 人力 |

> 目标不是"不可伪造"，而是"伪造需要付出与真实执行相近的成本"。

### C10 证据/报告的写入边界（INV-016 延伸）

- **现状**：`.devflow` 整体被排除在 Git 变更之外，Agent 可以改**任何节点**的报告而不被发现。
- **目标**：
  - 报告路径必须落在 `nodes/<自己的 node_id>/`（`evidence_problems` 已校验 `node_id` 字段，
    但没有校验"报告文件是否被别的节点写过"）；
  - `start` 时记录"本次允许写的报告路径白名单"；`sync` 时校验这两份文件之外没有 `.devflow/nodes/*` 变化
    （可用 `source_snapshot` 的目录级哈希实现）；
  - 已 `completed` 节点的报告被改动 → `validate` 报错（用 C8 的 sha256）。

## 三、并行与隔离

| 场景 | 当前是否允许 | 目标 |
| --- | --- | --- |
| 同组节点、`files_scope` 不重叠、无依赖 | 允许（`parallel_issues` 校验） | 保留；补"资源别名"识别 |
| 同组节点共享工作区 | 允许（靠 Git 差集归属） | **建议要求 worktree 隔离**，否则验收时把并行改动算到彼此头上 |
| 跨组/跨阶段并行 | 不允许（依赖未完成不能 start） | 保留 |
| 执行 Agent 与主控同时写 `.devflow` | 锁保证互斥 | 保留；补"报告 sha 校验" |

> 若短期不做 worktree：至少在 `accept` 输出里区分 `attributed_to_this_node` 与 `workspace_other_changes`，
> 并要求主控对后者显式确认（类似 `--allow-scope`）。

## 四、不变量 → 目标强制点（实施顺序建议）

| 优先级 | 不变量 | 目标机制 | 依赖 |
| --- | --- | --- | --- |
| 1 | INV-008 | C1 计划来源校验 | 无 |
| 2 | INV-015 | C6 项目迁移表 | 无 |
| 3 | INV-002/020 | C2 需求 digest | C3（指针校验） |
| 4 | INV-012 | C4 dirty 指纹 + C5 ignored 策略 | 无 |
| 5 | INV-018/023 | C8 报告指纹 + C9 证据分层 | 无 |
| 6 | INV-017 | C7 统一事务 | C6 |
| 7 | INV-010/011 | 资源别名 + worktree 隔离（可选） | C4 |

## 五、测试策略（与不变量一一对应）

1. **不变量测试模板**：每条 INV 至少 3 条用例 —— 正常路径、被禁止的路径、绕过尝试。
   建议文件 `tests/test_invariants.py`，用例名 `test_inv_008_plan_requires_change`。
2. **状态机穷举**：`for cmd in ALL_COMMANDS: for status in PROJECT_STATUSES:` 断言"是否应拒绝"，
   替代现在零散的"某个命令在某状态下"测试（`AcceptedGuardTest` 已是雏形）。
3. **属性测试（无第三方库也能做）**：随机生成 `files_scope` 集合与路径，断言
   `scope_match` 与 `_glob_to_regex` 语义一致；随机生成 plan，断言 `parallel_issues` 不误报互不相交的组。
4. **故障注入**：在 `atomic_write_json` 上打补丁抛异常，验证 G1/G2/G4/G5 各组要么全成要么可回滚。
5. **反向测试**：显式构造"应该被拒绝"的样本（无 CR 的 v2、改过的需求文档、手改的报告），
   断言 CLI 返回 2 且信息可读。

## 六、与现状的差异汇总

| 能力 | 现状 | 目标 | 差异性质 |
| --- | --- | --- | --- |
| 计划来源 | CR 只约束 change-apply | 所有 v>1 计划都要基于 CR/审计记录 | 收紧 |
| 需求基准 | 无指纹 | digest + 回归绑定 | 新增 |
| traceability | 双份无校验 | 指针哈希 + 导入归档 | 收紧 |
| Git 归属 | 整份剔除 baseline dirty | 逐文件指纹比较 | 修正 |
| ignored | 不可见 | 策略化阻断 | 新增 |
| 项目状态 | 命令直接赋值 | 迁移表 + 单一入口 | 重构 |
| 多文件写 | 只有 change-apply 有事务 | 统一 `apply_multi` | 扩展 |
| 报告 | 只校验内容 | 内容 + 指纹 | 新增 |
| 证据真实性 | 自洽校验 | 分层（自洽→可追溯→可选抽查） | 分层 |
| 并行隔离 | 同工作区 | 可选 worktree | 可选 |

> 需要产品/架构决策的点见 `08-decisions-required.md`。
