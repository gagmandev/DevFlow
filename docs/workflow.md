# 工作流

## 一、全景

```
init → analyze → clarify → confirm → plan → [用户审核] → plan-activate
                                                          ↓
                          ┌───────────────────────────────┘
                          ↓
                  next → start → 执行 → sync → accept → （循环）
                          ↑                              ↓
                          └──── retry / change ←─────────┘
                                                          ↓
                                              regression → release
```

17 个核心模式：`init` `analyze` `clarify` `confirm` `plan` `board` `status` `next`
`task` `execute` `sync` `retry` `change` `impact` `regression` `release` `release-accept`；
另有 `release-ready` `recover` `migrate` 三个运维入口。

## 二、七个阶段

### 阶段 1 · 立项（init）

判断 greenfield / existing / refactor → 分析技术栈 → 写 `project.json`。

已有项目必须先摸清现状。**不要在别人的代码库里发明一套新架构。**

### 阶段 2 · 需求（analyze → clarify → confirm）

```
读需求 → 提取 15 个维度 → 建 REQ 编号 → 分 A/B/C 类歧义
     → 问 A 类 → 记录答复 → 生成需求基准 → req-confirm
```

出口条件：所有 A 类问题有答案，`confirmed-requirements-vN.md` 存在且被确认。

**这道门没过，不许拆任务。** 需求没定就拆，拆出来的东西没有依据，改一次全废。

### 阶段 3 · 规划（plan）

```
拆节点（5 阶段 × 功能域）→ 推依赖 → 判并行 → 写 8 个字段
  → prompt-init 生成提示词骨架 → 补实现步骤
  → plan-import（自动校验 + 算 ready + 出看板 + 出依赖图）
  → 用户审核 → plan-activate
```

出口条件：`validate` 0 错误，用户确认节点数量/边界/依赖/优先级。

草稿计划 `status=draft`，不能执行。这不是形式主义——`next` 会直接拒绝。

### 阶段 4 · 执行（execute → sync → accept）

单节点闭环：

```
start（检查依赖）
  → 读提示词 + 上下文
  → 实现 → 写测试 → 跑测试
  → 失败则修（最多 2 轮）
  → 写 execution-report.md + test-report.json
  → sync（读证据，转 needs_review 或 failed；只接受 in_progress）
  → accept（证据齐全 → completed，自动解锁下游）
```

并行：满足六条件的节点进同一个 `parallel_group`。
宿主支持子 Agent 就并行派发，不支持就生成并行清单让用户手动投喂。
**不要说"已并行执行"如果实际是顺序做的。**
同组冲突（间接依赖、文件/表/接口/配置撞车）会被校验器阻断，例外要写 `parallel_exceptions`。

外部 Agent 的报告用 `sync <ID> --external --from <目录>` 导入，仍走同一套校验。

`start` 前会比对依赖交付物指纹：`accept` 时记下的文件若在本节点 `inputs` 里且被改过，
会要求先评估影响（`--ack-context --reason` 留档）。

### 阶段 5 · 变更（change）

```
用户提变更 → change-new → 读需求/计划/状态/报告/代码
  → 逐节点判定 reusable/modify/add/deprecate/review
  → 写 impact-assessment.md
  → 生成需求 vN+1 + 计划 vN+1（draft）
  → 用户确认 → change-apply（旧计划 superseded）
```

关键：**不覆盖旧版本，不重跑不受影响的已完成节点。**

### 阶段 6 · 回归（regression）

```
regression-check（节点完成度、证据、需求覆盖、需求验收项证据——任一不足直接失败）
  → 真实执行：构建 / 单元 / 集成 / 核心链路 / 契约 / 一致性 / 权限 / 脚本
  → 问题定位到 REQ 与 DF
  → 修复 → 重跑受影响测试
  → 写 final-report.json → regression-import（校验 + 渲染 Markdown）
```

JSON 是事实来源，整体状态必须与检查项推导结果一致；缺必需检查项直接拒绝导入。

没跑的写 `not_run`。**不要写 passed。**

### 阶段 7 · 交付（release → release-ready → release-accept）

`release` 从状态汇总事实（需求、任务、证据、验证分类）并检查交付门槛，状态置
`release_candidate`——**报告生成不等于交付**。`release-ready` 复核门槛后进入
`release_ready`，最后由用户执行 `release-accept --by` 才变成 `accepted`。

回归结论必须是 `passed`；节点没完成、证据缺失、需求未覆盖，都进不了 `release_ready`。

## 三、状态在什么时候变

| 操作 | 状态变化 |
| --- | --- |
| `plan-import` | 计划 `draft`；无依赖节点 `pending → ready` |
| `plan-activate` | 计划 `draft → active`，旧计划 `→ superseded` |
| `start` | `ready → in_progress`，attempts +1，归档旧报告 |
| `sync` | `→ needs_review`（证据全）/ `→ failed`（证据不全或测试失败） |
| `accept` | `needs_review → completed`，下游 `pending → ready` |
| `block` / `fail` | `→ blocked` / `→ failed` |
| `retry` | `failed/blocked → in_progress`，生成新的 `attempt_id` |
| `change-apply` | 旧计划 `→ superseded`，新计划 `→ active`（事务提交） |
| `regression-check` | `→ regression`（有阻塞则失败） |
| `regression-import` | 写入 `final-report.json` + 渲染 Markdown |
| `release` | `→ release_candidate`（仅生成报告） |
| `release-ready` | `release_candidate → release_ready`（门槛通过） |
| `release-accept` | `release_ready → accepted`（用户确认） |
| `recover` | 回滚未完成事务，恢复文件到事务前 |

`accepted` 是终态：之后 `start` / `sync` / `accept` / `retry` / `regression-check` / `release` /
`release-ready` 都会被拒绝（退出码 2）。要继续开发走 `change-new` → `change-apply`，
新计划激活时状态回到 `in_progress`。

## 四、示例项目走了一遍

`examples/sample-project/` 是一个真实跑通的积分账户服务（Python + unittest，74 条测试）。
完整过程见 `examples/sample-project/WALKTHROUGH.md`。

摘要：

| 阶段 | 结果 |
| --- | --- |
| init | 识别为 greenfield，写入技术栈与 5 条硬约束 |
| analyze | 8 条需求（REQ-001~008），4 个 A 类歧义 |
| clarify | Q-001 奖励比例、Q-002 有效期、Q-003 并发、Q-004 退款 |
| confirm | 需求基准 v1 |
| plan | 7 个节点，2 个并行组 A，关键路径 6 层 |
| execute | 逐节点真实开发 + 真实测试，51 条测试全绿 |
| change | CR-001 冻结能力 → 2 modify / 1 add / 4 reusable → v2 |
| execute v2 | DF-002 返工、DF-008 新增、DF-006 重跑、DF-007 文档，74 条全绿 |
| regression | 编译 + 74 测试 + 核心链路 + 两条对账，全部真实执行 |
| release | 8 节点：7 已验证 / 1 已实现未验证（纯文档） |

## 五、每步的"出口条件"

不要把阶段当成仪式。每个阶段都有明确的出门条件：

| 阶段 | 出门条件 |
| --- | --- |
| init | `project.json` 有 tech_stack 和 commands.test |
| analyze | 每条需求有编号、来源、验收标准 |
| clarify | 所有 A 类问题 `answered` |
| confirm | 需求基准存在且 `req-confirm` 成功 |
| plan | `validate` 0 错误 + 用户批准 |
| execute | 每个节点 `completed` 且有证据 |
| change | 用户确认影响范围 + 新计划 validate 通过 |
| regression | 9 项检查有明确结果（pass 或 not_run + 原因） |
| release | 交付报告区分了"已验证/未验证/未实现" |

## 六、什么时候该停下来问

- 需求有 A 类歧义
- 计划要用户批准
- 变更影响范围超过 3 个已完成节点
- 需要新增依赖 / 改数据结构 / 动生产环境
- 节点连续两轮失败
- 代码现状与计划不一致

除此之外自己决定，记进 `decisions.md` 或执行报告的 `assumptions`。
