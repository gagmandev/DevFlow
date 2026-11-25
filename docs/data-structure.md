# 数据结构说明

## 一、目录总览

```
<project>/
└── .devflow/
    ├── project.json                    项目档案（唯一）
    ├── decisions.md                    决策记录
    ├── requirements/
    │   ├── source/                     原始需求副本（只读）
    │   ├── clarification.md            澄清问答
    │   ├── confirmed-requirements-vN.md 需求基准（按版本）
    │   ├── traceability-vN.json        需求追踪（按版本，保留历史）
    │   └── traceability.json           当前版本的副本
    ├── plans/
    │   ├── backlog-vN.json             计划（按版本，只增不改）
    │   ├── board-vN.md                 看板（由 JSON 渲染）
    │   ├── dependency-graph-vN.md      依赖图
    │   └── .history/                   被 --force 覆盖前的备份
    ├── nodes/
    │   └── DF-001/
    │       ├── prompt.md               节点提示词（冷启动可执行）
    │       ├── context.md              可选：额外上下文
    │       ├── execution-report.md     执行报告（含证据块）
    │       ├── test-report.json        测试证据（机器读）
    │       ├── test-report.md          可选：人读明细
    │       └── history/                返工前的旧报告
    ├── changes/
    │   └── CR-001/
    │       ├── change-request.md       变更说明（人读）
    │       ├── change-request.json     变更记录（机器读）
    │       └── impact-assessment.md    影响评估
    ├── regression/
    │   ├── precheck.md                 回归前置检查
    │   └── final-report.md             回归报告
    ├── releases/
    │   └── release-report.md           交付报告
    └── .txn/
        └── <txn_id>/
            ├── journal.json            事务日志（open/committed/rolled_back）
            └── files/                  提交前快照（按相对路径保存）
```

## 二、project.json

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | `"1.0"` |
| `project_id` | string | 小写字母数字与 `-`，用于跨文件关联 |
| `project_name` | string | 展示名 |
| `project_type` | enum | `greenfield` / `existing` / `refactor` |
| `status` | enum | `initialized` → `analyzing` → `clarifying` → `requirements_confirmed` → `planned` → `in_progress` → `regression` → `release_candidate` → `release_ready` → `accepted`；`released` 是旧版别名，迁移为 `accepted` |
| `current_plan_version` | int/null | 生效计划版本；空表示还没有 |
| `current_requirement_version` | int/null | 生效需求基准版本 |
| `tech_stack` | object | 语言、框架、存储、测试框架 |
| `commands` | object | `install` / `build` / `test` / `lint` / `run`，**必须可直接执行** |
| `constraints` | string[] | 硬约束（禁止的依赖、必须遵循的规范） |
| `conventions` | object | 命名、分层、错误处理约定 |
| `analysis` | object | 环境分析结论 |
| `notes` | string | 项目目标与范围 |

## 三、backlog-vN.json

| 字段 | 说明 |
| --- | --- |
| `plan_version` | 与文件名一致 |
| `requirement_version` | 基于哪一版需求基准 |
| `status` | `draft`（待审核）/ `active`（生效）/ `superseded` / `rejected` |
| `approved_at` / `approved_by` | 谁在什么时候批准 |
| `based_on` | `{plan_version, change_id}`，变更产生的新版计划才有 |
| `nodes` | 节点数组 |

### 节点字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `node_id` | ✓ | `DF-001` 格式，三位起 |
| `node_name` | ✓ | 一句话说清交付什么 |
| `phase` | ✓ | Foundation / Shared / Business / Integration / Release |
| `type` | ✓ | infrastructure / shared / business / integration / release |
| `priority` | ✓ | P0–P3 |
| `description` | ✓ | 目标，不要写"优化"这种不可验收的 |
| `requirement_ids` | ✓ | 关联的 REQ 编号，基础设施可为空 |
| `dependencies` | ✓ | 前置节点 ID，必须全部 completed |
| `parallelizable` | ✓ | 是否可并行 |
| `parallel_group` | | 组名，如 `A`；同组必须无依赖、文件不重叠 |
| `acceptance_criteria` | ✓ | 至少 1 条，可判断真假 |
| `inputs` | ✓ | 必读文件 |
| `outputs` | ✓ | 交付物 |
| `files_scope` | ✓ | 允许修改的范围（并发安全的核心） |
| `test_strategy` | ✓ | 要覆盖的场景 |
| `no_test` / `no_test_reason` | | 纯文档节点用，必须给理由 |
| `status` | ✓ | 见状态机 |
| `prompt_file` | ✓ | 默认 `.devflow/nodes/<ID>/prompt.md` |
| `execution_report` / `test_report` | | 为空时按约定路径推导 |
| `owner` / `attempts` | | 谁在做、尝试了几次 |
| `current_attempt_id` | | 本轮执行标识；报告必须回填，防止旧报告冒充 |
| `attempt_number` | | 与 `attempts` 同步，便于人读 |
| `scope_violations` | | 最近一次同步发现的越界文件；非空禁止验收 |
| `scope_waivers` | | 主控确认放行的记录（含理由、时间、人） |
| `prompt_version` | | 提示词版本；每次 `prompt-init` 递增 |
| `git_baseline` | | `start` 时的 HEAD 与已有未提交文件 |
| `git_actual_changes` | | `sync` 检测到的实际变更（已排除执行前的修改） |
| `git_discrepancies` | | 实际变更与 `modified_files` 的差异 |
| `git_waivers` | | 确认 Git 差异的记录 |
| `db_objects` / `api_contracts` / `shared_config` | | 并行冲突检测用；同组不允许交集 |
| `deliverables` | | `accept` 时记录的交付物指纹（sha256 或 size+mtime）、契约文件指纹与当时的 git HEAD |
| `contract_bindings` | | `api_contracts` / `db_objects` 对应的可核查文件（`{kind, id, files}`） |
| `scope_baseline` | | `start` 时已存在的交付物；空 `files_scope` 时判断哪些是本轮新增文件 |
| `git_unavailable` | | 本次同步无法计算 Git 变更（非仓库/非仓库根） |
| `context_acks` | | 依赖产物变更后，主控确认「不影响本节点」的记录 |
| `started_at` / `completed_at` / `accepted_by` | | 时间线 |
| `superseded_by` | | 被哪个节点取代 |
| `blocked_reason` | | 阻塞原因，`blocked` 必填 |
| `remark` | | 备注；返工、reusable 判定理由写这里 |

## 四、节点状态机

| 状态 | 含义 | 可迁移到 |
| --- | --- | --- |
| `pending` | 待执行，依赖未满足 | ready, blocked, cancelled, superseded |
| `ready` | 依赖已满足 | in_progress, pending, blocked, cancelled, superseded |
| `in_progress` | 执行中 | needs_review, failed, blocked, cancelled, ready |
| `needs_review` | 待验收 | completed, in_progress, failed, blocked |
| `completed` | 已验收 | superseded |
| `failed` | 执行失败 | in_progress, blocked, cancelled, superseded |
| `blocked` | 阻塞 | ready, in_progress, failed, cancelled, superseded |
| `superseded` | 被新版取代 | — |
| `cancelled` | 已取消 | — |

自动行为：任何状态变更后，依赖全部 completed 的 `pending` 节点自动变 `ready`。
**不会**自动把 `ready` 退回 `pending`。

## 五、test-report.json

```json
{
  "node_id": "DF-004",
  "status": "passed",
  "executed_at": "2026-11-25T13:53:00+08:00",
  "executed_by": "code-agent",
  "attempt_id": "a1b2c3d4e5f6",
  "plan_version": 1,
  "requirement_version": 1,
  "commands": [{"cmd": "python -m unittest ...", "exit_code": 0, "output_file": null}],
  "summary": {"total": 17, "passed": 17, "failed": 0, "skipped": 0},
  "coverage": {"tool": "coverage.py", "line": 0.82, "branch": null},
  "cases": [{"name": "test_x", "result": "pass", "detail": "..."}],
  "not_run_reason": null,
  "notes": ""
}
```

验收要求（`strict` = 节点有 `current_attempt_id`）：

- 通用：`status=passed`、`commands` 非空、**每条**命令退出码为 0、`summary.failed=0`、`total>0`
- `total = passed + failed + skipped`，否则判失败
- strict：`cases` 数量与 `summary` 一致、`skipped=0`、`attempt_id`/版本号与节点一致
- 历史报告（无 `attempt_id`）只做通用校验，避免升级后旧报告集体失效

## 六、回归报告（regression/final-report.json）

```json
{
  "schema_version": "1.0",
  "plan_version": 2,
  "requirement_version": 2,
  "status": "passed",
  "executed_at": "2026-11-25T15:00:00+08:00",
  "executed_by": "code-agent",
  "checks": [
    {
      "check_id": "unit_test",
      "name": "单元测试",
      "required": true,
      "status": "passed",
      "command": "python -m unittest discover -s tests",
      "exit_code": 0,
      "executed_at": "2026-11-25T15:01:00+08:00",
      "evidence": "Ran 74 tests / OK",
      "notes": ""
    }
  ],
  "notes": ""
}
```

- `status` 必须等于检查项推导结果（`derive_regression_status`）
- 必需项（`required=true`）任一 `failed` → 整体 `failed`
- 必需项任一 `not_run`/`skipped` → 整体不能是 `passed`
- `passed` 项必须有命令、`exit_code=0`、证据
- `not_run`/`skipped` 必须写 `notes`
- `config/quality-gates.yaml` 的 `required_checks` 决定哪些 check_id 必须存在

Markdown 由 `regression-import` 从 JSON 渲染，不手工维护。

## 七、不变量

这些必须永远成立，`validate.py` 会检查：

1. 同一时间只有一个 `active` 计划，且等于 `project.current_plan_version`
2. 计划里 `node_id` 唯一
3. 依赖指向的节点存在，且不成环
4. `ready` / `in_progress` / `needs_review` 的节点，依赖全部 completed
5. `completed` 节点有执行报告 + 测试证据（或 `no_test` + 理由）
6. `parallel_group` 内成员之间没有依赖
7. 节点引用的 `REQ-xxx` 在追踪表里存在
8. `plan_version` 与文件名一致，`project_id` 与项目档案一致
9. `needs_review` / `completed` 的节点，`scope_violations` 必须为空
10. 节点有 `current_attempt_id` 时，两份报告的 `attempt_id` 必须与之一致
11. 不存在未完成事务（`.txn/*/journal.json` 的 `status` 只能是 `committed` / `rolled_back`）
12. `project.status = accepted` 只能由 `release-accept` 产生，且此前必须是 `release_ready`
13. 未完成节点的提示词元数据必须与 `project_id` / `plan_version` / `requirement_version` 一致
14. 同一 `parallel_group` 内不存在依赖、文件重叠或 `db_objects`/`api_contracts`/`shared_config` 交集（除非有 `parallel_exceptions`）
15. `release_ready` 之前 `regression/final-report.json` 必须存在且推导为 `passed`
16. `accepted` 之后节点类与交付类命令全部拒绝执行；状态只能由变更流程（`change-apply`）回到 `in_progress`
17. 非 `active` 需求必须留凭据：`deferred`/`out_of_scope`/`removed` 要有 `reason` + `confirmed_by`，`changed` 要有指向 active 需求的 `superseded_by`
18. `change-apply` 前 `base_plan_version` / `base_requirement_version` 必须等于当前版本（`0` 也算有效基准）
19. 节点的 `inputs` 若被某个直接依赖记录为交付物，`start` 前指纹必须未变（否则需 `context_acks` 留档）
20. 回归报告必须带 `source_snapshot`，且与当前代码一致（否则结论失效）
21. `release-report.md` + `release-report.json` 必须存在且版本与当前计划一致，`release_ready` 才可能通过
22. 回归报告里的必需检查项由 `config/quality-gates.yaml` 决定，报告不能降级或新增必需项

## 八、文件命名约定

| 类型 | 规则 |
| --- | --- |
| 计划 / 看板 / 依赖图 | `backlog-vN.json` / `board-vN.md` / `dependency-graph-vN.md` |
| 需求 | `confirmed-requirements-vN.md` / `traceability-vN.json` |
| 节点 | `DF-001` 目录，ID 不复用 |
| 变更 | `CR-001` 目录，ID 不复用 |
| 归档 | `history/<原名>.<时间戳>.<扩展名>` |

## 九、Git 建议

提交：`.devflow/` 下除锁文件外的全部内容。
忽略：`.devflow/.lock`、`.devflow/**/*.tmp`（`init` 会自动写进 `.gitignore`）。

计划变更、需求变更、报告更新都能 diff，这是 DevFlow 最有价值的部分之一。
