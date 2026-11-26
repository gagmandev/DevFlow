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

**"规则分散 + 关键事实无指纹"** 导致的多类绕过：

| # | 问题 | 证据 |
| --- | --- | --- |
| 1 | **计划可以不带 CR 直接换**：`plan-import` 的 `based_on` 可空，`plan-activate` 不校验来源与状态，等于绕过变更管理 | `df.py:522`、`validate.py:516-523`、`df.py:587` |
| 2 | **需求基准/回归没有内容指纹**：改 `confirmed-requirements-vN.md` 内容不改版本，回归与交付照样通过 | `df.py:435-448`、`df.py:2100-2105` |
| 3 | **项目状态没有迁移表**：8 处直接赋值，可跳阶（`release` 从任意状态置 candidate）或回退（`plan-activate` 回 planned） | `dfcore.py:29-43`、`df.py:628/2435` |
| 4 | **Git 归属有盲区**：开工前就 dirty 的文件被整份剔除；ignored 文件不可见 | `dfcore.py:993`、`dfcore.py:961` |
| 5 | **证据只能自证**：`test-report.json` 由 Agent 写，CLI 只能校验自洽 | `dfcore.py:2086` |
| 6 | **交付前可缩小需求范围**：`release_ready` 后 `req-import` 把需求标 `removed`（无凭据），门禁跳过非 active | `df.py:399`、`df.py:2231-2232`、`df.py:2565/2602` |
| 7 | **`planned` 下 `req-confirm` 可回退状态并改需求版本指针** | `df.py:434-437`、`df.py:466-467` |

> 这些问题不是"某个函数写错了"，而是**同一约束没有唯一收口点**：
> 每次修一个入口，另一个入口仍然漏（本轮就发现 `req-import` 归档缺位、`plan-activate` 无来源校验）。

## 3. 当前有多少条核心系统不变量？

**29 条**（`05-system-invariants.md`），分为 9 组：
事实来源与版本（3）、需求完整性（2）、变更合法性（3）、DAG 与并行（3）、
真实变更识别（2）、状态迁移（3）、原子性与历史（2）、回归与交付（7，含新增 INV-029）、并发与恢复（4）。

> 判定口径：**函数存在 ≠ 全流程强制**。本轮逐条核对了"哪些入口真正调用了检查函数"，
> 因此把 INV-005（退役凭据）、INV-024（交付门禁）从"已实现"下调为"部分实现"。

## 4. 哪些不变量已经被代码强制保证？

**12 条**：

| 领域 | 不变量 |
| --- | --- |
| 需求 | INV-004（active 必须有验收标准） |
| 变更 | INV-006（CR 绑定）、INV-007（基准过期拒绝） |
| DAG | INV-009（无环/依赖存在） |
| 路径 | INV-013（`..`/绝对路径/符号链接） |
| 状态 | INV-014（节点迁移表）、INV-016（accepted 白名单终态） |
| 回归 | INV-019（代码快照）、INV-021（必需项不可降级）、INV-022（证据自洽） |
| 交付 | INV-027（失败安全默认）、INV-028（用户验收） |

> INV-005 与 INV-024 已下调为"部分"：前者在 `req-import` 与交付阶段不生效，后者不重校验需求基线。

## 5. 哪些不变量只是文档或 Prompt 约束？

**6 条完全未实现**：

- **INV-002** 需求基准只能走变更流程（无内容校验）
- **INV-003** 需求版本指针一致/单调（无检查，且 `req-confirm` 可回拨）
- **INV-008** 计划变更必须可追溯 CR（`based_on` 可空）
- **INV-015** 项目状态不得跳阶/回退（无迁移表）
- **INV-023** 证据真实性（无机制，只能自证）
- **INV-029** 交付门禁重校验需求基线（`release_ready` 后仍可改需求，A6）

**11 条部分实现**（有检测但不完整/不阻止）：INV-001（派生文件无防篡改）、INV-005（退役凭据只在
validate/回归检查）、INV-010（并行资源别名）、INV-011（ignored 文件）、INV-012（baseline dirty）、
INV-017（仅 `change-apply` 有事务）、INV-018（`req-import` 覆盖历史）、INV-020（需求只绑版本号）、
INV-024（交付门禁不重校验需求基线）、INV-025（`doctor` 写图无锁）、INV-026（其他多文件写无 journal）。

## 6. 哪些设计需要重新讨论？

审查后已定 5 项（见 `08` 第一节），仍有 4 项待定：

**已定**：
1. **D-01**：影响交付基准的计划修改必须走已确认 CR，不设 `--replan` 旁路。
2. **D-02**：CLI 生成双 digest + `baselines.json`；计划/CR/回归/交付绑同一 `baseline_id`。
3. **D-03 / D-09**：生产并行必须每节点独立 worktree；未实现前只允许串行。
4. **D-05**：Agent 自报只是过程记录；生产回归以绑定 commit SHA + baseline 的 CI 结果为准。
5. **D-06**：门禁失败不改状态；全部门禁通过才进 `release_candidate`。

**待定**：D-04（ignored 文件策略）、D-07（legacy 退场）、D-08（示例项目）、D-10（是否拆分 `df.py`）。

## 7. 下一阶段应该先解决哪些问题？

按"堵漏优先、重构靠后"排序：

| 顺序 | 动作 | 对应不变量 / 决策 | 预计改动面 |
| --- | --- | --- | --- |
| 1 | `plan_version>1` 必须绑定已确认 CR（无旁路） | INV-008 / D-01 | `plan_import`/`plan_activate`/schema/migrate |
| 2 | 项目状态迁移表；`release` 失败不改状态 | INV-015 / D-06 | 8 个调用点 + 新模块 |
| 3 | CLI 需求基线双 digest + `baselines.json` + 全链路绑定 | INV-002/003/020 / D-02 | `req_confirm`/`req_import`/`regression_import`/`release*` |
| 4 | 交付门禁重算需求基线（digest + 退役凭据 + 版本） | **INV-029** / D-02 | `delivery_blockers`/`release-ready`/`accept` |
| 5 | baseline dirty 逐文件指纹；生产并行 worktree 约束 | INV-012 / C4、D-03 | `dfcore` Git 三函数 + 配置 + `plan-activate` |
| 6 | CI 证据绑定（commit + baseline）替代自报 | INV-023/024 / D-05 | 回归报告 schema + `release*` + CI |
| 7 | 报告指纹（release/节点报告） | INV-018 | `release`/`accept`/`validate` |
| 8 | 统一多文件事务 | INV-017 | `plan_activate`/`req_import`/`release`/`regression_import`/`accept` |
| 9 | 示例项目重做 / 迁移（待 D-08） | F1 | `examples/` + CI |
| 10 | 按不变量重写测试（`test_invariants.py`） | 全部 | `tests/` |

> 建议每完成一项，就把对应的 INV 状态从"部分/未实现"改为"已实现"，并在 `09-audit-index.md` 补测试链接。
> 这样"不变量清单"成为后续所有迭代的唯一验收依据，而不是又一轮"修 bug + 加测试"。

## 本阶段做了什么 / 没做什么

**做了**：读完 `scripts/` 全部核心代码、7 个 Schema、6 个测试文件、示例项目、CI、SKILL 与 docs；
跑通引擎 223 条、示例 74 条、`validate`（只读）；整理 9 份设计文档 + 本总结。

**没做**（遵守阶段限制）：没有改任何 `.py`/测试/配置/JSON；没有执行会改状态的 CLI（`init/start/sync/accept/release*` 等）；
没有"顺手修"任何发现的问题；没有替产品做 D-01~D-10 的决定。

**下一步**：等待独立审查。审查通过后再进入第二阶段（按第七节的 1→8 顺序实施），
实施时每条改动都必须绑定对应 INV 编号与测试。


---

## 附：第二轮审查修订记录

| 修订项 | 变化 |
| --- | --- |
| 新增 P0 风险 | A6（`release_ready` 后 `req-import` 缩小交付范围，可绕过需求确认与 CR）、A7（`planned` 下 `req-confirm` 回退状态并改版本指针） |
| 不变量重校 | INV-005（退役凭据）与 INV-024（交付门禁）由"已实现"下调为"部分"；新增 INV-029（交付重校验需求基线）；总数 28→29，已实现 14→12，部分 9→11，未实现 5→6 |
| 事务描述修正 | 明确"唯一使用 `Transaction` 的是 `change-apply`"；`plan-activate`/`req-import`/`release`/`regression-import` 均无事务，且 `regression-import` 不改项目状态 |
| 状态机补全 | 由 6 个补到 7 个（新增 Requirement / 展开 Regression、Release），标注触发命令、前置条件、证据与禁止迁移 |
| 决策定案 | D-01 / D-02 / D-03+D-09 / D-05 / D-06 采纳；D-04 / D-07 / D-08 / D-10 仍待定 |
| 威胁模型 | 04 新增第五节：资产、威胁主体、攻击面 × 四层控制、指纹的边界 |
| 一致性 | 00/01/02/03/04/05/06/07/08/09 交叉引用已对齐（风险号 A6/A7、B9/B10、INV-029） |

> 本轮仍只改 Markdown；未运行任何会改变项目状态的命令。
