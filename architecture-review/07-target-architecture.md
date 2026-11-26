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

### C1 计划激活唯一入口：v1 用 `plan-activate`，v2+ 只能 `change-apply`

- **现状**：`plan-import` 允许 `based_on` 为空；`plan-activate` 不校验来源与版本，
  任意 draft 计划都能直接生效。
- **目标**（D-01）：
  - **v1**：`plan-activate` 允许，但仅当项目从未激活过计划（`current_plan_version is None`
    且没有别的 active 计划）。
  - **v2 及以后**：`plan-activate` **一律拒绝**（不是"补个 based_on 就放行"）；
    唯一入口是 `change-apply`，它同时完成计划切换、旧计划 supersede、CR→applied。
  - `change-apply` 必须交叉校验：CR `confirmed`、`plan.based_on.change_id == CR`、
    `based_on.plan_version == CR.base_plan_version`、`plan.plan_version == CR.target_plan_version`、
    CR 基准 == 项目当前指针、需求版本一致、`migration_problems` 为空、validate(activating) 通过。
  - `plan-import` 仍可创建草稿（准备变更），但草稿不能绕过 `change-apply` 生效。
  - 目标：只有 `dfstate` 能写 `plan.status=active` / `project.current_plan_version`。
- **历史项目**：无 `based_on` 的 v>1 计划由 `migrate` 标 `legacy_plan_origin`，只读保留，不允许再激活。
- **代价**：低（校验 + migrate 标记）。**收益**：堵住"无 CR 换计划"这条最大绕过。

### C2 需求基准内容指纹（INV-002 / INV-020）

- **现状**：只校验存在/长度；`req-import` 可在任意状态改写追踪表。
- **目标**（D-02，CLI 生成双 digest + `baselines.json` + **CR staging**）：
  - `req-confirm` 计算 `traceability_digest`（结构化数组规范化哈希）与
    `confirmed_doc_digest`（文档全文哈希），合成 `baseline_id`；
  - 写入 `project.requirement_baseline`，并**追加**到 `requirements/baselines.json`（不可覆盖）；
  - 计划 / CR / 回归报告 / 交付报告元信息都写 `requirement_baseline_id`；
  - `regression-import`、`release-ready`、`release-accept` 重算并逐项比对；
  - `req-import` 的"写正式"能力只保留到**首次确认之前**；之后只能写 CR 的 staging
    （`.devflow/changes/CR-xxx/staging/`），正式文件在 `change-apply` 前不变；
  - `change-apply` 在**一个事务**内切换：新版追踪表 + 基准文档 + `baselines.json` +
    正式指针 + `project.json` + 新/旧计划 + CR 状态 + 看板（完整顺序见 08 D-02 §5）；
  - 版本必须单调（`to_version == current + 1`），禁止跳版/回退。
- **代价**：中（新增字段 + 清单文件 + 多处比对 + 迁移）。**收益**：堵住"改文档/改状态不改版本"两条暗路。
- **边界**：digest 只证明内容一致，不证明确认者身份（见 D-02 / D-05）。

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

- **状态：已定（D-04）**。production 默认 `deny`。
- **目标**：
  - `production`：`git.ignored_policy = deny`；`git status --ignored --porcelain` 出现新增/修改即阻断；
    例外必须写 `node.ignored_allowlist` / 项目级 allowlist，每条含 `path` + `purpose` + `approved_by`。
  - `development`：允许 `off`，但项目档案标 `non_production_verification=true`，
    看板与交付报告显示"未做 ignored 校验（非生产级）"。
  - 配置错误（production 未开 deny）由 `doctor` 报错、`release-ready` 阻断；
    **不允许**用"报告里声明没检测"代替门禁。
- **代价**：中（处理 ignored 目录噪音，提供 allowlist 模板）。**收益**：补上 `.env`/构建产物这类盲区。
- **注意**：这是 D-03/09 之外的另一条"真实变更"缺口，不能因为 worktree 落地就认为已解决。

### C6 项目状态迁移表（INV-015）

- **目标**：`PROJECT_TRANSITIONS`，与节点表同构：

  | from | 允许 to | 触发 |
  | --- | --- | --- |
  | initialized | requirements_confirmed | `req-confirm` |
  | requirements_confirmed | planned | `plan-activate` |
  | planned | in_progress | `start` |
  | in_progress | regression | `regression-check`（无阻塞） |
  | regression | release_candidate | `release`（**全部门禁满足**，D-06） |
  | release_candidate | release_ready | `release-ready` |
  | release_ready | accepted | `release-accept --by` |
  | accepted | in_progress | `change-apply`（唯一出口） |

  **显式禁止**（目标）：
  - `planned/release_candidate → requirements_confirmed`（`req-confirm` 在已激活计划后应拒绝）；
  - `regression/release_candidate → planned`（换计划必须走 CR，且只能由 `change-apply` 回 `in_progress`）；
  - `release` 失败时改状态（D-06：保持原状态，只在报告中记 `blockers`）。

  - 所有写入改走 `dfstate.set_project_status(project, to, cause)`；表外迁移直接抛错。
  - `release` 全部门禁满足才置 `release_candidate`；失败保持原状态。
- **代价**：中（要改 8 处调用点 + 测试）。**收益**：消灭跳阶/回退。

### C7 多文件写统一事务（INV-017）

- **现状（已核实）**：只有 `change-apply` 使用 `Transaction`；`plan-activate`、`req-import`、
  `release`、`regression-import`、`accept/sync/start` 都是"多次单文件原子写"（见 02 §四）。
- **目标**：把 `Transaction` 提升为 `apply_multi(paths, name, files, mutate)`，覆盖：
  - `change-apply`（**需求变更时扩展为「需求变更原子提交文件集合」**：核心 9 个 + 派生依赖图，含新版追踪表、指针、`baselines.json`、
    新版基准文档、`project.json`、新计划、旧计划、CR、看板与依赖图；清单生成 / 前置检查 /
    回滚语义见 08 D-02 §5）；
  - `plan-activate`（仅 v1；旧计划 + 新计划 + project + board）；
  - `release`（md 归档 + md + json + project 状态）；
  - `regression-import`（归档 + json + md）；
  - `accept`（计划 + 看板 + deliverables）。
- **不覆盖** `req-import` 的正式写入：按 D-02，它已不再有"直接写正式"的权限（首次导入除外，
  首次导入只有两份文件，仍应走同一 `apply_multi`）。
- **事务 ≠ 读原子**：`apply_multi` 仍只保证写回滚；并发读一致性由事务标记 +
  统一读取守卫保证（08 D-02 §5b），两者是独立机制，必须一起实现。
- **代价**：中（要保证每个 mutate 幂等）。**收益**：中断不再留下"半套状态"。

### C8 报告完整性指纹（INV-018 / D3）

- **目标**：
  - `release` 在 `release-report.json` 写入 `report_sha256`；
  - `release-ready` 重算并比对，正文被手改就拒绝；
  - 节点报告同理：`accept` 时把两份报告的 sha256 记进 `node.deliverables.reports`，
    后续若被替换，`validate` 与 `start`（下游）能发现。
- **代价**：低。**收益**：把"验收后改证据"变成可检测。

### C9 证据可信度分层（INV-023）

按 **D-05** 分层，Agent 自报降级为"过程记录"：

| 层 | 机制 | 是否可作验收依据 |
| --- | --- | --- |
| L0（现有） | 报告自洽、`attempt_id`、统计一致 | ❌ 仅过程记录 |
| L1（生产门槛） | **CI 结果**：绑定 `commit_sha` + `baseline_id` + run id + 退出码 | ✅ 生产项目必须 |
| L2（可选增强） | `commands[]` 记 `stdout_sha256` / 耗时 / 环境（OS/Python） | 仅提高伪造成本 |
| L3（流程） | 交付前人工抽检 1-2 条命令 | 辅助 |

> 必须写清：SHA-256 与 CI 报告只证明"结果对应这个 commit 与这份需求基线"，
> **不证明执行过程真实**；若 CI 结果由被审计方自行产出且无外部锚点，仍是"自报的升级版"。
> 不把"内容指纹"当成"执行真实性"的证明。

### C10 证据/报告的写入边界（INV-016 延伸）

- **现状**：`.devflow` 整体被排除在 Git 变更之外，Agent 可以改**任何节点**的报告而不被发现。
- **目标**：
  - 报告路径必须落在 `nodes/<自己的 node_id>/`（`evidence_problems` 已校验 `node_id` 字段，
    但没有校验"报告文件是否被别的节点写过"）；
  - `start` 时记录"本次允许写的报告路径白名单"；`sync` 时校验这两份文件之外没有 `.devflow/nodes/*` 变化
    （可用 `source_snapshot` 的目录级哈希实现）；
  - 已 `completed` 节点的报告被改动 → `validate` 报错（用 C8 的 sha256）。

## 三、并行与隔离

| 场景 | 当前 | 目标（D-03 / D-09） |
| --- | --- | --- |
| 同组节点、`files_scope` 不重叠、无依赖 | 允许（`parallel_issues` 校验） | 保留；补"资源别名"识别 |
| **生产**环境同组并行 | 允许，但共享工作区 | **必须**每节点独立 worktree（或等效隔离）；`isolation` 未声明时 `plan-activate` 拒绝 |
| development 同组并行 | 共享工作区 | 允许，但必须在看板/报告标注"非隔离并行" |
| 跨组/跨阶段并行 | 不允许（依赖未完成不能 start） | 保留 |
| 执行 Agent 与主控同时写 `.devflow` | 锁保证互斥 | 保留；补"报告 sha 校验"（C8） |

> **不再提供"共享工作区靠 Git 差集归属"作为生产默认**：归属不可靠（见 06 B3），
> 只在 development 档位保留，并明确标注。worktree 尚未实现前，生产并行只能串行。

## 四、不变量 → 目标强制点（实施顺序建议）

| 优先级 | 不变量 | 目标机制 | 依赖 |
| --- | --- | --- | --- |
| 1 | INV-008 | C1 计划来源校验（无 `--replan` 旁路） | 无 |
| 2 | INV-015 | C6 项目迁移表（含 D-06 语义） | 无 |
| 3 | INV-002/003/020 | C2 需求双 digest + baselines 清单 | C3（指针校验） |
| 4 | **INV-029** | 交付门禁重算 baseline + 退役凭据 | C2 |
| 5 | INV-011/012 | C4 dirty 指纹 + C5 ignored deny（production 默认） | D-04（已定） |
| 6 | INV-023/024 | C9 CI 证据层级 + C8 报告指纹 | 外部 CI |
| 7 | INV-017 | C7 统一事务 | C6 |
| 8 | INV-010/011 | 资源别名 + worktree 隔离（生产必须） | C4 |

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

| 能力 | 现状 | 目标 | 依据 |
| --- | --- | --- | --- |
| 计划来源 | CR 只约束 `change-apply`；`plan-activate` 无来源校验 | 所有 v>1 计划必须绑定已确认 CR（无 `--replan` 旁路） | D-01 |
| 需求基准 | 只查存在/长度；无 digest | CLI 生成双 digest + `baselines.json`，全链路绑 `baseline_id` | D-02 |
| traceability | 双份无一致性校验；同版本可覆盖 | 指针哈希 + 导入归档 + 单调版本 | D-02 |
| 交付时需求校验 | 不校验退役凭据、不比对 digest | `release-ready`/`accept` 重算并校验 | D-02 / INV-029 |
| Git 归属 | 整份剔除 baseline dirty；并行互相污染 | 逐文件指纹；生产并行必须 worktree | C4 / D-03 |
| ignored | 不可见 | 待定策略（`allowlist`/`deny`/`off`） | D-04（待定） |
| 项目状态 | 命令直接赋值；可回退/跳阶 | 迁移表 + 单一入口；失败不改状态 | C6 / D-06 |
| 多文件写 | 只有 `change-apply` 有事务 | 统一 `apply_multi` | C7 |
| 报告 | 只校验内容 | 内容 + `report_sha256` | C8 |
| 证据真实性 | Agent 自证 + 自洽校验 | Agent 自报=过程记录；生产用 CI 结果（绑 commit + baseline） | D-05 |
| 并行隔离 | 同工作区 | 生产必须 worktree；未实现前只允许串行 | D-03 / D-09 |

> 仍待定的决策（D-07 / D-08 / D-10）见 `08-decisions-required.md`；D-01~D-06、D-09 已定案。
