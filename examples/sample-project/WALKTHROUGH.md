# 示例项目：从需求走到交付

这个目录不是"演示数据"，是**真跑过一遍**的完整项目：真实的 Python 代码、真实的测试、
真实的执行报告、真实的变更记录。

- 业务：积分账户服务（开户、签到、订单奖励、兑换扣减、流水查询、退款冻结）
- 技术：Python 3.11+ 标准库，JSON 文件存储，unittest
- 规模：8 个节点（v1 七个 + 变更新增一个），74 条测试

## 怎么看

| 想看什么 | 打开 |
| --- | --- |
| 原始需求（只读） | `requirements.md` |
| 澄清记录（4 个 A 类问题） | `.devflow/requirements/clarification.md` |
| 需求基准 v1 / v2 | `.devflow/requirements/confirmed-requirements-v1.md` / `-v2.md` |
| 最终计划 | `.devflow/plans/backlog-v2.json` |
| 最终看板 | `.devflow/plans/board-v2.md` |
| v1 看板（对比变更前后） | `.devflow/plans/board-v1.md` |
| 节点提示词 | `.devflow/nodes/DF-004/prompt.md` |
| 执行报告（含证据块） | `.devflow/nodes/DF-004/execution-report.md` |
| 变更影响分析 | `.devflow/changes/CR-001/impact-assessment.md` |
| 回归报告 | `.devflow/regression/final-report.md` |
| 交付报告 | `.devflow/releases/release-report.md` |
| 代码 | `src/points/` |
| 测试 | `tests/`（74 条） |

## 实际跑过的命令

### 1. 初始化

```bash
$ python ../../scripts/df.py init --name "积分账户服务" --id points-demo --type greenfield
DevFlow 初始化完成：.devflow
- project_id: points-demo
- project_type: greenfield
```

### 2. 需求与澄清

```bash
$ python ../../scripts/df.py req-import --file .devflow/requirements/traceability.json
已写入 .devflow/requirements/traceability-v1.json：8 条需求（v1）
当前指针已更新：.devflow/requirements/traceability.json

$ python ../../scripts/df.py req-confirm --version 1
需求基准 v1 已生效：.devflow/requirements/confirmed-requirements-v1.md
```

澄清阶段提出 4 个 A 类问题（奖励比例、有效期、并发方案、退款处理），
**需求未确认前没有生成任何计划**。

### 3. 拆解与导入

```bash
$ python ../../scripts/df.py plan-import --file .devflow/plans/.import-v1.json --version 1
草稿计划已生成：.devflow/plans/backlog-v1.json（7 个节点，status=draft）
...
== 依赖检查 · 计划 v1 ==
✓ 无循环依赖，依赖节点全部存在
- 关键路径（6 层）：DF-001 → DF-002 → DF-003 → DF-004 → DF-006 → DF-007
- 分层：
    L1: DF-001
    L2: DF-002
    L3: DF-003
    L4: DF-004, DF-005
    L5: DF-006
    L6: DF-007
计划处于 draft 状态，不能执行。
```

7 个节点：3 个 Foundation/Shared，2 个 Business（并行组 A），1 个 Integration，1 个 Release。
DF-004 与 DF-005 同层、无依赖、文件不重叠 → 允许并行。

### 4. 执行（逐节点真实开发）

```bash
$ python ../../scripts/df.py start DF-001
$ python -m unittest discover -s tests -v
Ran 5 tests in 0.000s
OK
$ python ../../scripts/df.py sync DF-001
DF-001 已同步，等待验收（needs_review）
$ python ../../scripts/df.py accept DF-001
✓ DF-001 项目骨架与领域异常 验收通过
新解锁：DF-002
进度：14.3%（1/7）
```

每个节点都真的写了代码和测试，报告里的用例数与 `unittest` 输出一致。

到 DF-004 / DF-005 时，看板显示：

```
可执行节点 2 个：
- 并行组 A（2 个，确认无文件冲突再并行）：
    DF-004 账户服务：开户 / 签到 / 奖励 / 扣减 / 余额（P1）
    DF-005 流水查询与运营视图（P2）
```

本次由单个 Agent **顺序**完成（报告里如实写了，没有声称并行）。

v1 完成时：

```bash
$ python ../../scripts/df.py status
生效计划：v1（active，7 节点） | 需求 v1
进度：100.0%（7/7）
```

### 5. 需求变更 CR-001

业务方要求：退款时冻结积分（v1 的 OPEN-001）。

```bash
$ python ../../scripts/df.py change-new --title "退款场景：积分冻结与解冻" --type rule_change
$ python ../../scripts/df.py change-set --change CR-001 --patch patch.json
已更新 change-request.json（8 条影响）
$ python ../../scripts/df.py plan-import --file .devflow/plans/.import-v2.json --version 2
草稿计划已生成：.devflow/plans/backlog-v2.json（8 个节点，status=draft）
! DF-003: 已完成，但依赖 DF-002 当前是 ready（变更返工导致）
$ python ../../scripts/df.py change-apply --change CR-001
CR-001 已应用：计划 v1 → v2
当前可执行：DF-002
```

影响分析的结论（详见 `impact-assessment.md`）：

| 节点 | 动作 | 依据 |
| --- | --- | --- |
| DF-002 | modify | LedgerEntry 要加 `frozen_delta` |
| DF-008 | add | 冻结/解冻是独立写路径 |
| DF-006 | modify | 要补冻结链路与并发用例 |
| DF-007 | modify | 文档过时 |
| DF-001/003/004/005 | reusable | 接口未变；DF-004 已按 available 校验 |

**DF-004 没有重跑**——它 v1 就用 `available` 校验，冻结后自动生效。
这是"已完成的节点不因变更被无谓重跑"的实际例子。

### 6. 返工与新增

```bash
$ python ../../scripts/df.py start DF-002
旧报告已归档：.devflow/nodes/DF-002/history/execution-report.20261125T1410.md, ...
```

DF-002 返工时发现一个真问题：`frozen_delta` 如果插在 `amount` 后面，
`tests/test_queries.py` 里的位置参数会静默错位。最终把新字段追加到末尾，
并加了一条 `test_positional_call_still_works` 锁住这个约束。

### 7. 回归

```bash
$ python ../../scripts/df.py regression-check
✓ 没有未完成节点，所有已完成节点证据齐全

$ python -m compileall -q src tests      # exit 0
$ python -m unittest discover -s tests   # Ran 74 tests / OK
```

核心链路实际输出：

```
冻结后超可用兑换被拒: available=16
余额: {'balance': 24, 'frozen': 0, 'available': 24}
流水条数: 5 类型: ['checkin', 'freeze', 'order_reward', 'redeem', 'unfreeze']
对账1 sum(amount)==balance: True
对账2 sum(frozen_delta)==frozen: True
```

回归报告里，权限与数据库脚本两项写的是 `not_run` + 原因，没有写成 passed。

### 8. 交付

```bash
$ python ../../scripts/df.py release
交付报告已生成：.devflow/releases/release-report.md
- 已验证 7 | 已实现未验证 1 | 未实现 0
```

## 八个验收场景的对应证据

| # | 场景 | 证据 |
| --- | --- | --- |
| 1 | 新项目初始化 | `df.py init` 识别 greenfield，生成 `.devflow/` 与 project.json，无外部服务 |
| 2 | 需求分析与澄清 | `clarification.md` 的 4 个 A 类问题；确认前无计划文件 |
| 3 | 需求确认与任务拆解 | 7 节点、5 阶段、2 并行组、无环（`dependency_check` 输出）、7 份提示词 |
| 4 | 看板生成 | `backlog-v1.json` 通过 Schema；`board-v1.md` 由 JSON 渲染；进度统计正确 |
| 5 | 节点独立执行 | `nodes/DF-004/prompt.md` 可脱离需求文档执行；17 条真实测试；报告含证据块 |
| 6 | 节点同步 | `sync` + `accept` 后自动解锁下游（DF-003 → DF-004/005） |
| 7 | 需求变更 | `CR-001`：2 modify / 1 add / 1 文档 modify / 4 reusable；v1 计划与报告全部保留 |
| 8 | 全量回归 | `regression/final-report.md`：编译 + 74 测试 + 核心链路 + 两条对账，未跑项标 not_run |

## 自己复跑

```bash
cd examples/sample-project
python -m unittest discover -s tests -v        # 74 条
python ../../scripts/df.py status
python ../../scripts/df.py doctor
```

注意：这个目录的 `.devflow/` 已经是"交付完成"状态。想从零体验流程，
复制 `requirements.md` 到一个空目录，然后 `df.py init` 重新走一遍。

需求方答复在 `clarification.md` 里标注了"演示设定"——真实项目里这些答案来自你的用户。
