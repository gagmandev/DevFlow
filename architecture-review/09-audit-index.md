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
| ready 后改需求缩小交付范围 | `df.py:399`（无状态门槛）、`df.py:2231-2232`（跳过非 active）、`df.py:2565/2602`（交付不重校验） | **无** | 06 节 A6、05 节 INV-029 |
| 计划激活无唯一入口（v2 可 plan-activate） | `df.py:587-640`（无版本限制）、`df.py:522`（based_on 可空） | **无** | 08 D-01、07 C1、03 §3.5 |
| 需求变更无暂存目录 | 无 `changes/*/staging`；`df.py:423` 直写正式 | **无** | 08 D-02 §1-§5、03 §二 |
| `planned` 下 req-confirm 回退状态 | `df.py:434-437`、`df.py:466-467` | **无** | 06 节 A7、03 节 B10 |
| 退役凭据的实际调用点 | `validate.py:297`、`df.py:1931`（仅两处） | `RegressionCoverageTest` | 05 节 INV-005 |
| 事务实际使用点 | 仅 `df.py:1706`（`change-apply`） | `TransactionTest`（只测事务对象） | 02 节 §四 |
| 事务只保证写回滚、无读原子 | `dfcore.py:781`（Transaction 无 OPEN/锁概念）、读命令不取锁 | **无** | 08 D-02 §5b、05 INV-017、04 §5.6 |
| OPEN 检查后无需锁的结论已撤销 | 第四轮 §5b 的推论存在检查点↔读取点竞态窗口；第五轮改为读写互斥 | 本结论为本轮新增（静态推导） | 08 D-02 §5b |
| 读命令复用 ProjectLock 待代码审计 | 现锁为写命令设计（`dfcore.py` RLock+OS 锁）；`main()` 只对 MUTATING 加锁；超时拒绝路径、跨平台行为、读串行化未验证 | **无**（待实现） | 08 D-02 §5b 实现约束 |
| 命令读取路径分类（第六轮核对 parser） | parser 实际注册 **33** 个命令（`add("x", cmd_xxx)` 32 + `board` lambda）；`dependency_check` 是内部模块非 CLI（`df.py:3338`）；`self-test` 全程 `mkdtemp`（`df.py:2730`）隔离，单独成类；`change-stage` 归"读正式+写 staging"（待实现）；`doctor` 读正式+写图不在 MUTATING（`df.py:3328/3338`）；`init` 不在 MUTATING 且首次初始化锁待设计；`recover` 独立协议 | `test_p0_hardening.LockTest`（覆盖写锁） | 08 D-02 §5b 分类表、04 §5.6 |
| OPEN + journal 适用范围 | 仅 `change-apply` 用 Transaction（`df.py:1706`）；`plan-activate`(3 写)/`release`(4 写)/`req-import`(2 写)/`regression-import`/`accept` 持锁但无事务，崩溃留部分写入（02 §四 G2–G7） | `TransactionTest`（只测事务对象） | 08 D-02 §5b 三概念区分、05 INV-017 |
| `--by`/`confirmed_by` 无身份验证 | `--by` 直接入库（`change-set`/`release-accept` 等，CLI 无凭据核验） | **无** | 04 §5.5、08 D-02 §3 |
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
| "release_candidate = 具备候选资格" | `SKILL.md` 14 | `release` 在 blockers 非空时也置该状态（`df.py:2435`），语义混合；按 D-06 需拆分 |
| "证据可验收" | `SKILL.md` 10 | Agent 自写报告只能校验自洽；按 D-05 生产应以 CI 结果为准 |

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
| INV-024 | `df.py:2208/2484`（**部分**：不重校验需求基线） | `ReleaseGateTest`、`ReleaseStateTest` |
| INV-027 | 多处 | `EvidenceStrictTest`、`ReleaseGateTest` |
| INV-029 | **无强制点**（`release-ready/accept` 不调 `validate_all`、不查退役凭据） | **无** |
| INV-002/003/008/015/023 | **无强制点** | **无** |

> 判定方法：**不只看函数是否存在**，还要看它被哪些入口调用。完整调用矩阵见
> `05-system-invariants.md` 的 INV-005 / INV-017 / INV-024 / INV-029 行。

## 五、决策的唯一实现入口（目标核对）

| 决策 | 唯一实现入口（目标） | 当前竞争路径（必须关闭） | 关闭方式 |
| --- | --- | --- | --- |
| D-01 计划激活 | v1：`plan-activate`（仅当从未激活）；v2+：`change-apply` | `plan-activate` 可激活任意 draft（`df.py:587`）；`plan-import` 的 `based_on` 可空（`df.py:522`） | `plan-activate` 对 `version>1` 直接拒绝；`change-apply` 交叉校验 CR/版本/基准 |
| D-02 需求切换 | `change-stage`（读正式 + 写 staging，读取期间取锁）+ `change-apply`（Commit Set 事务切换 + OPEN 崩溃标记，当前唯一事务型写入） | `req-import` 直写正式（`df.py:423`）、无状态门槛（`df.py:399`）；读命令不取锁不查 OPEN | `req-import` 仅首次确认前可用；读写互斥覆盖全部读正式入口（08 D-02 §5b 分类表） |
| D-03/09 并行隔离 | `plan-activate`/`change-apply` 校验 `isolation` 声明 | 共享工作区并行（无 worktree 声明也放行） | production 档位拒绝未声明隔离的并行组 |
| D-04 ignored | `git_changes_since` + `release*` 门禁 | 现状完全不检测（`df.py` 无 `--ignored`） | production 默认 `deny` + allowlist（`path/purpose/approved_by`） |
| D-05 证据 | `regression-import`（CI 结果）+ `release*` 校验 | Agent 自写 `test-report.json` 直接作为证据 | 生产要求 CI 绑定 `commit_sha` + `baseline_id` |
| D-06 发布失败 | `release` 状态写入点 | blockers 非空仍置 `release_candidate`（`df.py:2435`） | 仅全部门禁满足才写状态 |

> 判定标准：每条决策都必须只有一个"写正式数据"的入口；其他路径要么只写草稿，要么直接拒绝。
> 只要存在第二个能写正式的入口，就视为未实现。

## 五、文档间引用关系

```
architecture-review/
  00-summary.md                ← 七问结论 + 修订记录
  01-current-architecture.md   ← 模块与实现现状
  02-data-model.md             ← 由 01 的"数据写入点"展开
  03-lifecycle-and-state-machines.md ← 由 02 的"版本关系"展开
  04-permission-boundaries.md  ← 由 01 的"谁写什么"展开
  05-system-invariants.md      ← 汇总 02/03/04 的约束，编号 INV-001..029
  06-cross-module-risks.md     ← 每条风险回指 INV 编号
  07-target-architecture.md    ← 每条建议回指 INV 编号
  08-decisions-required.md     ← 已定决策（D-01/02/03/05/06）+ 待定项 + 实施顺序
  09-audit-index.md            ← 本文件
```
