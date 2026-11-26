# 00 · 总结（回答七个问题）

> 阶段：架构审查与设计规则冻结。本阶段**未修改任何代码/测试/配置/JSON 状态**，
> 只新增 `architecture-review/` 下的 Markdown。
> 基线：`a227e71473def3be1c53453e3c599a7f2f3a3140`。

## 1. DevFlow 当前实际架构是什么？

**一个单进程、文件驱动、CLI 独占状态写入的状态机**，四个执行层：

```
主控 Agent（决策） → df.py（33 命令 + 门禁 + 锁 + 事务） → .devflow/（JSON 事实 + Markdown 展示）
                                   ↘ 执行 Agent（按 prompt 改业务代码 + 写两份报告）
```

- 状态：`project.json`（生命周期） + `backlog-vN.json`（计划/节点/DAG） + 节点报告（证据）。
- 校验：7 个 JSON Schema + 语义校验（`validate.py`、`dfcore` 的 `*_problems` 系列）。
- 并发：OS 文件锁 + 线程 `RLock`；事务只覆盖 `change-apply`。
- 交付：`regression`（机器可读报告 + 源码快照）→ `release_candidate` → `release_ready` → `accepted`。
- 规模：引擎 6916 行、测试 4489 行（223 + 74 条）、self-test 60 步、CI 4 矩阵。

## 2. 最严重的架构级问题是什么？

**"规则分散 + 关键事实无指纹"** 导致的两类绕过：

| # | 问题 | 证据 |
| --- | --- | --- |
| 1 | **计划可以不带 CR 直接换**：`plan-import` 的 `based_on` 可空，`plan-activate` 不校验来源与状态，等于绕过变更管理 | `df.py:522`、`validate.py:516-523`、`df.py:587` |
| 2 | **需求基准/回归没有内容指纹**：改 `confirmed-requirements-vN.md` 内容不改版本，回归与交付照样通过 | `df.py:435-448`、`df.py:2100-2105` |
| 3 | **项目状态没有迁移表**：8 处直接赋值，可跳阶（`release` 从任意状态置 candidate）或回退（`plan-activate` 回 planned） | `dfcore.py:29-43`、`df.py:628/2435` |
| 4 | **Git 归属有盲区**：开工前就 dirty 的文件被整份剔除；ignored 文件不可见 | `dfcore.py:993`、`dfcore.py:961` |
| 5 | **证据只能自证**：`test-report.json` 由 Agent 写，CLI 只能校验自洽 | `dfcore.py:2086` |

> 这些问题不是"某个函数写错了"，而是**同一约束没有唯一收口点**：
> 每次修一个入口，另一个入口仍然漏（本轮就发现 `req-import` 归档缺位、`plan-activate` 无来源校验）。

## 3. 当前有多少条核心系统不变量？

**28 条**（`05-system-invariants.md`），分为 9 组：
事实来源与版本（3）、需求完整性（2）、变更合法性（3）、DAG 与并行（3）、
真实变更识别（2）、状态迁移（3）、原子性与历史（2）、回归与交付（6）、并发与恢复（4）。

## 4. 哪些不变量已经被代码强制保证？

**14 条**：

| 领域 | 不变量 |
| --- | --- |
| 需求 | INV-004（active 必须有验收标准）、INV-005（退役凭据） |
| 变更 | INV-006（CR 绑定）、INV-007（基准过期拒绝） |
| DAG | INV-009（无环/依赖存在） |
| 路径 | INV-013（`..`/绝对路径/符号链接） |
| 状态 | INV-014（节点迁移表）、INV-016（accepted 白名单终态） |
| 回归 | INV-019（代码快照）、INV-021（必需项不可降级）、INV-022（证据自洽） |
| 交付 | INV-024（门禁不可绕过）、INV-027（失败安全默认）、INV-028（用户验收） |

## 5. 哪些不变量只是文档或 Prompt 约束？

**5 条完全未实现**：

- **INV-002** 需求基准只能走变更流程（无内容校验）
- **INV-003** 需求版本指针一致（无一致性检查）
- **INV-008** 计划变更必须可追溯 CR（`based_on` 可空）
- **INV-015** 项目状态不得跳阶/回退（无迁移表）
- **INV-023** 证据真实性（无机制，只能自证）

**9 条部分实现**（有检测但不完整/不阻止）：INV-001（派生文件无防篡改）、INV-010（并行资源别名）、
INV-011（ignored 文件）、INV-012（baseline dirty）、INV-017（仅一条路径有事务）、
INV-018（`req-import` 覆盖历史）、INV-020（需求只绑版本号）、INV-025（`doctor` 写图无锁）、
INV-026（其他多文件写无 journal）。

## 6. 哪些设计需要重新讨论？

见 `08-decisions-required.md`，最关键的 5 个：

1. **D-01** 计划变更是否一律走 CR（决定 INV-008 怎么修）。
2. **D-02** 需求基准用内容指纹还是由 CLI 生成（决定"谁写正式数据"）。
3. **D-03 / D-09** Git 归属：逐文件指纹 vs 每节点 worktree（决定并行能不能真正安全）。
4. **D-06** `release` 门禁失败时是否改状态（决定状态机语义）。
5. **D-05** 证据真实性的上限（接受自证 / 提高伪造成本 / CLI 代跑测试）。

另外两个"必须有人拍板"的兼容问题：D-07（legacy 路径何时退场）、D-08（示例项目是否重做）。

## 7. 下一阶段应该先解决哪些问题？

按"堵漏优先、重构靠后"排序：

| 顺序 | 动作 | 对应不变量 | 预计改动面 |
| --- | --- | --- | --- |
| 1 | `plan_version>1` 必须有 `based_on`（或 `--replan` 留痕） | INV-008 | `plan_import`/`plan_activate`/schema/测试 |
| 2 | 项目状态迁移表 + 单一写入函数 | INV-015 | 8 个调用点 + 新模块 |
| 3 | 需求基准 digest + 回归绑定 digest | INV-002/020 | `req_confirm`/`regression_import`/`release*` |
| 4 | baseline dirty 逐文件指纹 + ignored 策略 | INV-012/011 | `dfcore` Git 三函数 + 配置 |
| 5 | 报告指纹（release/节点报告） | INV-018/023 | `release`/`accept`/`validate` |
| 6 | 统一多文件事务 | INV-017 | `req_import`/`release`/`regression_import` |
| 7 | 示例项目重做 / 迁移 | F1 | `examples/` + CI |
| 8 | 按不变量重写测试（`test_invariants.py`） | 全部 | `tests/` |

> 建议每完成一项，就把对应的 INV 状态从"部分/未实现"改为"已实现"，并在 `09-audit-index.md` 补测试链接。
> 这样"不变量清单"成为后续所有迭代的唯一验收依据，而不是又一轮"修 bug + 加测试"。

## 本阶段做了什么 / 没做什么

**做了**：读完 `scripts/` 全部核心代码、7 个 Schema、6 个测试文件、示例项目、CI、SKILL 与 docs；
跑通引擎 223 条、示例 74 条、`validate`（只读）；整理 9 份设计文档 + 本总结。

**没做**（遵守阶段限制）：没有改任何 `.py`/测试/配置/JSON；没有执行会改状态的 CLI（`init/start/sync/accept/release*` 等）；
没有"顺手修"任何发现的问题；没有替产品做 D-01~D-10 的决定。

**下一步**：等待独立审查。审查通过后再进入第二阶段（按第七节的 1→8 顺序实施），
实施时每条改动都必须绑定对应 INV 编号与测试。
