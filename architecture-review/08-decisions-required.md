# 08 · 架构决策

> 本文件分两部分：**已定决策**（本轮采纳，作为第二阶段实施依据）与**仍待定**（需要产品/架构再拍板）。
> 已定决策不因"实现成本"回退；如果实施中发现不可行，必须回到本文件改决策，而不是在代码里留后门。

## 一、已定决策

### D-01 计划变更一律经 CR

**决策**：
1. 正式计划激活后，任何**影响交付基准**的计划内容修改（节点增删、`files_scope`、依赖、验收标准、
   需求引用、`outputs`/契约绑定）都必须通过**已确认的 CR**，走 `change-apply`。
2. **不设** `--replan` 之类的独立旁路；不存在"绕过 CR 但合法"的计划替换通道。
3. 纯执行元数据调整（`owner`、`remark`、`stats_note` 等不影响交付判定的字段）不要求新计划版本，
   但必须产生审计记录（谁、何时、改了什么、为什么）。

**推论**：
- `plan_version > 1` 的计划，`based_on.change_id` 必填且必须指向 `confirmed`/`applied` 的 CR；
  `plan-activate` 拒绝对这类计划的"裸激活"。
- 首次计划（`plan_version == 1`）允许无 `based_on`。
- 若历史项目存在无 `based_on` 的 v>1 计划：`migrate` 标记 `legacy_plan_origin`，只读保留，不允许再激活。

**对应**：INV-008；风险 A1 / B2；实施点 `plan-import` / `plan-activate` / `change-apply`。

### D-02 需求基线由 CLI 生成 SHA-256，并建立版本清单

**决策**：
1. `req-confirm` 由 **CLI** 计算并写入不可静默改写的需求基线记录：
   - `traceability_digest`：对 `requirements` 数组做规范化（键排序、去空白）后的 SHA-256；
   - `confirmed_doc_digest`：对 `confirmed-requirements-vN.md` 全文的 SHA-256；
   - `baseline_id = sha256(version | traceability_digest | confirmed_doc_digest)`；
   - 写入 `project.requirement_baseline = {version, baseline_id, 两个 digest, confirmed_by, at}`。
2. 维护**需求基线清单** `.devflow/requirements/baselines.json`：每次确认追加一条（不覆盖），
   形成"版本 → baseline_id"的不可静默覆盖映射。
3. `Plan`、`CR`、`Regression`、`Release` 都必须绑定同一个 `baseline_id`：
   - 计划写入 `requirement_baseline_id`；
   - 回归报告写入 `requirement_baseline_id`；
   - 交付报告元信息写入 `requirement_baseline_id`；
   - `release-ready` / `release-accept` 重新计算并与上述逐项比对。
4. `baseline_id` 变化即视为"需求基线已变更"：旧回归失效，必须重跑；旧交付报告不得用于验收。

**明确边界**：SHA-256 证明"内容与摘要一致"，**不证明**"这份内容经过谁确认"。确认动作的可信度来自
`confirmed_by` + 项目锁 + 审计记录，不来自哈希。

**对应**：INV-002 / INV-003 / INV-020 / INV-029；风险 A2 / A3 / A6。

### D-03 / D-09 生产并行必须每节点独立 worktree

**决策**（两条合并为一条，避免互相矛盾的默认策略）：
1. 生产环境的多 Agent 并行**必须**每节点独立 Git worktree（或等效的独立工作区），
   节点在自己的工作区改代码、跑测试、产出证据；主控负责合并与验收。
2. **Worktree 未实现前，禁止共享工作区的"生产级"并行执行**：只允许
   - 串行执行；或
   - 经过验证的隔离执行（例如容器/独立 clone），并在项目档案里显式记录隔离方式。
3. 共享工作区下的 Git 变更归属不可靠（`git_changes_since` 会把别的节点改动算进来），
   因此**不能**作为生产并行方案的默认实现。

**推论**：`parallel_group` 在生产档位下若没有 `isolation: worktree` 声明，`plan-activate` 应拒绝
（或降级为串行并记录）。development 档位可保留共享工作区，但必须在看板/报告里标注"非隔离并行"。

**对应**：INV-010 / INV-011 / INV-012；风险 B1 / B3；决策 D-09 不再单独存在。

### D-05 Agent 自报只是过程记录，生产回归以 CI 为准

**决策**：
1. `execution-report.md` / `test-report.json` 由 Agent 提交的内容，**只作为过程记录**，
   不具备独立验收权。
2. 生产项目的关键回归必须使用**可核验的 CI 结果**，且结果需绑定：
   - `commit_sha`（被验证的代码版本）；
   - `baseline_id`（需求基线，见 D-02）；
   - CI 运行标识（run id / URL / 环境）+ 退出码。
3. 信任边界必须写清：
   - SHA-256 与 CI 报告只能证明"这份结果对应这个 commit 和这份需求基线"，
     **不能证明"命令确实在无人干预下执行过"**；
   - 若 CI 结果由被审计方自行生成且无可信锚点（如平台签名、独立存储），它仍然只是"自报"的升级版。
4. 交付校验：无 CI 绑定的生产回归 → `release-ready` 阻断（development 可降级为警告并留痕）。

**对应**：INV-023 / INV-024；风险 D5。

### D-06 发布门禁失败不改状态

**决策**：
1. `release` 只负责**生成报告**，不因"报告生成成功"而改变生命周期状态。
2. 门禁不满足时：
   - 报告与元信息照常写入（便于对照修）；
   - `release-report.json.blockers` 记录未通过项；
   - **`project.status` 保持原状态**（不得进入 `release_candidate`）。
3. 只有**全部门禁满足**时，`release` 才把状态置为 `release_candidate`。
4. 不使用同一个 `status` 字段同时表示"报告已生成"和"具备候选资格"；两者分别是
   `release-report.json` 的存在性与 `project.status`。

**推论**：`release-ready` 的准入仍是 `release_candidate`；`release` 变成可重复执行的幂等操作
（每次都重算门禁）。旧项目若已处于 `release_candidate` 但门禁不满足，`release-ready` 会拒绝并要求重跑 `release`。

**对应**：INV-015 / INV-024；风险 D2。

## 二、仍待定

### D-04 ignored 文件的处理策略（待定）

- 选项：`allowlist`（默认只允许构建产物）/ `deny` / `off`。
- 需要产品确认：生产项目是否允许交付依赖 ignored 配置（`.env` 这类）。
- 未定期间：文档与实现都保持现状（不检测），但必须在 `release` 报告里声明"ignored 文件未纳入范围校验"。

### D-07 旧项目兼容路径的退场时间（待定）

- `legacy_release_shortcut` / `legacy_regression_accepted` / `release --legacy-regression` 目前无退出计划。
- 需要产品给出"哪个版本移除"；在此之前 `doctor` 应把 legacy 项目标为需要处理。

### D-08 示例项目是否重做（待定）

- 现状：`examples/sample-project` 是 schema 1.1 + accepted，缺机器可读回归/交付报告，当前代码无法复现。
- 选项：迁移补齐 / 用 `self-test` 生成新示例 / 降级为"纯业务代码示例"。
- 影响：CI 里的 doctor 提示、文档引用、新人基线。

### D-10 是否拆分 `df.py`（待定）

- 建议拆 `dfstate.py` / `dfintegrity.py` / `dfpolicy.py`（见 07 §一），但本轮不决策；
  如果 D-01/02/06 的实施量较大，拆分可以作为同期重构。

## 三、决策 → 不变量 → 实施点

| 决策 | 不变量 | 实施点 | 对现有代码的改动性质 |
| --- | --- | --- | --- |
| D-01 计划必走 CR | INV-008 | `plan_import` / `plan_activate` / schema / migrate | 收紧（新拒绝路径） |
| D-02 CLI 需求基线 digest | INV-002/003/020/029 | `req_confirm` / `req_import` / `regression_import` / `release*` | 新增字段 + 新校验 |
| D-03/09 worktree 并行 | INV-010/011/012 | 配置 + `plan-activate` + 部署文档 | 约束 + 新拒绝路径 |
| D-05 CI 证据 | INV-023/024 | `regression_import` / `release*` / 报告 schema | 新增字段 + 门禁 |
| D-06 失败不改状态 | INV-015/024 | `release` 状态写入点 | 行为变更（可能影响旧脚本） |

## 四、实施顺序（与 07 §四一致）

1. D-01（计划来源）—— 改动小、堵最大绕过。
2. D-06（release 状态语义）—— 决定状态机表，越早越省事。
3. D-02（需求 digest + 清单）—— 依赖 D-01 的 CR 绑定。
4. D-03/09（worktree 约束）—— 文档 + 激活校验。
5. D-05（CI 证据）—— 需要外部 CI 配合，放最后。

> 每一步都必须同时补 `tests/test_invariants.py` 的对应用例，并在 `09-audit-index.md` 更新状态。
