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

审查后已定 6 项（见 `08` 第一节），仍有 3 项待定：

**已定**：
1. **D-01**：计划激活唯一入口——v1 用 `plan-activate`（仅当从未激活），v2+ 只能 `change-apply`。
2. **D-02**：需求变更走 CR 暂存（staging）+ CLI 双 digest + `baselines.json` + `change-apply` 事务原子切换。
3. **D-03 / D-09**：生产并行必须每节点独立 worktree；未实现前只允许串行。
4. **D-04**：ignored 文件 production 默认 `deny`，例外需 allowlist（path/purpose/approved_by）。
5. **D-05**：Agent 自报只是过程记录；生产回归以绑定 commit SHA + baseline 的 CI 结果为准。
6. **D-06**：门禁失败不改状态；全部门禁通过才进 `release_candidate`。

**待定**：D-07（legacy 退场）、D-08（示例项目）、D-10（是否拆分 `df.py`）。

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
没有"顺手修"任何发现的问题；没有替产品做 D-07 / D-08 / D-10 的决定（D-01~D-06、D-09 已在后续轮次定案）。

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
| 决策定案（第三轮更新） | D-01 改为「计划激活唯一入口」；D-02 扩为「staging + 原子切换」；D-04 定为 production deny；D-03+09 / D-05 / D-06 维持；D-07 / D-08 / D-10 待定 |
| 威胁模型 | 04 新增第五节：资产、威胁主体、攻击面 × 四层控制、指纹的边界 |
| 一致性 | 00/01/02/03/04/05/06/07/08/09 交叉引用已对齐（风险号 A6/A7、B9/B10、INV-029） |

> 本轮仍只改 Markdown；未运行任何会改变项目状态的命令。


---

## 附：第三轮审查修订记录

| 修订项 | 变化 |
| --- | --- |
| D-01 收紧 | 由「计划变更走 CR（可 `--replan` 留痕）」改为「**计划激活唯一入口**：v1 才允许 `plan-activate`，v2+ 只能 `change-apply`」；`plan-import` 只产草稿 |
| D-02 扩充 | 补全 CR 需求变更的完整设计：staging 目录结构、manifest、双 digest → `baseline_id`、确认人、「需求变更原子提交文件集合」（第五轮起数量动态决定）、提交顺序、回滚、证据失效时机（08 D-02 §1–§8） |
| D-04 定案 | production 默认 `deny`；例外需 allowlist（`path`+`purpose`+`approved_by`）；development 允许 `off` 但必须标 `non_production_verification` |
| 状态机澄清 | 03 的 Project / Requirement / Plan 全部拆成「实际行为 / 风险 / 目标」三段；明确 `release` 当前只拒 `accepted`、`req-import` 的 reason/confirmed_by 目前**不是**导入期校验 |
| 数量修正 | INV-015 的项目状态直接赋值修正为 **8 处**（`df.py:468/628/1080/1736/2006/2435/2594/2627`） |
| 结构修正 | 删除 07 中重复的「四、不变量 → 目标强制点」旧表；04 两个第五节重编号（威胁模型=五、结论=六）；「三层控制」改为「四层控制」 |
| Git 可见性 | 04/02 明确：Git 本身能看到 `.devflow` 内改动（若已提交），但 DevFlow 的 `git_changes_since` 显式过滤 `.devflow/`，两者不同 |
| 新增核对表 | 09 新增「决策的唯一实现入口」表：每条决策列出唯一写入口、当前竞争路径、关闭方式 |
| 需求变更数据流 | 03 §二重写为「change-new → change-stage → 用户确认 → change-apply」，含 staging 数据流表、提交前置、失败恢复、指针切换点 |

> 本轮仍只改 Markdown；未运行任何会改变项目状态的命令。所有目标功能（staging、baseline_id、
> 原子提交文件集合、ignored deny、CI 证据绑定）均标注**待实现**。


---

## 附：第四轮审查修订记录

| 修订项 | 变化 |
| --- | --- |
| 身份信任边界 | 08 D-02 §3 与 04 §5.5 明确：`--by`/`confirmed_by` 只是审计声明，不是身份认证；ProjectLock 与 SHA-256 也不是；production 需外部审批锚点（签名审批文件 / 外部审批系统 / CI review），CLI 核验凭据后才写 `approval`；执行 Agent 不持凭据、对 CR/manifest/审批记录只读；无 OS 隔离时不能抵御恶意 Agent（防误操作 ≠ 防恶意） |
| 并发读取一致性 | 08 D-02 §5b 当时选定「事务标记 + 统一读取守卫」（**第五轮已修正为读写互斥 + OPEN 崩溃标记**，见下方第五轮记录）：`change-apply` 写 OPEN 标记 → 全部读入口（列出约 25 个命令，不遗漏 validate/doctor/regression-check）检查标记，读到 OPEN 即拒绝；顺序不变式：写锁 → OPEN → 写盘 → committed → 删标记；崩溃后必须先 recover 再开放读取。明确现有 Transaction 只保证写回滚、不保证读原子 |
| 文件集合口径 | "9 文件事务"改为「需求变更原子提交文件集合」（当时按核心 9 + 派生描述，第五轮修正为动态数量：journal 属事务基础设施不计入；）补清单生成、begin 前置检查（路径重复/存在性/文件类型）、回滚按 `existed_before` 删除或恢复 |

> 本轮仍只改 Markdown；未运行任何会改变项目状态的命令。读守卫（第五轮修正为读写互斥）、外部审批等目标功能均标**待实现**。


---

## 附：第五轮审查修订记录

| 修订项 | 变化 |
| --- | --- |
| 撤销错误结论 | 第四轮 §5b"标记检查与文件读取之间不需要锁"被独立审查推翻（检查点↔读取点存在竞态窗口），已从 08/04/05/07 全部移除 |
| 读端协议重设计 | 选定**读写互斥**：所有读正式数据的命令取与写者同一的项目锁（非共享读锁），取锁失败立即拒绝，持锁完成全部正式读取后才释放；写者持锁完成 OPEN→写盘→committed→删 OPEN 全程 |
| OPEN 角色调整 | OPEN 保留为**崩溃残留哨兵**：读者取锁后仍检查残留，残留即拒绝；只有 `recover --apply` / 严格校验的 `recover --complete` 可解除，恢复前不开放正式读取 |
| 实现约束如实记录 | `ProjectLock` 为写命令设计，复用到读命令（超时拒绝、跨平台锁行为、读串行化）**待代码审计**，不得假设已支持 |
| 命令读取路径分类表 | §5b 按实际读取内容分五类（只读 staging / 读正式 / 读正式+写派生 / 读写正式 / 事务恢复）；`change-stage` 因需读正式基准归读写类；`doctor`（读正式+写图、不在锁集合）与 `init` 标为已知缺口；`self-test` 因 mkdtemp 隔离不参与；`recover` 独立协议；未列命令默认最严格 |
| Commit Set 口径 | 删除"10 个文件 / 9+1"固定数量；五类划分（核心 / 条件性核心 / 派生 / 事务元数据 / 快照），journal 与快照不计入业务数量；补集合差异示例与派生文件回滚恢复旧计划状态 |

> 本轮仍只改 Markdown；未运行任何代码测试。所有目标机制（读写互斥、OPEN、Commit Set、
> 外部审批）均标注待实现；第四轮身份信任边界（`--by` 非认证、SHA-256 非签名、
> production 需外部审批锚点、无 OS 隔离不能防恶意 Agent）完整保留。
