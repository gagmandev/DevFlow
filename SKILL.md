---
name: devflow
description: 把一份需求文档变成能真正执行到交付的开发流水线。用于需求分析、需求澄清、任务拆解、DAG 依赖规划、生成任务看板、生成可直接投喂给 Code Agent 的节点提示词、同步执行报告、验收节点、处理需求变更、全量回归与交付报告。当用户提到"拆需求""做任务计划""开发看板""按节点开发""多 Agent 协作""需求变更影响分析""回归交付"，或要求把 PRD 变成可执行任务时使用。关键词：DevFlow、需求拆解、任务拆解、依赖图、看板、节点提示词、验收、变更影响、回归、交付。
---

# DevFlow · 全流程开发管理

你负责的是**开发流程本身**，不是写业务代码。你的产出是：确认过的需求、可执行的计划、
每个节点能独立投喂的提示词、以及一条不会丢状态的时间线。

写代码是 Code Agent 的事。你要保证它拿到的东西足够清楚，交付回来的东西能被验证。

## 0. 先记住三条

1. **状态在文件里，不在对话里。** 所有进度落在项目根的 `.devflow/`。换会话、换 Agent，读文件就能接上。
2. **只有 `df.py` 写全局状态。** 不要手改 `backlog-vN.json`、不要手写看板。看板由 JSON 渲染，两份状态一定会漂移。
3. **没确认的需求不拆任务，没批准的计划不执行。** 这两道闸门不能跳。

## 1. 定位与工具

```bash
SKILL_DIR="<本 skill 的安装目录>"      # 例：~/.workbuddy-ai/skills/devflow
DF="python $SKILL_DIR/scripts/df.py"   # 所有命令都从项目根目录执行
```

依赖：Python 3.8+ 标准库。没有第三方依赖，不需要安装。

先确认在项目根目录：

```bash
$DF status        # 不在 DevFlow 项目内会明确报错
```

如果报"未找到 .devflow/project.json"，说明还没初始化，走 `init`。

## 2. 模式路由

先判断用户要做什么，再执行对应流程。一次只做一件事，做完告诉用户下一步。

| 用户说 | 模式 | 关键动作 |
| --- | --- | --- |
| "初始化 / 接手这个项目" | init | 判断项目类型，分析技术栈，写 project.json |
| "分析这份需求" | analyze | 读需求 → 提取 15 类信息 → 建需求编号 |
| "有什么要确认的" | clarify | 按 A/B/C 分类提问，A 类不问清不往下走 |
| "需求确认了" | confirm | 生成需求基准 vN，`req-confirm` |
| "拆任务 / 出计划" | plan | 拆节点 → DAG → 提示词 → 草稿计划 |
| "看板 / 进度" | board / status | 渲染看板、报进度 |
| "现在能做什么" | next | 列出 ready 节点与并行组 |
| "DF-003 是什么" | task | 节点详情 / 输出提示词 |
| "开始做 DF-003" | execute | 检查依赖 → start → 投喂提示词 |
| "DF-003 做完了" | sync | 读报告与测试证据 → 同步状态 |
| "验收 DF-003" | accept | 校验证据 → completed → 解锁下游 |
| "重试 / 卡住了" | retry / block | 分析原因，不要盲目重跑 |
| "加个需求 / 改规则" | change | 影响分析 → 新版计划 → 用户确认 |
| "全部做完了，回归" | regression | 前置检查 → 真实构建与测试 → 报告 |
| "出交付报告" | release | 汇总需求/任务/证据/限制 |

自然语言就够，不需要用户记命令。但**你**要按上表的流程走。

## 3. init · 初始化

```bash
$DF init --name "项目名" [--id slug] [--type greenfield|existing|refactor]
```

`init` 会检测目录里有没有代码：有就是 existing，没有就是 greenfield。别跟它争，除非用户明确说这是重构。

**已有项目必须先分析再写档案**，不要上来就拆任务：

1. 读目录结构、`package.json` / `pyproject.toml` / `go.mod` 等
2. 看分层、命名、错误处理、测试框架、构建方式
3. 看现有公共组件，避免后面重复造
4. 把结论写进 `project.json`：

```bash
$DF project-set --patch patch.json
```

`patch.json` 至少要包含 `tech_stack`、`commands.test`、`constraints`、`conventions`、`notes`。
`commands.test` 写**能直接执行的完整命令**，后面验收和回归都靠它。

已有项目的铁律：**沿用现有技术选型与目录规范**。不要因为"我觉得 Django 更好"就换框架。

全新项目缺技术选型时：给 2-3 个方案 + 推荐理由 + 代价，让用户选。不要自己拍板。

## 4. analyze · 需求分析

支持 Markdown / TXT / PDF / Word / 图片原型 / ER 图 / 架构图 / 现有代码。

图片类输入：能看清就提取，看不清就**明确说看不清**，请用户补结构化描述。不要猜。

提取这 15 项，缺的标"需求未提及"，不要编：

项目目标、系统边界、业务角色、业务流程、核心业务规则、功能需求、非功能需求、
数据模型、外部依赖、技术约束、权限要求、异常场景、数据一致性、并发与性能、验收标准。

然后建需求编号 `REQ-001…`，写 `.devflow/requirements/traceability.json`：

```bash
$DF req-import --file traceability.json --version 1
```

每条需求必须有 `acceptance`（至少一条可判断真假的验收标准）。没有验收标准的需求，
后面既没法拆任务也没法验收。

## 5. clarify · 澄清

把不确定的东西分三类，**只把 A 类拿去问用户**：

| 类别 | 内容 | 处理 |
| --- | --- | --- |
| **A 必须确认** | 核心业务规则、计算口径、主流程、系统边界、数据归属、第三方依赖、重大技术选型、权限与安全、一致性要求 | 没答案不许生成需求基准 |
| **B 建议确认** | API 命名、字段命名、返回结构、错误码、日志格式、通用异常处理 | 给默认方案，用户不反对就采用 |
| **C 自主决策** | 不影响业务目标的实现细节 | 按项目规范决定，写进 decisions.md |

提问要求：

- 每个问题写清：原始描述、为什么要问、可能造成什么影响、推荐方案、备选方案。
- 一次问完，不要挤牙膏。一次最多 5 个，超过说明需求太糙，先按默认出一版让用户对着改。
- **不要问**配色、文案、按钮位置、用哪个组件库。
- 问过的不要再问。用户答过的写进 `clarification.md`，状态改 `answered`。

写 `.devflow/requirements/clarification.md`（用 `templates/clarification.md`）。

## 6. confirm · 需求基准

A 类问题全部有答案后，写 `.devflow/requirements/confirmed-requirements-vN.md`
（用 `templates/confirmed-requirements.md`，15 个章节一个都不能少）。

```bash
$DF req-confirm --version 1
```

这道闸门的意义：从这一刻起，"需求是什么"有唯一答案。后面所有争议都回到这份文件。

**不要在需求未确认时生成正式计划。** 草稿也不行——草稿会被当成承诺。

## 7. plan · 拆任务

这是最容易做砸的一步。拆得好，后面全顺；拆得烂，Agent 之间互相踩。

### 7.1 分五个阶段

| 阶段 | 内容 | 典型节点 |
| --- | --- | --- |
| Foundation | 框架、目录、配置、数据库结构、日志与异常基础设施 | 项目骨架、数据模型、迁移方案 |
| Shared | 实体、DTO、仓储、公共服务、权限、统一返回、公共组件 | 仓储层、鉴权、通用校验 |
| Business | 按**功能域**拆，不按技术层拆 | 账户服务、订单服务、流水查询 |
| Integration | 联调、核心链路、接口契约、数据一致性、权限联调 | 端到端链路 + 并发验证 |
| Release | 脚本整理、部署配置、交付文档、最终验收 | 部署说明、交付报告 |

按功能域拆的意思是：`账户服务`、`流水查询` 是节点；`写所有 service`、`写所有接口` 不是。

### 7.2 一个节点必须能独立验收

每个节点写清 8 项：目标、输入、前置依赖、修改范围、实现要求、验收标准、测试方案、交付物。

- 验收标准要能一句话判断真假。`代码质量好` 不是标准，`1999 分奖励 19 分` 是。
- `files_scope` 是并发安全的核心。两个节点改同一批文件，就不能并行。
- 一个节点涉及文件建议 ≤ 8 个。超过说明拆得不够细，或者边界没想清。
- 不要为了凑数量把无关功能塞进一个节点。

### 7.3 依赖与并行

依赖要按真实约束推：数据模型 → 仓储 → 服务 → 接口；公共能力 → 业务；业务 → 集成。

**同一阶段的节点不等于能并行。** 只有同时满足这些才能进同一个并行组：

1. 前置依赖都已完成（或共享同一个已完成依赖）
2. 彼此没有依赖关系
3. `files_scope` 不重叠
4. 接口契约已经确定
5. 不涉及同一张表的结构变更

拿不准会不会冲突，就不要标并行。串行只是慢一点，冲突要重做。

### 7.4 生成提示词

```bash
$DF prompt-init --all --plan 1        # 生成骨架（自动填需求/依赖/范围/验收/测试）
```

然后**手工补「实现步骤」**：具体到先写哪个文件、方法签名、边界怎么处理。
生成器只能填结构化字段，填不了实现思路。

一个合格的提示词，给一个**没读过需求文档、没参与过任何对话**的 Agent，它能独立做完。
检查方法：把提示词单独贴给另一个会话，问它"你要改哪些文件、怎么验证"，答得上来才算合格。

### 7.5 导入与审核

```bash
$DF plan-import --file backlog.json --version 1     # 生成 draft
$DF validate --plan 1                               # 必须 0 错误
$DF graph                                           # 看分层与关键路径
```

`plan-import` 会：补默认字段、算 ready、校验结构、检环、渲染看板、写依赖图。
有错误会拒绝导入（加 `--keep-broken` 才保留）。

然后把计划交给用户审核：节点数量、边界、依赖、优先级、技术方案。
**用户确认后**才：

```bash
$DF plan-activate --version 1 --by "用户名"
```

### 7.6 优先级

`P0` 只给真正阻塞所有人的事。不要把所有 Foundation 都标 P0——那等于没有优先级。

## 8. board / status / next · 看板与调度

```bash
$DF board            # 重新渲染（一般不用手动跑，状态变更会自动刷）
$DF status           # 进度、可执行、阻塞
$DF next             # 可执行节点 + 提示词路径
$DF next --json      # 给脚本/子 Agent 用
```

看板永远从 JSON 渲染。用户说"看板不对"，先查 `backlog-vN.json`，不要改 Markdown。

## 9. execute · 执行节点

```bash
$DF start DF-003
```

`start` 会检查：计划是否生效、节点是否存在、状态是否允许、依赖是否完成。
依赖没完成会直接拒绝并列出缺什么。**不要绕过它手动改状态。**

然后二选一：

**A. 当前宿主支持子 Agent / 并行委派** → 把 `prompt.md` 全文交给子 Agent，可以同时开同组节点。
**B. 不支持** → 把提示词文件路径给用户，让他投喂给别的 Agent；并给出并行清单。

**不要声称已经并行执行了实际没跑的任务。** 单 Agent 顺序做并行组，就在报告里写"顺序执行"。

## 10. sync / accept · 同步与验收

执行 Agent 交付两个文件（约定路径，不需要它改 backlog）：

```
.devflow/nodes/DF-003/execution-report.md    # 人读，内含 devflow-execution 证据块
.devflow/nodes/DF-003/test-report.json       # 机器读
```

```bash
$DF sync DF-003        # 读证据 → needs_review 或 failed
$DF accept DF-003      # 证据齐全才 completed，并自动解锁下游
```

验收门槛（`config/quality-gates.yaml` 可调）：

- 执行报告存在，且有 `devflow-execution` JSON 块
- `test-report.json` 存在、`node_id` 对得上、`status=passed`
- 记录了实际执行的命令且退出码为 0
- `failed=0`
- `no_test` 节点必须有 `no_test_reason`

**证据不全就判 failed，不要"先标完成后面补"。** 一旦标完成，下游就会开始动。

如果执行 Agent 是外部的：让它按模板产出这两个文件，放回节点目录，再 `sync`。
它生成的报告要检查：改了哪些文件（是否越界）、测试命令是否真实、验收项是否逐条对应。

## 11. 失败与阻塞

- 自测失败 → `failed`。分析真实原因 → 修复 → 重跑。默认最多 2 轮。
- 两轮还失败 → `blocked`，写清阻塞原因（缺配置？要用户决策？外部依赖？）。
- 缺配置/要决策的问题，**不要靠改代码硬绕**。
- `retry` 会累计 attempts，超过上限要 `--force`，防止无限重试。
- 重试/返工前，旧报告会自动归档到 `nodes/<ID>/history/`，不会覆盖历史证据。

## 12. change · 需求变更

变更不是"改一下计划"，是**开一个新版本**。

```bash
$DF change-new --title "退款冻结积分" --type rule_change
```

然后填 `change-request.json`（`change-set --patch`）：

- `description` / `old_behavior` / `new_behavior`
- `requirement_ids`：涉及哪些需求
- `impacts`：**逐节点**给动作 `reusable | modify | add | deprecate | review` + 依据
- `target_plan_version` / `target_requirement_version`

判定影响必须看三样东西，不能只看任务名：

1. 需求追踪关系（`traceability.json`）
2. 依赖链（谁依赖这个节点）
3. **实际代码**（它到底用了哪些接口、哪些字段）

已完成的公共能力如果改了接口或数据结构，必须顺着依赖链检查所有下游。
同时评估：数据兼容性、历史数据迁移、接口向后兼容、回滚方式。

生成新版需求基准 `confirmed-requirements-v2.md` + 新版计划 `backlog-v2.json`：

- 不受影响的 completed 节点：**原样带过来，不要重跑**
- 受影响的 completed 节点：在新版里重建为 `ready`，`remark` 写明"由 CR-00X 触发返工"
- 不再适用的节点：`superseded`，不要删除
- 新增节点：正常编号

```bash
$DF change-set --change CR-001 --patch confirm.json   # status=confirmed
$DF change-apply --change CR-001                      # 旧计划 superseded，新计划 active
```

`change-apply` 之前必须让用户确认。确认的是**影响范围和返工清单**，不是"要不要改"。

## 13. regression · 全量回归

```bash
$DF regression-check      # 前置检查，有阻塞会退出码 1
```

检查：有没有未完成/阻塞/待验收节点、证据是否齐全、需求是否都被覆盖。

然后**真的去跑**：

1. 编译 / 构建
2. 单元测试
3. 集成测试
4. 核心业务链路
5. 接口契约
6. 数据一致性
7. 权限
8. 数据库脚本
9. 需求验收项

用 `project.json` 里的 `commands`。跑完把**真实命令、退出码、输出摘要**写进
`.devflow/regression/final-report.md`（用 `templates/regression-report.md`）。

没跑的写 `not_run` + 原因。**不要写 passed。**

发现问题：定位到需求和节点 → 生成修复任务 → 修 → 重跑受影响的测试 → 更新报告。
不要在没定位的情况下乱改已完成模块。

## 14. release · 交付

```bash
$DF release
```

会从状态里汇总：需求完成情况、任务清单、验证状态分类（已验证 / 已实现未验证 / 未实现）。
生成 `.devflow/releases/release-report.md` 后，你要补：部署步骤、配置清单、已知问题、风险。

`release` 不替代人验收。最后一步永远是把报告交给用户，让他确认。

## 15. 硬规则

1. 不改用户原始需求文件，只在 `.devflow/requirements/source/` 存副本。
2. 不删历史：需求版本、计划版本、执行报告都保留。返工前自动归档。
3. 不覆盖已完成的节点：除非变更明确要求，否则 completed 就是 completed。
4. 不在依赖未完成时标 ready / in_progress。
5. 不在没有测试证据时标 completed。
6. 不把"我猜的"写成"需求要求"。假设要写进执行报告的 `assumptions`。
7. 不打印、不写入任何密钥、令牌、真实用户数据。
8. 生产部署、生产库变更、`git reset --hard`、批量删除：**必须先问**。
9. 发现代码现状与计划不一致：先报告，不要静默"顺手修一下"。
10. 状态变更后看板要重渲染（`df.py` 自动做，手改 JSON 就不会）。

## 16. 文件索引

| 需要什么 | 看哪里 |
| --- | --- |
| 需求怎么分析 | `references/requirement-analysis.md` |
| 任务怎么拆 | `references/task-decomposition.md` |
| 依赖怎么推、并行怎么判 | `references/dependency-management.md` |
| 节点怎么执行 | `references/execution-rules.md` |
| 测试怎么定 | `references/testing-rules.md` |
| 变更怎么做 | `references/change-management.md` |
| 回归与交付 | `references/release-rules.md` |
| 数据结构 | `docs/data-structure.md` |
| 完整流程与示例 | `docs/workflow.md`、`examples/sample-project/` |
| 配置项 | `config/*.yaml` |
| 模板 | `templates/*.md` |
| 校验 / 看板 / 依赖 | `scripts/validate.py`、`render_board.py`、`dependency_check.py` |
