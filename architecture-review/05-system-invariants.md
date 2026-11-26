# 05 · 系统不变量（System Invariants）

> 判定口径：
> - **已实现**：有代码强制，且至少有 1 条自动化测试覆盖（给出测试名）。
> - **部分实现**：有代码强制但不完整，或只覆盖主路径，或只有事后检测没有阻止。
> - **未实现**：代码不检查，只靠文档/约定。
> - **无法确认**：代码存在但行为依赖外部环境，本阶段无法验证。
>
> 级别：P0 = 破坏即等于"门禁失效/数据不可信"；P1 = 会产出错误结论或历史丢失；P2 = 影响可维护性/可读性。
>
> 汇总：共 **28** 条 —— 已实现 **14**、部分 **9**、未实现 **5**。

## A. 事实来源与版本

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-001 | 项目/计划/节点状态只有一个事实来源：`backlog-vN.json` 与 `project.json`；看板与依赖图是派生文件，不得作为判断依据 | 计划、状态、看板 | `render_board` + `refresh()`（`df.py:152`）；`validate` 只读 JSON | `test_devflow.BoardTest`、`test_docs_consistency` | 部分 | 派生文件手改不会被发现；没有内容哈希 | P1 |
| INV-002 | 正式需求基准（`confirmed-requirements-vN.md`）只能通过变更流程修改；内容变更必须产生新版本 | 需求、CR、交付 | 无（`req-confirm` 只查存在/长度/关键词，`df.py:435-448`） | **缺失**：应加内容指纹 + 变更前后比对 | 未实现 | 直接改文件内容不改版本，回归/交付全部照样通过 | **P0** |
| INV-003 | 需求版本指针（`project.current_requirement_version`）必须与 `traceability.json.requirement_version` 及 `traceability-vN.json` 内容一致 | 需求、回归、交付 | 无（`validate` 只查文件存在，`validate.py:299-304`） | **缺失** | 未实现 | 指针漂移、同版本覆盖后无人发现 | P1 |

## B. 需求完整性

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-004 | 每条 active 需求必须有 ≥1 条非空验收标准（字符串非空白 / 对象 `criterion` 非空） | 需求导入、校验、回归 | `acceptance_spec_problems`（`dfcore.py:1898`）→ `req-import`/`validate`/`regression-check` | `test_v14_gates.RequirementSpecTest`（9 条） | 已实现 | 非 active 不受此约束（见 INV-005） | P0 |
| INV-005 | 非 active 需求必须有退役凭据：`deferred/out_of_scope/removed` 需 `reason`+`confirmed_by`；`changed` 需 `superseded_by` 指向 active 需求 | 需求、回归 | `requirement_retirement_problems`（`dfcore.py:1458`） | `test_p1_workflow.RegressionCoverageTest` | 已实现 | 只在 validate/回归时检查；req-import 不拦 | P1 |

## C. 变更合法性

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-006 | `change-apply` 的目标计划必须满足 `based_on.change_id == CR.change_id` 且 `based_on.plan_version == CR.base_plan_version` | CR、计划 | `df.py:1664-1689` | `test_v14_gates.ChangeBindingTest`（4 条） | 已实现 | 只约束 `change-apply` 通道 | P0 |
| INV-007 | CR 基准过期（base_plan/base_req ≠ 当前）必须拒绝，`base=0` 也参与比较 | CR | `df.py:1640-1660` | `test_p1_workflow.ChangeBaseVersionTest` | 已实现 | — | P0 |
| INV-008 | **任何计划版本变更都必须可追溯到 CR**（除首次激活 v1）；不能通过 `plan-import` + `plan-activate` 直接换计划 | 计划、CR | **无**：`plan-import` 的 `based_on` 可空（`df.py:522`），`plan-activate` 不校验来源（`df.py:587`） | **缺失**：应断言 "plan_version>1 且 based_on 为空 → 拒绝激活" | 未实现 | 绕过 CR 直接换计划、改 `files_scope`、加节点，无审计链 | **P0** |

## D. DAG 与并行

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-009 | 计划图必须无环、依赖节点存在、不依赖 superseded/cancelled、不自依赖、依赖不重复 | 计划 | `find_cycles`（`dfcore.py:1390`）+ `validate.py:437-460` | `test_devflow.GraphTest`、`test_p1_workflow.DependencyCheckParallelTest` | 已实现 | — | P0 |
| INV-010 | 同一并行组内不得有直接/间接依赖，且 `files_scope`/`db_objects`/`api_contracts`/`shared_config` 不得冲突（含通配符交集） | 并行 | `parallel_issues`（`dfcore.py:1629`）；`scope_overlap` 支持通配符（`dfcore.py:1575`）；例外必须点名 `nodes` | `test_p1_workflow.ParallelSafetyTest`、`test_v14_gates.WildcardScopeTest` | 部分 | 资源按字符串相等比较，不识别"同一张表的别名"；保守判冲突可能误报 | P1 |
| INV-011 | 执行 Agent 只能修改 `files_scope` 内文件（含新增/删除/重命名） | 执行、验收 | `scope_violations`（`dfcore.py:710`）+ `evaluate_node_changes`（`df.py:1148`） | `test_p1_workflow.GitScopeTest`、`test_v14_gates.AcceptLiveGitTest` | 部分 | 检测而非阻止；ignored 文件与 baseline dirty 文件存在盲区（见 INV-012） | P0 |

## E. 真实变更识别

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-012 | Git 实际变更必须参与范围校验，且 `accept` 必须以实时工作区重算，不能复用 `sync` 结论 | 同步、验收 | `evaluate_node_changes`（`df.py:1148`）；`accept` 调用点（`df.py:1359`） | `AcceptLiveGitTest`（7 条） | 部分 | ① baseline 就 dirty 的文件被整份剔除（`dfcore.py:993`）→ 期间再改无法归属；② ignored 文件不可见 | **P0** |
| INV-013 | 路径必须拒绝 `..`、绝对路径、符号链接逃逸、目录前缀逃逸 | 范围校验 | `normalize_rel`（`dfcore.py:618`）、`_symlink_escape`（`dfcore.py:686`） | `test_p0_hardening.ScopeTest`（含同名前缀兄弟目录） | 已实现 | Windows 大小写/盘符已处理；网络盘未验证 | P0 |

## F. 状态迁移

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-014 | 节点状态只能沿 `TRANSITIONS` 迁移，表外迁移必须拒绝 | 节点 | `TRANSITIONS`（`dfcore.py:61`）+ `transition()`（`df.py:159`） | `test_devflow.StateMachineTest` | 已实现 | 手改 JSON 可绕过（INV-001） | P0 |
| INV-015 | 项目状态只能沿合法生命周期推进，不得跳阶或回退（唯一回退入口是 `change-apply`） | 项目 | **无迁移表**；7 处直接赋值（`df.py:468/628/1080/1736/2006/2435/2594/2627`） | **缺失**：应枚举所有命令 × 状态 | 未实现 | `plan-activate` 可从 regression/release_candidate 回 planned；`release` 可从任意状态跳 candidate | **P0** |
| INV-016 | `accepted` 是终态：白名单外的写命令必须拒绝；只有 `change-apply` 能退出 | 全部命令 | `ACCEPTED_ALLOWED`（`df.py:78`）+ `accepted_denial`（`df.py:212`）+ `main()` 守卫 | `test_v14_gates.AcceptedGuardTest`（枚举全部命令） | 已实现 | `init --force` 不在白名单体系内（但不写 `project.json`，见 D-04） | P0 |

## G. 原子性与历史

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-017 | 多文件状态变更必须原子（全成或可回滚） | 计划生效、需求导入、回归导入、交付报告 | 仅 `change-apply` 用 `Transaction`（`dfcore.py:781`） | `test_p0_hardening.TransactionTest` | 部分 | `req-import`/`release`/`regression-import` 是多次独立原子写，中断留不一致 | P1 |
| INV-018 | 历史版本不得被静默覆盖（计划、提示词、节点报告、回归报告、交付报告） | 全部历史 | `plan-import --force` 归档（`df.py:501`）、prompt 归档（`df.py:948`）、报告归档（`df.py:996`/`1819`）、release md 归档（`df.py:2409`） | `test_p1_workflow.PromptBindingTest`、`RegressionJsonTest` | 部分 | `req-import` 同版本覆盖无守卫（`df.py:423`）；`release-report.json` 无归档 | P1 |

## H. 回归与交付

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-019 | 回归结论必须绑定执行时的**代码快照**；代码变化后旧结论失效 | 回归、交付 | `source_snapshot`（`dfcore.py:1183`）+ `snapshot_problems`（`df.py:1844`） | `test_v14_gates.SourceSnapshotTest`（3 条） | 已实现 | 非 Git 项目是文件树哈希，粒度到"任意文件变化"（含未跟踪产物） | P0 |
| INV-020 | 回归结论必须绑定**需求快照**；需求内容变化后旧结论失效 | 回归、交付 | 只比对 `requirement_version` 数字（`df.py:2091-2105`） | **缺失** | 部分 | 需求文档内容改了但版本不变 → 旧回归继续有效（与 INV-002 同源） | **P0** |
| INV-021 | 回归必需检查项由配置唯一决定；报告不得降级、缺项、重复、自创必需项 | 回归 | `derive_regression_status`（`df.py:2018`）+ `_regression_data_problems`（`df.py:2091`） | `test_p1_workflow.RegressionJsonTest`（14 条） | 已实现 | 可选检查项失败默认不阻断（配置可改） | P0 |
| INV-022 | 节点验收证据必须自洽：版本/`attempt_id` 绑定、命令退出码、统计一致、验收逐条对应 | 验收 | `evidence_problems`（`dfcore.py:2086`） | `test_p0_hardening.EvidenceStrictTest`、`RequirementEvidenceTest` | 已实现 | 只保证"报告内部一致"，不保证"真的执行过" | P0 |
| INV-023 | 证据必须真实反映执行（防伪造） | 验收 | **无**（Agent 直接写报告；CLI 只做自洽校验） | **无法自动验证** | 未实现 | 有动机的 Agent 可以编造 `test-report.json`；只能靠"提高伪造成本"（命令输出、快照） | P0 |
| INV-024 | 交付门禁不可绕过：全部节点完成 + 证据齐全 + 回归通过 + 需求覆盖 + 交付报告完整 + 快照一致 | 交付 | `delivery_blockers`（`df.py:2208`）+ `release_report_problems`（`df.py:2484`）+ `release-ready`（`df.py:2565`）+ `release-accept`（`df.py:2602`） | `test_v14_gates.ReleaseGateTest`（5 条）、`test_p0_hardening.ReleaseStateTest` | 已实现 | 旧项目兼容开关 `legacy_release_shortcut` 会跳过报告校验（有留痕） | P0 |

## I. 并发、恢复与默认安全

| ID | 规则 | 适用范围 | 强制执行位置 | 验证方法 | 当前实现 | 现存风险 | 级别 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-025 | 所有写状态的命令必须在项目锁内执行；并发写不得丢状态 | 全部写命令 | `MUTATING_COMMANDS`（`df.py:47`）+ `project_lock`（`dfcore.py:381`） | `test_p0_hardening.LockTest`（含线程 liveness）、`WriteCommandLockTest` | 部分 | `doctor` 写依赖图但不在锁集合（`df.py:3338`）；`init` 不加锁 | P1 |
| INV-026 | 中断后必须能恢复到一致状态 | 事务、写命令 | `Transaction` + `recover`（`df.py:2641`）+ `doctor` 检测未完成事务 | `test_p0_hardening.TransactionTest` | 部分 | 只有 change-apply 有 journal；其他多文件写中断无恢复协议 | P1 |
| INV-027 | 失败默认安全：缺证据 → failed/阻断；Git 不可用且配置要求 → 阻断；报告不完整 → 阻断 | 全部校验 | `evidence_problems`、`snapshot_problems`、`release_report_problems` | 覆盖在 Evidence/Release/Accept 各测试类 | 已实现 | 兼容路径（legacy）例外，但会留痕 | P0 |
| INV-028 | 最终验收必须由用户显式触发并记录验收人；系统不得自动置 `accepted` | 交付 | `release-accept --by`（`df.py:2602`）；无自动调用点 | `test_p0_hardening.ReleaseStateTest.test_release_ready_and_accept` | 已实现 | `self-test` 会自动跑到 accepted，但那是在临时目录（`df.py:3252`） | P0 |

## 汇总与优先级

| 状态 | 数量 | 编号 |
| --- | --- | --- |
| 已实现 | 14 | 004 005 006 007 009 013 014 016 019 021 022 024 027 028 |
| 部分实现 | 9 | 001 010 011 012 017 018 020 025 026 |
| 未实现 | 5 | 002 003 008 015 023 |

**必须先修的 5 条 P0（未实现/部分）**：

1. **INV-008**：计划变更绕过 CR（`plan-import` + `plan-activate` 无 based_on 要求）——可直接改范围、加节点。
2. **INV-002 / INV-020**：需求基准与回归的需求绑定都没有内容指纹——改需求文档不失效。
3. **INV-015**：项目状态无迁移表，`plan-activate`/`release` 可跳阶回退。
4. **INV-012**：Git 归属盲区（baseline dirty、ignored 文件）——交付质量证据可能漏掉越界改动。
5. **INV-023**：证据真实性无机制（只能靠自洽校验 + 提高伪造成本）。

> 每条不变量的"可测试化"建议见 `07-target-architecture.md` 第五节；
> 每条风险的复现细节见 `06-cross-module-risks.md`。
