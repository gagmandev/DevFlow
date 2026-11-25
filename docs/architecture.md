# 架构说明

## 一、DevFlow 是什么

一个**工作流总控 Skill**，不是代码生成器，也不是项目管理平台。

它解决的问题：需求拆解靠人、依赖靠记、进度散在多个会话里、验收没有标准。
它提供的东西：确认过的需求基准、可执行的 DAG 计划、能独立投喂的节点提示词、
带证据的验收、可追溯的变更、诚实的交付报告。

## 二、三层职责

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| 主控 Skill（你） | 需求分析、拆解、计划、看板、验收、变更、回归组织、交付 | 写业务代码 |
| Code Agent | 读提示词、改代码、写测试、跑测试、出报告 | 改全局计划、改其他节点 |
| 开发者 | 确认业务规则、技术选型、任务计划、重要变更、人工决策 | 重复劳动 |

这条边界是硬划的。主控一旦开始写业务代码，就没人管状态一致性了。

## 三、为什么是文件驱动

Agent 的对话记忆不可靠：换会话就丢，长对话会截断，并行会话互相看不见。

所以所有状态落盘到项目根的 `.devflow/`：

- **JSON 是唯一事实来源**：`project.json`、`backlog-vN.json`、`traceability.json`
- **Markdown 是给人看的视图**：需求基准、看板、报告
- **看板由 JSON 渲染**，不手工维护——两份状态一定会漂移

好处：

- 换 Agent / 换会话能无缝接手
- 状态能进 Git，变更可 review
- 出问题能 diff 出"哪一步改了什么"

代价：文件会多。用 `.devflow/nodes/<ID>/` 按节点分目录，把噪音隔离开。

## 四、为什么没有数据库 / 服务端

- 装一个 Skill 不该要求用户先起一个服务
- 项目状态量很小（几十个节点、几百 KB），文件足够
- JSON 可读、可 diff、可手改（应急时）
- 没有并发写需求：单写者模型（只有主控写）

如果项目规模大到文件撑不住（几千节点），说明拆解粒度有问题，不是存储的问题。

## 五、模块划分

```
SKILL.md              主控指令：模式路由 + 每步怎么做
config/               可调规则（阈值、门槛、阶段定义）
  schemas/              7 个 JSON Schema，结构校验的唯一依据
  templates/            11 个文档模板（需求、提示词、报告、变更、回归、交付）
  references/           7 篇方法论文档（拆解、依赖、测试、变更……）
  scripts/
  dfcore.py           核心库：路径、原子 IO、配置、状态机、证据校验、Git 核对、并行冲突
  df.py               CLI：init / status / next / start / sync / accept / change / regression / release
  validate.py         JSON Schema 子集校验 + 语义校验（环、依赖、状态、覆盖、并行冲突）
  render_board.py     JSON → Markdown 看板
  dependency_check.py 环检测、分层、并行组安全、关键路径、不可达节点
examples/sample-project/  真实跑通的示例（含完整 .devflow 与真实测试）
docs/                 本目录
```

`dfcore.py` 是单一实现，其他脚本都 import 它。避免"看板脚本和校验脚本对状态理解不一致"。

## 六、状态机

9 个状态，迁移关系写死在 `dfcore.TRANSITIONS`：

```
pending → ready → in_progress → needs_review → completed
                     ↓              ↓
                  failed / blocked ←┘
任意状态 → cancelled / superseded（终态）
```

关键约束：

- `ready` 必须所有依赖 `completed`（由 `recompute_ready` 自动算，不手填）
- `completed` 必须有执行报告 + 测试证据（`evidence_problems` 把关）
- `completed` 只能通过 `superseded` 离开（保护已完成工作）
- 返工要 `retry` / 新版计划重建，不能直接把 completed 改回 in_progress

## 七、一致性保障

| 风险 | 措施 |
| --- | --- |
| 多个 Agent 同时写计划 | 单写者模型 + OS 级排他锁（`msvcrt.locking` / `fcntl.flock`），进程崩溃自动释放 |
| 加锁前读到旧状态 | 所有写命令统一在锁内重新读取 `project.json` 与 `backlog` |
| 写到一半崩溃 | 临时文件 + `os.fsync` + `os.replace` 原子写 |
| 多文件提交中途失败 | 快照 + 事务日志 + 回滚；`df.py recover` 处理残留事务 |
| 看板与 JSON 不一致 | 看板只由 `render_board` 生成，每次状态变更自动重渲染 |
| 结构写错 | 7 个 Schema + 语义校验，导入时就拦 |
| 依赖成环 | 导入时检测，有环拒绝 |
| 证据造假 | 机器读 `test-report.json` + 执行报告证据块；统计自洽、命令退出码、验收逐条对应 |
| 用旧报告冒充本轮 | `attempt_id` 绑定到执行尝试 |
| 越界修改 | `files_scope` 规范化匹配，越界进 `scope_violations`，验收阻断 |
| 历史被覆盖 | 版本化文件 + 返工前归档到 `history/` |
| 报告被当成交付 | 三段式状态：candidate → ready → accepted，`accepted` 是终态 |
| 并行组靠"同层"猜 | `dfcore.parallel_issues` 统一判定依赖可达性与资源冲突 |
| 上游产物改了、下游照旧开工 | `accept` 记交付物指纹，`start` 比对声明的 `inputs` |
| 文档和代码漂移 | `tests/test_docs_consistency.py` + `df.py self-test` |
| 回归后代码又改了 | `regression-check` 记录源码快照，导入/交付时复核 |
| 交付报告没写完就交付 | `release-report.json` 元信息 + 章节/占位符校验 |
| 门禁一套用到底 | `quality_profile`：development / production 两档 |

## 八、扩展点

不需要改代码就能改的：

- 阶段定义、优先级、拆解阈值 → `config/task-rules.yaml`
- 验收门槛、覆盖率要求 → `config/quality-gates.yaml`
- 重试次数、并行上限 → `config/defaults.yaml`
- 文档格式 → `templates/*.md`
- 提示词结构 → `templates/node-prompt.md`（`prompt-init` 按它生成）

需要改代码的（尽量别）：

- 新增状态
- 新增 Schema 字段
- 换存储方式

## 九、已知限制

1. **不支持多主控并发**。文件锁只能防覆盖，不能合并冲突。多个人同时改计划会打架。
2. **不做子 Agent 调度**。是否支持并行取决于宿主；DevFlow 只生成提示词和并行清单。
3. **不做代码质量分析**。测试通过不等于代码好，那是 review 的事。
4. **不做需求真伪判断**。用户说要什么就是什么，DevFlow 只负责问清和落实。
5. **JSON 全量读写**。节点上千会变慢，但那时该换工具了。
