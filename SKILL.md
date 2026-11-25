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
| "加个需求 / 改规则" | change | 影响分析 → 新版计划 → 用户确认 → 事务提交 |
| "全部做完了，回归" | regression | 前置检查 → 真实构建与测试 → 报告 |
| "出交付报告" | release | 汇总需求/任务/证据/限制 → `release_candidate` |
| "可以交付了吗" | release-ready | 复核门槛 → `release_ready` |
| "验收通过 / 我确认交付" | release-accept | 记录验收人 → `accepted` |
| "状态不对 / 崩了" | recover | 查未完成事务 → 回滚 → doctor |
| "升级旧项目" | migrate | 旧状态别名与 schema 版本升级 |

自然语言就够，不需要用户记命令。但**你**要按上表的流程走。

### 按需读取（不要一次全读）

本文只放**入口、路由和安全规则**。细则按当前模式读对应文件，读完再动手：

| 当前在做什么 | 读哪个 |
| --- | --- |
| 分析需求、写澄清问题 | `references/requirement-analysis.md` |
| 拆节点、判粒度、定优先级 | `references/task-decomposition.md` |
| 推依赖、判并行、看关键路径 | `references/dependency-management.md` |
| 写节点提示词、执行节点、写报告 | `references/execution-rules.md` |
| 定测试策略、写测试证据 | `references/testing-rules.md` |
| 做变更影响分析 | `references/change-management.md` |
| 回归、交付报告、验收 | `references/release-rules.md` |

**机器能查的规则不要靠读文档记**：状态迁移、证据校验、范围比较、并行冲突、版本绑定，
全部由 `df.py` / `validate.py` 强制执行。文档写错了以代码为准，并且要修文档。

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

**方法与判据见 `references/task-decomposition.md`**（五阶段、八字段、粒度判据、按功能域拆、
依赖推导、自查清单）。这里只留三条红线：

- 按**功能域**拆，不按技术层拆：`账户服务`、`流水查询` 是节点；`写所有 service` 不是。
- 一个节点必须能独立验收：验收标准要能一句话判断真假（`1999 分奖励 19 分`，不是"代码质量好"）。
- 一个节点涉及文件建议 ≤ 8 个；超过说明拆得不够细或边界没想清。

优先级：`P0` 只给真正阻塞所有人的事，全标 P0 等于没有优先级。

### 7.1 依赖与并行

依赖按真实约束推：数据模型 → 仓储 → 服务 → 接口；公共能力 → 业务；业务 → 集成。
**同一阶段的节点不等于能并行**，六个条件（依赖、文件、契约、表、配置、阶段）见
`references/dependency-management.md`。拿不准就不要标并行——串行只是慢，冲突要重做。

**校验器会拦这些**（不是提醒）：同组直接依赖、**间接依赖**（A→B→C，A 和 C 同组）、
`files_scope` 重叠、`db_objects` 撞同一张表、`api_contracts` 撞同一个接口、
`shared_config` 撞同一个配置项。确认安全必须显式写例外：

```json
"parallel_exceptions": [
  {"group": "A", "reason": "两块代码互不影响，已评审", "approved_by": "架构师"}
]
```

拓扑分层只是"可以并行的候选"，不代表代码修改安全。别把同层节点直接塞进一个组。

### 7.2 生成提示词

```bash
$DF prompt-init --all --plan 1        # 生成骨架（自动填需求/依赖/范围/验收/测试）
```

提示词开头会写一段版本元数据：

```
---
project_id: points-demo
node_id: DF-004
plan_version: 1
requirement_version: 1
prompt_version: 3
generated_at: ...
---
```

`start` 会核对这五项。计划或需求变了，旧提示词直接拒绝执行，必须
`prompt-init <ID> --force` 重新生成。历史提示词不覆盖（版本号递增，文件本身可回溯）。

然后**手工补「实现步骤」**：具体到先写哪个文件、方法签名、边界怎么处理。
生成器只能填结构化字段，填不了实现思路。

一个合格的提示词，给一个**没读过需求文档、没参与过任何对话**的 Agent，它能独立做完。
检查方法：把提示词单独贴给另一个会话，问它"你要改哪些文件、怎么验证"，答得上来才算合格。

### 7.3 导入与审核

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

还会做一次**上下文变更检查**：`accept` 时每个节点会记下自己交付物的指纹
（`outputs` / `files_scope` 里真实存在的文件）。如果本节点 `inputs` 里的某个文件
在被依赖节点完成之后被改过，`start` 会拒绝并要求重新评估影响：

```bash
$DF start DF-003 --ack-context --reason "只改了注释，接口未变"
```

确认后理由会写进 `context_acks` 留档。**没声明 `inputs` 的节点不做推断**——
不要因为"仓库里别处改了个文档"就让所有下游返工。

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
$DF sync DF-003        # 只接受 in_progress → needs_review / failed
$DF accept DF-003      # 证据齐全才 completed，并自动解锁下游
```

### 状态约束

`sync` **只允许 `in_progress`**。`pending` / `ready` 必须先 `start`——这不是形式，
`start` 会生成 `attempt_id`，没有它就无法证明报告属于本轮执行。

`start` 输出一行 `attempt_id=xxxxxxxx`。执行 Agent 必须把同一个值写进
执行报告证据块和 `test-report.json`；不一致或缺失，`sync` 直接判 failed。

### 验收门槛（`config/quality-gates.yaml` 可调）

- 执行报告存在，且有 `devflow-execution` JSON 块；`result` 必须是 `success`
- `acceptance` **逐条覆盖**节点的验收标准，每条 `met=true` 且 `evidence` 非空
- `test-report.json` 存在、`node_id` 对得上、`status=passed`
- `commands` 非空，**每条**命令都有整数退出码，且**任何一条非 0 就失败**
- `summary` 自洽：`total = passed + failed + skipped`，`failed=0`，`total>0`
- `cases` 数量与 `summary` 一致，且没有失败用例
- `no_test` 节点必须有 `no_test_reason`
- `modified_files` 必须全部落在 `files_scope` 内

**证据不全就判 failed，不要"先标完成后面补"。** 一旦标完成，下游就会开始动。

### 越界修改

`files_scope` 之外的文件改动不会被"警告放过"：

1. `sync` 把越界项写进节点的 `scope_violations`，状态停在 `needs_review`，退出码 1
2. `accept` 直接拒绝
3. 三条出路：撤掉改动重新同步 / 更新 `files_scope` 并走变更 / 
   `accept --allow-scope --reason "为什么必须改"`（会记进 `scope_waivers` 留档）

路径比较是规范化后的：反斜杠、`./`、`..`、绝对路径、符号链接逃逸都会被处理，
目录匹配要求 `dir/` 前缀，`src/service` 不会放行 `src/service-backup/x.py`。

### 外部 Agent

外部 Agent 也必须先 `start`（主控可以代跑），再按模板产出两个文件。
把报告放进任意目录后：

```bash
$DF sync DF-003 --external --from /path/to/reports
```

它会先校验 `node_id` / `attempt_id` 再把文件搬进节点目录，然后走**同一套**证据、
依赖、范围、Git 校验。没有"外部报告免检"这回事。

### Git 变更核对（可选）

`start` 会记录 Git 基准：HEAD + **执行前就存在的未提交文件**。
`sync` 时计算实际变更，并减掉这些已有修改——用户原来就改了一半的文件，不能算到这个节点头上。

报告里的 `modified_files` 与实际情况不一致时会记录到 `git_discrepancies`：

- `verify_changes: warn`（默认）→ 提示，验收前人工看
- `verify_changes: error` → 阻止验收，确认后用 `accept --ack-changes --reason "..."` 留档

项目不是 git 仓库、或项目目录不是仓库根时自动降级，不阻断流程。

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
$DF change-apply --change CR-001                      # 事务提交，旧计划 superseded
```

`change-apply` 之前必须让用户确认。确认的是**影响范围和返工清单**，不是"要不要改"。

### 提交是事务

`change-apply` 会依次改：新计划、旧计划、`project.json`、CR 状态、看板。
这一串是**事务**：

1. 先校验基准版本：CR 的 `base_plan_version` / `base_requirement_version`
   必须等于当前生效版本，否则**拒绝应用**，要求重新做影响分析
2. 校验目标计划属于本 CR、需求基准存在、结构校验通过
3. 快照 + 写事务日志 → 逐个落盘 → 标记 committed
4. 中途任何异常 → 按快照回滚，项目保持原样，日志留在 `.devflow/.txn/<id>/`

应用前还会检查**节点迁移关系**：

- 旧节点不能凭空消失（不需要就显式 `superseded`）
- 已完成的节点被返工，`remark` 必须写明是哪个 CR 触发的
- 新计划里不能凭空出现 `completed` 节点

这些不满足就拒绝应用——不是"提醒一下"。

出现半提交（比如进程被 kill）时：

```bash
$DF recover            # 列出未完成事务与所处阶段
$DF recover --apply    # 回滚到事务前状态
$DF doctor             # 复查一致性
```

**不允许出现两个 active 计划，也不允许 project.json 指向不存在的版本。**
`doctor` 会检查这一点。

## 12.5 改自己的引擎时先自检

改过 `scripts/` 之后，除了跑单元测试，还要跑一遍端到端：

```bash
$DF self-test              # 临时目录跑完整生命周期 + 单元 + 示例项目集成
$DF self-test --fast       # 只跑端到端，跳过两层测试（调试用）
$DF self-test --keep       # 保留临时目录，方便进去看
$DF self-test --json --out /tmp/self-test.json   # 机器可读结果
```

它在临时目录从 `init` 一路走到 `release-accept`（28 步），不读写任何真实项目。
输出会分开报告三层：单元测试、集成测试（示例项目）、端到端工作流。
任何一层失败都退出码 1——**不要用降低门槛的方式让它变绿**。

## 13. regression · 全量回归

```bash
$DF regression-check      # 前置检查，有阻塞会退出码 1
```

前置检查会**阻断**（不是警告）这些情况：

- 还有 pending / ready / in_progress / needs_review / blocked / failed 节点
- 已完成节点证据不齐（走同一套 `evidence_problems`）
- active 需求没有任何节点覆盖，或覆盖节点都没完成
- 需求只有 `no_test` 节点覆盖（缺可执行验证）
- 需求的验收项在覆盖节点报告里找不到 `met=true` 且带证据的记录（"代码写了"不等于"验证过"）
- 需求被改成 `deferred` / `out_of_scope` / `removed`，但没有 `reason` 和 `confirmed_by`
- 需求被改成 `changed`，但没有 `superseded_by`，或替代需求不存在 / 不是 active

把需求改成非 active 不是逃生门——必须写清原因和谁确认的，`changed` 必须指向真正的替代需求。

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

用 `project.json` 里的 `commands`，把结果写成 **JSON**（`templates/regression-report.json`）：

```bash
$DF regression-import --file regression.json
```

它做三件事：Schema 校验、语义校验（状态与检查项必须自洽）、由 JSON 渲染 Markdown。
`final-report.json` 是事实来源，`final-report.md` 只是展示。

- 任一必需项 `failed` → 整体必须 `failed`
- 必需项 `not_run` / `skipped` → 整体不能是 `passed`
- `passed` 的项必须有命令、退出码 0、证据
- `not_run` 的项必须写原因
- 缺 `config/quality-gates.yaml` 里的 `required_checks` → 拒绝导入

没跑的写 `not_run` + 原因。**不要写 passed。** 旧项目只有 Markdown 时，
`release --legacy-regression` 可以走降级路径，但会在项目档案里留记录。

发现问题：定位到需求和节点 → 生成修复任务 → 修 → 重跑受影响的测试 → 重新导入。

## 14. release · 交付（三段式）

生成报告、满足条件、用户验收，是**三件事**。不要合并。

```bash
$DF release          # 1) 生成交付报告 → release_candidate
$DF release-ready    # 2) 检查交付条件 → release_ready
$DF release-accept --by "张三"   # 3) 用户验收 → accepted
```

### 1) release

汇总需求完成情况、任务清单、验证状态分类（已验证 / 已实现未验证 / 未实现），
生成 `.devflow/releases/release-report.md`，状态置为 `release_candidate`。

它**同时**检查交付门槛：节点是否全部完成、证据是否齐全、回归结论是否为 `passed`、
需求是否都被覆盖。有阻塞就退出码 1，但报告仍然生成（便于对照修）。

注意：`release` **不会**把项目标成已交付。生成报告 ≠ 交付。

### 2) release-ready

再查一遍门槛（状态可能在这期间变化），全部通过才置 `release_ready`。
这是"可以交给用户验收"的状态，不是"已验收"。

### 3) release-accept

必须带 `--by`，必须处于 `release_ready`。置 `accepted` 并记录验收人和时间。

**`accepted` 是终态**：`start` / `sync` / `accept` / `retry` / `regression-check` / `release`
等命令会被直接拒绝（退出码 2），`regression-check` 也不会再把状态改回 `regression`。
需要继续开发必须走变更流程：`change-new` → `change-apply`（生成新计划，状态回 `in_progress`）。

回归结论从 `final-report.json` 读取（旧项目可用 `--legacy-regression` 读 Markdown 的
`结论：passed` 行）。没写结论、写成 `failed`、或者节点没完成，都进不了 `release_ready`。

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
11. 越界修改不许"警告放过"：要么撤回，要么走变更扩范围，要么显式记录豁免。
12. 旧报告不能冒充新执行：`attempt_id` 对不上就判失败。
13. 生成交付报告 ≠ 交付；`accepted` 只能由用户明确验收产生。
14. `accepted` 是终态：继续开发只能走变更流程，不能重跑命令把状态改回去。
15. 不改 `scripts/` 之前先读代码；改完必须跑 `df.py self-test`，不许靠降门槛让它变绿。

## 16. 文件索引

| 需要什么 | 看哪里 |
| --- | --- |
| 业务方法论（需求 / 拆解 / 依赖 / 执行 / 测试 / 变更 / 回归） | 见第 2 节「按需读取」 |
| 数据结构与不变量 | `docs/data-structure.md` |
| 完整流程与示例 | `docs/workflow.md`、`examples/sample-project/` |
| 架构与审计记录 | `docs/architecture.md`、`docs/engineering-audit.md` |
| 配置项 | `config/*.yaml` |
| 模板 | `templates/` |
| 校验 / 看板 / 依赖检查 | `scripts/validate.py`、`render_board.py`、`dependency_check.py` |
