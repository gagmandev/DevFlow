# DevFlow 工程质量审计（V1 → V1.3）

- 审计时间：2026-11-25
- 审计范围：`SKILL.md`、`scripts/`、`schemas/`、`config/`、`templates/`、`references/`、`tests/`、`examples/sample-project/`
- 结论：架构成立（文件驱动 + 单写者 + 证据门槛），但**校验层偏软**——
  多个关键约束只打印警告、不改变状态，等于没有约束。
- 分轮次实施：第一轮 P0（证据 / 范围 / 锁 / 事务 / 交付状态机）；
  第二轮 P1（需求覆盖、回归 JSON、Git 核对、并行冲突、版本绑定）见第七节；
  第三轮 P2（SKILL 分层、上下文变更检测、self-test）见第八节。

## 一、当前架构

```
SKILL.md            主控：模式路由 + 关键安全规则（细则在 references/）
df.py               状态机 CLI（33 个命令）
dfcore.py           路径 / 原子 IO / 配置 / 状态机 / 证据校验
validate.py         Schema 子集校验 + 语义校验
render_board.py     JSON → Markdown 看板
dependency_check.py 环检测 / 分层 / 关键路径
schemas/            7 个 JSON Schema
```

数据流：`df.py` 命令 → 读 `project.json` + `backlog-vN.json` → 改内存 → `atomic_write` 落盘 →
重算 ready → 渲染看板。执行 Agent 只写 `nodes/<ID>/` 下的两份报告。

## 二、已确认的缺陷

| 编号 | 位置 | 缺陷 | 影响 |
| --- | --- | --- | --- |
| D-01 | `dfcore.evidence_problems` | 用 `all(exit_code != 0)` 判断命令失败，只有**全部**失败才报错 | 一条命令失败、另一条成功 → 判定通过 |
| D-02 | `dfcore.evidence_problems` | 缺 `exit_code` 时 `int(c.get("exit_code", 0))` 默认 0 | 没跑命令也能通过 |
| D-03 | 同上 | 不校验 `summary` 内部一致性（total/passed/failed/skipped） | 统计造假可过 |
| D-04 | 同上 | 不校验 `cases` 与 `summary` 一致 | `status=passed` 但有用例 fail 可过 |
| D-05 | 同上 | `total=0` 视为通过 | 空测试套件可验收 |
| D-06 | 同上 | 不校验执行报告的 `acceptance` 是否覆盖节点验收标准 | 验收标准没做也能过 |
| D-07 | 同上 | `result=failed` 不阻止验收（只拦了 `blocked`） | 失败报告 + passed 测试可过 |
| D-08 | 同上 | 报告与执行尝试无绑定 | 上一轮报告可冒充本轮 |
| D-09 | `df.py cmd_sync` | `out_of_scope_files` 只打印警告 | 越界修改照样能 accept |
| D-10 | `dfcore.out_of_scope_files` | 字符串前缀匹配 + 未处理 `..` / 绝对路径 / 符号链接 | `src/service` 放行 `src/service-backup/x.py` |
| D-11 | `dfcore.locked` | 按锁文件年龄判陈旧；释放时 `unlink` | 误删他人锁；崩溃后 30s 空窗；无归属校验 |
| D-12 | `df.py` 各命令 | 先 `ctx()` 读状态再加锁（且多数命令根本没用锁） | 并发丢失更新 |
| D-13 | `df.py cmd_change_apply` | 依次写 4 个文件，中途异常留下半提交 | 两个 active 计划 / project 指向不存在版本 |
| D-14 | `df.py cmd_release` | 生成报告即 `status=released` | 报告存在 ≠ 用户验收 |
| D-15 | `df.py cmd_release` | 只检查 Markdown 存在与长度 | 回归失败也能出报告 |
| D-16 | `df.py cmd_regression_check` | `uncovered` 只打印，不进退出码 | 需求未覆盖也能进回归 |
| D-17 | `df.py cmd_sync` | `ready` 状态可隐式转 `in_progress` 后同步 | 绕过依赖检查 |
| D-18 | `schemas/project.schema.json` | 状态枚举无交付细分 | 无法表达"报告已生成但未验收" |

## 三、修改涉及的文件

| 文件 | 改动 |
| --- | --- |
| `scripts/dfcore.py` | 新增 OS 级文件锁、路径规范化与 scope 匹配、严格证据校验、事务快照 |
| `scripts/df.py` | 所有写命令改为"加锁 → 重读 → 改"；sync/accept 严格化；change-apply 事务化；release 三段式 |
| `scripts/validate.py` | 传入 plan 上下文做版本绑定校验；scope 空值策略提示 |
| `schemas/project.schema.json` | 新增 `release_candidate` / `release_ready` / `accepted`，`released` 降级为兼容别名 |
| `schemas/node.schema.json` | 新增 `current_attempt_id` / `attempt_number` / `scope_violations` / `scope_waivers` |
| `schemas/test-report.schema.json` | 新增 `plan_version` / `requirement_version` / `attempt_id` |
| `config/quality-gates.yaml` | 新增 `require_no_skipped` / `require_acceptance_evidence` / `out_of_scope: error` |
| `tests/test_devflow.py` | 新增证据、scope、锁、事务、交付状态测试 |
| `docs/*`、`SKILL.md`、`references/release-rules.md` | 同步状态机与新命令 |

## 四、数据兼容风险

| 风险 | 处理 |
| --- | --- |
| 旧 `project.status = released` | 读取时映射为 `accepted`；`df.py migrate` 显式改写 |
| 旧节点无 `current_attempt_id` | 视为"历史节点"，跳过 attempt 绑定校验；新 `start` 才写入 |
| 旧 `test-report.json` 无 `attempt_id` | 仅对**新尝试**强制；已 completed 的历史节点不受影响 |
| 旧 `backlog-vN.json` 无新字段 | Schema 中全部为可选；`plan-import` 归一化时补默认值 |
| 旧回归 Markdown 报告 | `release` 仍接受 Markdown 作为降级依据，但要求包含结论段；第二轮起以 JSON 为准 |

## 五、修复方案（本轮 P0）

1. **证据**：重写 `evidence_problems`，逐项硬校验；新增 `attempt_id` 绑定。
2. **范围**：`normalize_rel` + `scope_match`（支持目录、精确文件、`*`/`**` 通配），
   拒绝绝对路径、`..`、符号链接逃逸；越界 → `scope_violations` → accept 阻断。
3. **锁**：`msvcrt.locking` / `fcntl.flock` 的 OS 级排他锁，进程退出自动释放；
   进程内可重入；锁文件保留不删除；写命令统一"加锁 → 重读 → 修改 → 落盘"。
4. **事务**：`change-apply` 走快照 + 日志 + 回滚；`df.py recover` 处理未完成事务。
5. **交付**：`release` → `release_candidate`；`release-ready` 检查条件；`release-accept` 才 `accepted`。

## 六、测试方案

| 层 | 覆盖 |
| --- | --- |
| 单元 | 证据 12 类反例、scope 8 类路径、状态机迁移、事务回滚 |
| 并发 | 8 个进程同时 start 不同节点，断言无丢失更新；锁占用超时报错 |
| 端到端 | 现有 `LifecycleTest` 扩展为 start → sync → accept → regression → release → release-ready → release-accept |
| 回归 | 引擎 37 条 + 示例项目 74 条必须继续全绿 |

## 七、第二轮：P1 工作流正确性

### 7.1 缺陷清单

| 编号 | 位置 | 缺陷 | 影响 |
| --- | --- | --- | --- |
| D-19 | `df.py cmd_regression_check` | `uncovered` 只打印，不进退出码 | 需求没覆盖也能进回归 |
| D-20 | `df.py cmd_regression_check` | 需求改 `deferred`/`inactive` 即可绕过覆盖 | 逃逸门 |
| D-21 | `df.py cmd_release` | 只看 Markdown 是否存在，没有机器可读回归结论 | 报告存在 ≠ 回归通过 |
| D-22 | `df.py cmd_sync` | `ready` 可隐式转 `in_progress`；外部报告无导入入口 | 绕过依赖/执行身份 |
| D-23 | 全流程 | 报告声称改了哪些文件，没人核对工作区实际变更 | 虚报改动无法发现 |
| D-24 | `df.py cmd_change_apply` | 不校验 `base_plan_version` / `base_requirement_version` | 过期影响分析被直接套用 |
| D-25 | `validate.py` 并行组 | 只查直接依赖与文件重叠，警告不阻断；把"同层"当安全 | 间接依赖 + 同表/同接口并行 |
| D-26 | `templates/node-prompt.md` | 提示词不绑定计划/需求版本 | 计划变了仍按旧提示词开发 |

另发现两处实现级缺陷（本轮修复）：

1. `change-apply` 的基准校验用 `if base_plan and ...`，`base_plan_version=0`
   （CR 创建时还没有计划）会因为 0 是 falsy 被跳过，等于给过期基准开后门。
   已改为 `is not None` 显式判断。
2. `regression-check` 不检查当前项目状态，在 `accepted` 项目上重跑会把状态改回
   `regression`（本次调试示例项目时实际触发）。已补 `accepted` 终态保护：
   节点类与交付类命令在 `accepted` 后全部拒绝，只能通过变更流程回到 `in_progress`。

### 7.2 修复对应

| 编号 | 修复 |
| --- | --- |
| P1-1 | `regression-check`：未完成/failed/blocked 节点、证据不齐、需求无覆盖或只有 `no_test` 覆盖、需求改非 active 缺 `reason`+`confirmed_by`、需求验收项无 `met=true` 证据 —— 全部计入 `blockers` 并退出码 1 |
| P1-2 | 新增 `schemas/regression-report.schema.json`、`templates/regression-report.json`；`regression-import` 做 Schema + 语义校验后写 `final-report.json` 并渲染 `.md`；`release` 读 JSON |
| P1-3 | `sync` 只接受 `in_progress`；新增 `--external --from <dir>` 导入通道，只搬文件 + 校验 `node_id`/`attempt_id`，之后走同一套证据/范围/依赖校验 |
| P1-4 | `core.git_snapshot` / `core.git_changes_since`：`start` 记录 HEAD + 已有脏文件，`sync` 求差集并写入 `git_actual_changes` / `git_discrepancies`；`verify_changes: warn/error/off` |
| P1-5 | `change-apply` 校验基准版本、目标计划归属、目标需求版本、迁移关系；全部不满足就拒绝 |
| P1-6 | `core.parallel_issues` 统一判定：直接/间接依赖、`files_scope`、`db_objects`、`api_contracts`、`shared_config`；`validate` 阻断，`dependency_check`/`graph` 同步报告；例外需 `parallel_exceptions`（group + reason + approved_by） |
| P1-7 | 提示词头部写 `project_id`/`node_id`/`plan_version`/`requirement_version`/`prompt_version`；`start` 校验，`validate --activating` 也校验 |

### 7.3 修改涉及的文件

| 文件 | 改动 |
| --- | --- |
| `scripts/df.py` | 回归前置检查硬阻断、`regression-import`、`sync --external`、Git 核对、CR 基准校验、提示词版本绑定、`accepted` 终态守卫、旧报告/旧提示词归档 |
| `scripts/dfcore.py` | `git_snapshot`/`git_changes_since`、`parse_prompt_meta`/`prompt_binding_problems`、`migration_problems`、`requirement_retirement_problems`、`parallel_issues` 及路径/资源冲突辅助函数 |
| `scripts/validate.py` | `activating` 模式、提示词版本校验、并行冲突改为错误、迁移关系校验 |
| `scripts/dependency_check.py` | 并入并行冲突判定（与 validate 同源），分层说明改为"仅候选" |
| `schemas/*.json` | `node` 增 `prompt_version`/`git_*`/`db_objects` 等；`backlog` 增 `parallel_exceptions`；新增 `regression-report` |
| `config/quality-gates.yaml` | `regression.required_checks`、`git.verify_changes` |
| `templates/` | 新增 `regression-report.json` |
| `tests/test_p1_workflow.py` | P1 全部场景（新增 34 条） |

### 7.4 数据兼容

| 风险 | 处理 |
| --- | --- |
| 旧节点没有提示词元数据 | `plan-activate` 前必须 `prompt-init --force` 重新生成；已 completed 的节点不校验 |
| 旧项目只有 Markdown 回归报告 | `release --legacy-regression` 降级，并在 `project.json` 记 `legacy_regression_accepted` |
| 旧节点没有 `git_baseline` | `git_changes_since` 返回 `None`，跳过核对（不是失败） |
| 旧节点无 `current_attempt_id` | 需求验收证据检查跳过（不追溯旧项目） |
| 旧计划无 `parallel_exceptions` | 字段可选；没有例外时冲突直接报错 |

### 7.5 测试

| 层 | 覆盖 |
| --- | --- |
| 单元 | 需求覆盖 5 类、回归报告 6 类、外部报告 3 类、Git 2 类、并行 4+4 类、提示词 2 类、迁移 4 类、CR 基准 5 类 |
| 端到端 | 引擎 117 条 + 示例项目 74 条全绿 |
| 手工 | `df.py graph` / `df.py doctor` 在示例项目上通过 |

## 八、第三轮：P2 架构与可维护性

### 8.1 做了什么

| 项 | 实现 |
| --- | --- |
| P2-1 SKILL.md 分层 | 第 2 节新增「按需读取」路由表；第 7 节的方法论（五阶段、八字段、粒度、优先级）收敛为三条红线 + 指向 `references/task-decomposition.md` / `dependency-management.md`；文件索引去重；硬规则补到 15 条 |
| P2-2 上下文变更检测 | `accept` 记录 `deliverables`（`outputs`/`files_scope` 里真实存在的文件：小文件 sha256、大文件 size+mtime，附 git HEAD）。`start` 比对直接依赖记录、且在本节点 `inputs` 里声明的文件；变了就拒绝，`--ack-context --reason` 留档到 `context_acks` |
| P2-3 自动化验收 | 新增 `df.py self-test`：临时目录从 `init` 跑到 `release-accept`（28 步），不读写真实项目；`--fast` 只跑端到端，`--json/--out` 输出机器可读结果，`--keep` 保留现场；分层报告单元（引擎）/ 集成（示例项目）/ 端到端，任一失败退出码 1 |

### 8.2 设计取舍

- **只对声明的依赖做判断**：`inputs` 为空就不推断，仓库里改个文档不该让全部下游返工。
  这比"全工作区 diff 一有变化就失效"更符合实际，也更不容易被当成噪音关掉。
- **不新引依赖**：指纹只算 sha256（标准库），大文件退化到 size+mtime，避免给二进制算哈希。
- **self-test 防递归**：分层子进程带 `DEVFLOW_SELF_TEST_LAYERS=1`，测试里再调 self-test 不会无限套娃。

### 8.3 顺带修掉的缺陷

`change.schema.json` 缺 `applied_at` 且 `additionalProperties: false`，而 `change-apply`
会写这个字段 —— 任何真正走过变更的项目，`validate` / `doctor` 都会报错。
self-test 第一次跑就撞上了。已补字段，并把 `base_plan_version` 的最小值放宽到 0
（提出变更时项目可能还没有生效计划）。

### 8.4 测试

| 层 | 数量 |
| --- | --- |
| 引擎单元 / 集成 | 132 条全绿 |
| 示例项目 | 74 条全绿 |
| 端到端（`self-test`） | 28 步 → accepted |

## 九、尚未处理

- 契约级变更检测（`api_contracts` / `db_objects` 的版本化指纹，目前只做文件级）。
- `self-test` 覆盖并行组与外部报告导入两条支线（目前走串行 + 本地报告）。
- 多主控协同（当前设计明确只允许一个主控写全局状态）。
