# 01 · 当前架构

> 审查基线：`a227e71473def3be1c53453e3c599a7f2f3a3140`（main）
> 方法：阅读全部 `scripts/`（6916 行）、`schemas/`（7 个）、`config/`、`tests/`（引擎 3597 行 / 示例 892 行）、
> `examples/sample-project/`、`.github/workflows/ci.yml`、`SKILL.md` 与 `docs/`。
> 结论均标注代码位置；文档声明与实现不符处单独标记为「文档声明，代码未证实」。

## 一、系统定位（实际实现）

DevFlow 是一个**纯文件 + Python CLI 的状态机**：没有数据库、没有服务端、没有守护进程。
所有跨会话状态落在项目根的 `.devflow/`，所有状态变更由 `scripts/df.py` 的单个命令完成。

```
主控 Agent ──(调用 CLI)──> df.py ──(读写)──> .devflow/*.json + *.md
                              │
执行 Agent ──(读写报告)───────┘        Git 工作区（业务代码）
```

代码规模：`df.py` 3574 行（CLI + 业务编排）、`dfcore.py` 2226 行（领域模型/校验/锁/事务）、
`validate.py` 596 行（Schema + 语义校验）、`render_board.py` 247 行（纯渲染）、
`dependency_check.py` 273 行（DAG 分析）。

## 二、模块划分（职责 / 输入 / 输出 / 写入文件 / 依赖 / 调用者）

| # | 模块 | 职责 | 输入 | 输出 | 写入文件 | 依赖 | 调用者 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | 项目档案 | 初始化目录、写/改 `project.json` | 目录现状、patch、profile | `project.json` | `.devflow/project.json`、`.devflow/decisions.md`、`.gitignore` | dfcore.Paths | 用户、主控（`init` / `project-set` / `migrate`） |
| M2 | 需求导入 | 校验并落地需求追踪表 | `traceability*.json` | 版本化追踪表 + 当前指针 | `requirements/traceability-vN.json`、`requirements/traceability.json` | Schema、`acceptance_spec_problems` | 主控（`req-import`） |
| M3 | 需求确认 | 确认基准、推进项目状态 | `confirmed-requirements-vN.md`、追踪表 | `current_requirement_version`、状态 | `project.json` | M2 | 主控/用户（`req-confirm`） |
| M4 | 计划导入 | 归一化节点、检环、渲染看板与依赖图 | `backlog.json` | 草稿计划、看板、依赖图 | `plans/backlog-vN.json`、`plans/board-vN.md`、`plans/dependency-graph-vN.md` | validate、dependency_check、render_board | 主控（`plan-import`） |
| M5 | 计划激活 | 校验后把 draft 置为 active，旧计划 superseded | 计划版本 | active 计划 + `planned` 状态 | `backlog-vN.json`、`backlog-vOld.json`、`project.json` | validate（activating） | 用户确认后由主控执行（`plan-activate`） |
| M6 | 提示词生成 | 按模板生成带版本元数据的 `prompt.md` | 节点、需求、依赖 | 提示词文件（历史归档） | `nodes/<ID>/prompt.md`、`nodes/<ID>/history/` | 需求追踪、计划 | 主控（`prompt-init`） |
| M7 | 节点执行 | 校验状态/依赖/提示词/上下文后开工 | 节点 ID | `in_progress` + `attempt_id` + Git baseline | `backlog-vN.json`、`board-vN.md` | Git 快照、上下文比对 | 主控或执行 Agent 触发（`start`） |
| M8 | 状态同步 | 读证据、校验、合并 Git 变更、判定范围 | 两份报告 | `needs_review` / `failed` | 同上 + 节点报告目录 | `evidence_problems`、`evaluate_node_changes` | 主控（`sync`） |
| M9 | 节点验收 | 实时复算 Git/范围、记录豁免与交付物指纹 | 节点 ID | `completed` + `deliverables` | `backlog-vN.json`、`board-vN.md` | Git、scope、evidence | 主控（`accept`） |
| M10 | 范围与 Git 检查 | 规范化路径、符号链接边界、范围匹配、真实变更 | 文件列表、baseline | `scope_violations` / `git_discrepancies` | 由 M8/M9 落盘 | Git、`_glob_to_regex` | M8、M9 |
| M11 | 并行安全 | 依赖可达性 + 资源冲突判定 | 计划 | errors / warnings | 无（只读） | dfcore、dependency_check | validate、dependency_check、plan-import |
| M12 | 变更 CR | 新建/编辑/应用变更，事务提交 | CR JSON、新版计划 | 新 active 计划、状态回 `in_progress` | `changes/CR-xxx/*`、`backlog-vN.json`、`project.json`、board | Transaction、validate、migration_problems | 主控（`change-new/set/apply`） |
| M13 | 回归管理 | 前置检查、快照、导入机器可读报告 | 回归 JSON | `regression` 状态、`final-report.{json,md}` | `regression/*`、`project.json` | source_snapshot、required_checks | 主控（`regression-check/import`） |
| M14 | 交付报告 | 汇总事实、生成报告与元信息 | 计划、证据、回归 | `release_candidate` | `releases/release-report.{md,json}` | delivery_blockers | 主控（`release`） |
| M15 | 交付验收 | 复核门禁、报告完整性、源码快照 | — | `release_ready` / `accepted` | `project.json` | release_report_problems、snapshot_problems | 用户（`release-ready/accept`） |
| M16 | 锁与事务 | 跨进程/跨线程互斥、快照回滚 | 项目路径 | 锁、journal、回滚 | `.devflow/.lock`、`.devflow/.txn/*` | OS 文件锁 | `main()`（MUTATING 集合） |
| M17 | 体检与迁移 | validate/doctor/migrate、事务恢复 | — | 报告、迁移记录 | `project.json`、依赖图 | 全部 | 主控/用户 |
| M18 | 自测 | 临时目录跑完整生命周期 + 并行 + 外部报告 | — | 分层报告 | 仅系统临时目录 | 全部 | 开发者（`self-test`） |

### 每个模块**不应该**承担的职责

| 模块 | 不该做 | 现状 |
| --- | --- | --- |
| M1 项目档案 | 不推进生命周期 | ✅ 已限制（`PROJECT_PROTECTED_FIELDS`，`df.py:98`） |
| M4/M5 计划 | 不写节点执行证据 | ✅ 只写计划结构 |
| M8/M9 同步验收 | 不执行测试、不生成证据 | ✅ 只校验；证据由 Agent 写 |
| M10 范围检查 | 不判断业务正确性 | ✅ 只做路径/范围 |
| M13 回归 | 不替项目跑测试 | ✅ 只校验报告自洽；实际命令由人/Agent 执行 |
| M16 锁 | 不做业务校验 | ✅ 只互斥 |
| M18 自测 | 不碰真实项目 | ⚠️ 设计如此，但 `--out` 可写任意路径（`df.py:3252` 起） |

## 三、关键实现机制

### 1. 命令与锁

- 33 个命令由 `add(...)` 注册（`df.py:3389 build_parser / 3399+ add(...)`），写命令集合 `MUTATING_COMMANDS`（`df.py:47`）。
- `main()` 对 `MUTATING_COMMANDS` 统一加锁后执行，并在锁内重读 `project.json`（`df.py:3550-3565`，`Project.reload` `dfcore.py:1294`）。
- accepted 白名单 `ACCEPTED_ALLOWED`（`df.py:78`）+ `accepted_denial()`（`df.py:212`）。
- 锁实现：每项目一个 `_LockSlot`（`RLock` + OS 文件锁），等待在全局 guard 之外（`dfcore.py:357-410`）。

### 2. 校验

- Schema 校验器是自研子集（`validate.py:31` `SchemaValidator`，支持 `$ref/type/enum/oneOf/items/...`）。
- 语义校验集中在 `validate._validate_plan`（`validate.py:337+`）与 `dfcore` 的若干 `*_problems()`。
- 证据校验 `dfcore.evidence_problems`（`dfcore.py:2086`）是验收的唯一入口。

### 3. Git 与范围

- `git_snapshot`（`dfcore.py:952`）记录 HEAD + 执行前 dirty 文件；`git_changes_since`（`dfcore.py:970`）做差集。
- `scope_violations`（`dfcore.py:710`）做规范化 + 符号链接 + 通配符匹配。
- `evaluate_node_changes`（`df.py:1148`）把「报告声明 ∪ Git 实际」合并，`sync`/`accept` 共用。

### 4. 事务

- 仅 `change-apply` 使用 `Transaction`（`dfcore.py:781`），提交文件列表见 `df.py:1706-1714`。
- 提交顺序：快照 → journal → 逐文件写 → `committed`；失败回滚；中断由 `recover`（`df.py:2641`）回滚。

### 5. 测试与 CI

- 引擎 223 条（`tests/` 6 个文件），示例项目 74 条，`self-test` 60 步。
- CI 4 个矩阵：`ubuntu-22.04/3.8`、`ubuntu-latest/3.12`、`windows-latest/3.8`、`windows-latest/3.12`
  （`.github/workflows/ci.yml:17-27`）。

## 四、文档声明 vs 代码核实

| 文档说法 | 代码核实 | 结论 |
| --- | --- | --- |
| 只有 `df.py` 写全局状态 | 状态类字段确由 CLI 写；但 `prompt.md`、`execution-report.md`、`test-report.json`、`confirmed-requirements-vN.md` 由主控/Agent 直接写 | 部分成立：**状态**由 CLI 独占，**证据/基准文档**不是 |
| 看板由 JSON 渲染 | `refresh()` 每次状态变更重渲染（`df.py:152`）；`board` 命令可重渲染 | 成立；但 Markdown 无哈希，手改不会被发现 |
| 提示词版本绑定 | `prompt_binding_problems` 比对 5 项（`dfcore.py:1258`） | 成立 |
| 上下文变更检测 | `context_changes`（`dfcore.py:1106`）只比对声明过的 `inputs` 与契约文件 | 成立，范围有意收窄 |
| accepted 终态 | `ACCEPTED_ALLOWED` + 守卫（`df.py:78`、`3550-3565`） | 成立；`init --force` 不走该守卫，但实测不会写 `project.json`（见下） |
| `init --force` 会保留已有文件 | 存在分支里只改内存对象，未调用 `save()`（`df.py:287-297`） | **文档/预期不符**：`--type/--profile` 在已有项目上不落盘 |
| 回归与交付绑定代码快照 | `source_snapshot` + 三处复核 | 成立 |
| 回归绑定需求快照 | 只比 `requirement_version` 数字（`df.py:2091` 起） | **未实现内容级绑定** |

## 五、架构层面的第一印象（供后续文档展开）

1. **强项**：单一 CLI 入口 + 显式状态机 + 大量"不可绕过"的硬校验，跨模块不变量比同类工具多。
2. **弱项**：不变量分散在 4 个文件里，**没有一份可执行的"不变量清单"**，导致每轮修 bug 都在补同一类缺口。
3. **结构性问题**：同一事实存在多份副本（`traceability.json` 与 `traceability-vN.json`、报告 md 与 json、
   看板 md 与 backlog），但只有部分副本有防漂移机制。
4. **权限边界**：状态由 CLI 独占，但**证据与需求基准文档**由 Agent 直接写，安全性依赖"写后校验"，
   而不是"写不进去"。

> 详细的数据关系见 `02-data-model.md`，状态机见 `03-lifecycle-and-state-machines.md`，
> 权限矩阵见 `04-permission-boundaries.md`。

## 六、审查修订补充（第二轮独立审查）

第一轮遗漏了"需求侧"的一条完整通道，已在 02/03/05/06 中补齐，这里给出索引：

- **`req-import` 没有项目状态门槛**（`df.py:399`）：除 `accepted` 外任何状态都能改写追踪表；
  配合 `delivery_blockers` 跳过非 active 需求（`df.py:2231-2232`），可在 `release_ready` 后缩小交付范围（A6）。
- **`req-confirm` 未排除 `planned`**（`df.py:434-437`）：会把项目状态回退到 `requirements_confirmed`
  并改写需求版本指针（A7）。
- **交付阶段不重校验需求基线**：`release-ready`/`release-accept` 只跑 `delivery_blockers` +
  `release_report_problems`，不调用 `validate_all`、不检查退役凭据（INV-029）。
- **事务只存在于 `change-apply`**：`plan-activate` 没有事务（修正 02 的 G1 描述）。

> 这三条不改变本文的模块划分，但改变了"权限边界"与"不变量"的结论，
> 以 `04`/`05`/`06` 的修订版为准。
