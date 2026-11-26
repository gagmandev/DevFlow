# 09 · 审计索引

> 用法：任何一条结论，都能顺着这张表找到代码、测试、文档三处依据。
> 行号基于 `a227e71`；函数名比行号更稳定，重构后请以函数名为准。

## 一、结论 → 代码 / 测试 / 文档

| 结论 | 代码位置 | 测试位置 | 文档位置 |
| --- | --- | --- | --- |
| 33 个 CLI 命令、写命令集合 | `df.py:47` `MUTATING_COMMANDS`、`df.py:3389 build_parser`、`3399+ add(...)` | `test_docs_consistency.py:27 CommandDocsTest` | `SKILL.md` 第 2/16 节 |
| accepted 白名单 | `df.py:78 ACCEPTED_ALLOWED`、`df.py:212 accepted_denial()` | `test_v14_gates.py:701 test_every_writer_is_denied_by_default` | `SKILL.md` 第 14.3 节 |
| project-set 字段白名单 | `df.py:92/98`、`df.py:330` | `test_v14_gates.py:790 ProjectSetGuardTest` | `SKILL.md` "项目档案只能改资料性字段" |
| 节点状态迁移表 | `dfcore.py:61 TRANSITIONS`、`df.py:159 transition()` | `test_devflow.py:323 StateMachineTest` | `docs/data-structure.md` §四 |
| 项目状态无迁移表（8 处赋值） | `df.py:468/628/1080/1736/2006/2435/2594/2627` | **无** | 本文档 03 节 3.2 |
| 需求验收标准强制 | `dfcore.py:1898 acceptance_spec_problems`、`df.py:399`、`validate.py:296`、`df.py:1900+` | `test_v14_gates.py:850 RequirementSpecTest` | `SKILL.md` 第 4 节 |
| 非 active 退役凭据 | `dfcore.py:1458 requirement_retirement_problems` | `test_p1_workflow.py:68 RegressionCoverageTest` | `references/requirement-analysis.md` |
| CR 与计划严格绑定 | `df.py:1664-1689` | `test_v14_gates.py:288 ChangeBindingTest` | `references/change-management.md` |
| CR 基准过期拒绝 | `df.py:1640-1660` | `test_p1_workflow.py:856 ChangeBaseVersionTest` | 同上 |
| 无 CR 换计划的缺口 | `df.py:522`、`validate.py:516-523`、`df.py:587` | **无**（本次新增结论） | 本文档 03 节 B2 |
| DAG 检环/依赖校验 | `dfcore.py:1390 find_cycles`、`validate.py:437-460` | `test_devflow.py:243 GraphTest` | `references/task-decomposition.md` |
| 并行冲突判定（含通配符） | `dfcore.py:1629 parallel_issues`、`dfcore.py:1575 scope_overlap` | `test_v14_gates.py:200 WildcardScopeTest`、`test_p1_workflow.py:629` | `references/dependency-management.md` |
| 并行例外需点名节点 | `dfcore.py:1620 _exception_covers`、`schemas/backlog.schema.json` | `test_v14_gates.py`（组级豁免被拒） | `SKILL.md` 7.1 |
| 路径规范化/符号链接 | `dfcore.py:618 normalize_rel`、`dfcore.py:686 _symlink_escape`、`dfcore.py:710 scope_violations` | `test_p0_hardening.py:284 ScopeTest` | `references/execution-rules.md` |
| Git 快照与差集 | `dfcore.py:952 git_snapshot`、`dfcore.py:970 git_changes_since` | `test_p1_workflow.py:450 GitVerificationTest` | `docs/data-structure.md` |
| accept 实时复算 | `df.py:1148 evaluate_node_changes`、`df.py:1359` | `test_v14_gates.py:571 AcceptLiveGitTest`（7 条） | `SKILL.md` "Git 变更核对" |
| baseline dirty 整份剔除 | `dfcore.py:993-999` | **无**（本次新增结论） | 本文档 06 节 B1 |
| ignored 文件不可见 | `dfcore.py:961`（未用 `--ignored`） | **无** | 本文档 06 节 B2 |
| 证据自洽校验 | `dfcore.py:2086 evidence_problems` | `test_p0_hardening.py:161 EvidenceStrictTest` | `references/testing-rules.md` |
| 证据真实性无机制 | 无（Agent 直写报告） | **无法自动测试** | 本文档 06 节 D5 |
| 需求逐条验收证据 | `dfcore.py:1943 requirement_acceptance_problems` | `test_v14_gates.py:412 RequirementEvidenceTest` | `templates/execution-report.md` |
| 回归必需项不可降级 | `df.py:2018 derive_regression_status`、`df.py:2091` | `test_p1_workflow.py:222 RegressionJsonTest`（14 条） | `references/release-rules.md` |
| 回归绑定代码快照 | `dfcore.py:1183 source_snapshot`、`df.py:1844 snapshot_problems`、`df.py:1784` | `test_v14_gates.py:362 SourceSnapshotTest` | `docs/data-structure.md` |
| 回归只绑需求版本号 | `df.py:2100-2105` | **无**（本次新增结论） | 本文档 05 节 INV-020 |
| 交付门禁（节点/回归/需求） | `df.py:2208 delivery_blockers` | `test_p0_hardening.py:651 ReleaseStateTest` | `docs/workflow.md` |
| 交付报告完整性 | `df.py:2484 release_report_problems` | `test_v14_gates.py:107 ReleaseGateTest` | `references/release-rules.md` |
| release-ready/accept 同源门禁 | `df.py:2565`、`df.py:2602`（都调 `delivery_gate_problems`） | 同上 | 同上 |
| release 失败仍改状态 | `df.py:2435` | **无** | 本文档 06 节 D2 |
| 事务（仅 change-apply） | `dfcore.py:781 Transaction`、`df.py:1706` | `test_p0_hardening.py:549 TransactionTest` | `docs/architecture.md` |
| 锁（RLock + OS 锁） | `dfcore.py:357-410` | `test_p0_hardening.py:391 LockTest`、`:472 liveness` | `docs/architecture.md` |
| doctor 写图未加锁 | `df.py:3338`、`df.py:47` | **无** | 本文档 06 节 C3 |
| 迁移 | `df.py:3307 cmd_migrate`、`dfcore.py:903 migrate_project_data` | `test_v14_gates.py:521 LegacyMigrationTest` | `docs/usage.md` 七·六 |
| self-test 三层 | `df.py:3252 cmd_self_test`、`df.py:2560+`（并行/外部场景） | `test_p2_optimization.py:106` | `SKILL.md` 12.5 |
| 示例项目状态 | `examples/sample-project/.devflow/project.json`（schema 1.1/admitted） | 示例测试不覆盖交付门禁 | 本文档 06 节 F1 |
| CI 矩阵 | `.github/workflows/ci.yml:17-27` | `test_docs_consistency.py` `test_ci_pins_python38_runner` | `docs/release-notes-v1.4.md` |

## 二、文档声明但代码未证实

| 声明 | 出处 | 代码事实 |
| --- | --- | --- |
| `analyzing` / `clarifying` 项目状态 | `dfcore.py:31-32`、`SKILL.md` 模式路由 | 无任何命令写入这两个状态（`grep '"analyzing"'` 仅常量） |
| `.devflow/sessions/` 存会话记录 | `Paths.sessions`（`dfcore.py:555`）、`init` 建目录 | 全仓库无写入点 |
| `init --force` 会更新已有项目档案 | `df.py:239` 的提示语 | 已存在分支只改内存对象，未 `save()`（`df.py:287-297`） |
| 需求基准文档受保护 | `SKILL.md` "没确认的需求不拆任务" | 仅检查存在/长度/关键词，无内容校验 |
| 证据真实 | `SKILL.md` "实际跑测试并留下证据" | 只能校验自洽；无法证明执行 |
| `rejected` CR 有原因 | `schemas/change.schema.json` 允许 `rejected` | 无 reason/confirmed_by 要求 |
| 并行安全 | `SKILL.md` 7.1 | `parallel_issues` 已实现，但"多 Agent 共享工作区"时变更归属不可靠 |

## 三、代码存在但缺乏有效测试

| 功能 | 代码 | 测试情况 |
| --- | --- | --- |
| `block` 命令 | `df.py:1448` | 无 CLI 级测试（仅状态表断言） |
| `cancel` 命令 | `df.py:1489` | **无** |
| `supersede` 命令 | `df.py:1501` | **无** |
| `board` 命令 | `df.py:3488`（lambda → `render_board.main`） | 无 CLI 级测试（只测渲染函数 `BoardTest`） |
| `doctor` 写依赖图 | `df.py:3338` | 无并发/锁测试 |
| `init --force` 行为 | `df.py:239` | 无 |
| `recover --apply` 完整回滚 | `df.py:2641` | 只有 `Transaction.rollback` 单测，未测 CLI 恢复 |
| `release --legacy-regression` | `df.py:2248` | 无 |
| `migrate --legacy-release` | `df.py:3307` | 有字段断言，无"跳过后交付"端到端 |
| ignored 文件越界 | `dfcore.py:970` | 无 |
| baseline dirty 再修改 | `dfcore.py:993` | 无 |

## 四、状态机与不变量索引

| 不变量 | 代码强制点 | 测试 |
| --- | --- | --- |
| INV-004 | `dfcore.py:1898` | `RequirementSpecTest` |
| INV-006 | `df.py:1664` | `ChangeBindingTest` |
| INV-009 | `dfcore.py:1390` | `GraphTest` |
| INV-013 | `dfcore.py:686` | `ScopeTest` |
| INV-014 | `dfcore.py:61` | `StateMachineTest` |
| INV-016 | `df.py:78` | `AcceptedGuardTest` |
| INV-019 | `dfcore.py:1183` | `SourceSnapshotTest` |
| INV-021 | `df.py:2018` | `RegressionJsonTest` |
| INV-024 | `df.py:2208/2484` | `ReleaseGateTest` |
| INV-027 | 多处 | `EvidenceStrictTest`、`ReleaseGateTest` |
| INV-002/003/008/015/023 | **无强制点** | **无** |

## 五、文档间引用关系

```
architecture-review/
  01-current-architecture.md   ← 模块与实现现状
  02-data-model.md             ← 由 01 的"数据写入点"展开
  03-lifecycle-and-state-machines.md ← 由 02 的"版本关系"展开
  04-permission-boundaries.md  ← 由 01 的"谁写什么"展开
  05-system-invariants.md      ← 汇总 02/03/04 的约束，编号 INV-001..028
  06-cross-module-risks.md     ← 每条风险回指 INV 编号
  07-target-architecture.md    ← 每条建议回指 INV 编号
  08-decisions-required.md     ← 阻塞项与 07 的实施顺序对应
  09-audit-index.md            ← 本文件
```
