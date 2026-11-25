# DevFlow v1.3 发布说明

> 从 V1 到 V1.3 是"同一个架构、更硬的约束"：没有新增业务功能，
> 目标是让每个任务**可追踪、可验证、可交付**，并且需求变化后能安全继续。

## 一、三轮做了什么

| 轮次 | 主题 | 关键结论 |
| --- | --- | --- |
| P0 | 证据 / 范围 / 并发 / 事务 / 交付状态 | 把"只打印警告"的约束变成硬失败 |
| P1 | 工作流正确性 | 需求覆盖、回归 JSON、Git 核对、并行冲突、版本绑定 |
| P2 | 架构与可维护性 | 文档分层、上下文变更检测、self-test、文档一致性守卫 |

详细缺陷编号与修复映射见 `docs/engineering-audit.md`。

## 二、新增 / 变化的命令

| 命令 | 说明 |
| --- | --- |
| `regression-import --file <json>` | 导入机器可读回归报告，校验后渲染 Markdown |
| `release-ready` / `release-accept --by` | 交付三段式的后两段（报告 → 就绪 → 验收） |
| `recover [--apply]` | 处理中断的 `change-apply` 事务 |
| `migrate` | 升级旧版 `project.json`（`released` → `accepted`，schema 版本） |
| `self-test [--fast] [--json] [--out] [--keep]` | 临时目录跑完整生命周期 + 单元 + 集成 |
| `accept --allow-scope / --ack-changes --reason` | 越界改动的显式豁免（留档） |
| `start --ack-context --reason` | 依赖产物变更后的显式确认（留档） |
| `sync --external --from <dir>` | 外部 Agent 报告导入通道 |
| `release --legacy-regression` | 旧项目用 Markdown 结论的降级开关 |

原有命令参数保持兼容；新增参数全部可选。

## 三、数据结构变化（schema 1.2 → 1.3）

全部是**可选字段**，旧项目不迁移也能跑；`df.py migrate` 只改 `schema_version` 和旧状态别名。

| 位置 | 新增 |
| --- | --- |
| `project.json` | `release_ready_at` / `accepted_at` / `accepted_by` / `legacy_regression_accepted` |
| `backlog-vN.json` | `parallel_exceptions`、节点 `current_attempt_id` / `attempt_number` / `prompt_version` / `scope_violations` / `scope_waivers` / `git_baseline` / `git_actual_changes` / `git_discrepancies` / `git_waivers` / `db_objects` / `api_contracts` / `shared_config` / `deliverables` / `context_acks` |
| 节点报告 | `plan_version` / `requirement_version` / `attempt_id` |
| `change-request.json` | `base_plan_version` / `base_requirement_version`（必填，可为 0）、`applied_at` |
| 新增文件 | `.devflow/regression/final-report.json`、`regression-report.schema.json` |

历史兼容策略：

- 无 `current_attempt_id` 的旧节点视为"历史节点"，只做通用校验，不追溯新规则
- 旧回归报告只有 Markdown 时用 `release --legacy-regression`，并在项目档案留痕
- 旧提示词没有版本元数据：`plan-activate` 前必须 `prompt-init --force` 重新生成
- 旧报告不删除：返工进 `nodes/<ID>/history/`，回归报告进 `regression/history/`

## 四、测试

```bash
python -m unittest discover -s tests -v                      # 引擎，142 条
cd examples/sample-project && python -m unittest discover -s tests   # 示例项目，74 条
python scripts/df.py self-test                               # 端到端 28 步 + 上面两层
```

`self-test` 在系统临时目录完成：init → 需求导入/确认 → 计划导入 → 校验 → 依赖图 →
激活 → 执行 → 证据同步 → 验收 → 变更 → 新计划 → 回归 → 交付 → 验收，
输出分层结果（单元 / 集成 / 端到端），任何一层失败退出码 1。

## 五、使用上的变化（主控必须知道）

1. `sync` 只接受 `in_progress`；`pending` / `ready` 必须先 `start`。
2. 证据不全不再是"警告后放行"，直接判 `failed`；不要"先标完成后面补"。
3. `regression-check` 有阻塞就退出码 1；把需求改非 active 需要 `reason` + `confirmed_by`，
   `changed` 需要指向 active 需求的 `superseded_by`。
4. 回归结论以 `final-report.json` 为准；Markdown 只是渲染结果。
5. 生成交付报告 ≠ 交付；`accepted` 只能由用户 `release-accept` 产生，且是终态。
6. 并行组要显式声明，冲突会被阻断；例外必须写 `parallel_exceptions`。
7. 计划/需求变了，旧提示词会被拒绝执行。
8. `accept` 会记录交付物指纹，下游 `start` 时比对；变了要么评估影响，要么 `--ack-context` 留档。

## 六、已知限制

- 契约级变更检测未做：目前是文件级指纹，`api_contracts` / `db_objects` 的版本化还在计划里。
- `self-test` 走串行 + 本地报告，未覆盖并行组与 `--external` 两条支线。
- 设计上只允许一个主控写全局状态（文件锁会阻止第二个进程）。
- Git 核对依赖本机 `git`；不在仓库内或项目不是仓库根时自动降级，不阻断。
