# DevFlow

> 版本 1.4.1 · 生产质量门禁版（证据硬校验 / Git 实际变更合并校验 / 交付报告强制完整 / 回归绑定源码快照 / 需求逐条验收 / 并行通配符冲突 / development·production 双档位 / self-test 含并行与外部支线）

把一份需求文档变成**能真正执行到交付**的开发流水线。

不是代码生成器，是流程总控：需求确认 → 任务拆解 → 依赖规划 → 节点提示词 → 执行验收 →
变更管理 → 全量回归 → 交付报告。

```bash
python scripts/df.py init --name "项目名"
```

## 解决什么问题

| 现状 | DevFlow |
| --- | --- |
| 人工拆需求，耗时且容易漏 | 按 5 阶段 × 功能域拆，自动检环、算并行 |
| 任务依赖靠脑子记，顺序经常错 | DAG + ready 自动推导，依赖不满足直接拒绝执行 |
| 每个任务手写提示词 | `prompt-init` 生成骨架，模型补实现步骤 |
| 进度散在多个会话 | `.devflow/` 落盘，换 Agent 读文件即可接手 |
| 交付靠"我觉得做完了" | 测试证据 + 验收门槛，缺证据不能标完成 |
| 需求一变全乱 | 版本化变更，影响分析到节点级，旧计划保留 |
| 多 Agent 互相覆盖 | 单写者模型 + 文件锁 + 原子写 |

## 五分钟

```bash
SKILL=~/.workbuddy-ai/skills/devflow
DF="python $SKILL/scripts/df.py"

cd my-project
$DF init --name "我的项目"
# → 分析技术栈，写 .devflow/project.json

# 把需求文档给 Agent："分析这份需求，有歧义列出来"
# → .devflow/requirements/traceability.json + clarification.md

$DF req-confirm --version 1
# 用户确认后

# "拆任务，出计划"
$DF prompt-init --all
$DF plan-import --file backlog.json --version 1
$DF plan-activate --version 1 --by "我"

$DF next
$DF start DF-001
# … Agent 按提示词干活，写两份报告 …
$DF sync DF-001 && $DF accept DF-001

# 全部完成后
$DF regression-check
$DF regression-import --file regression.json   # 真实结果写成 JSON 再导入
$DF release              # 生成报告 → release_candidate
$DF release-ready        # 门槛复核 → release_ready
$DF release-accept --by "我"   # 用户验收 → accepted
```

改过 `scripts/` 之后：

```bash
$DF self-test     # 临时目录跑完整生命周期 + 单元 + 示例项目集成，不碰真实项目
```

## 工程可靠性（V1.1）

V1 的校验层偏软——多个关键约束只打印警告、不改变状态。V1.1 把这些变成硬约束：

| 风险 | 现在的行为 |
| --- | --- |
| 一条测试命令失败、另一条成功 | 判失败（修掉了 `all()` 逻辑漏洞） |
| 缺 `exit_code` / 空 commands | 判失败，不再默认成功 |
| `total=0`、统计对不上、用例有 fail | 判失败 |
| 验收标准没逐条对应 | 判失败 |
| 用上一轮报告冒充本轮 | `attempt_id` 绑定，对不上即失败 |
| 改了 `files_scope` 之外的文件 | 记入 `scope_violations`，`accept` 阻断 |
| 路径 `..` / 绝对路径 / 符号链接逃逸 | 规范化后拒绝 |
| 两个进程同时改状态 | OS 级排他锁（崩溃自动释放），锁内重读 |
| change-apply 中途崩溃 | 快照 + 事务日志 + 自动回滚，`df.py recover` |
| 生成交付报告就被当成已交付 | 三段式：candidate → ready → accepted |
| 需求没覆盖也能进回归 | `regression-check` 直接失败；改状态绕过需要理由和确认人 |
| 回归报告只有 Markdown | `final-report.json` 是事实来源，状态必须与检查项自洽 |
| 外部 Agent 的报告免检 | `sync --external` 只搬文件，之后走同一套校验 |
| 报告声称改了、实际没改 | Git 基准比对，自动排除执行前已有的未提交修改 |
| 并行组靠"同层"猜安全 | 间接依赖 / 文件 / 表 / 接口 / 配置冲突直接阻止激活 |
| 计划变了还在用旧提示词 | 提示词带版本元数据，`start` 对不上就拒绝 |
| 上游交付物被改了，下游照旧开工 | `accept` 记指纹，`start` 比对；不成立就走变更，成立要留理由 |
| 改完引擎靠人肉验证 | `df.py self-test` 临时目录跑完整生命周期（含并行与外部报告），分三层报告 |
| 必需回归项可以自己降级 | 必需集合只认质量配置；降级/缺项/重复一律拒绝 |
| Agent 漏报越界文件 | Git 实际变更与报告合并后再过 `files_scope` |
| 回归跑完又改代码 | 回归报告绑定源码快照；导入/交付时复核，代码一变旧结论作废 |
| 交付报告没写完就交付 | 只接受首次生成的完整报告，章节/版本/占位符都校验 |
| sync 之后偷偷改文件 | `accept` 实时重算 Git 变更并重跑范围校验 |
| accepted 之后还被改写 | 白名单终态，只有 `change-apply` 能退出 |

审计报告见 `docs/engineering-audit.md`、`docs/engineering-audit-v1.4.md`；
版本变化见 `docs/release-notes-v1.4.md`。

## 核心设计

1. **文件驱动**：状态在 `.devflow/`，不在对话里。JSON 是事实来源，Markdown 是视图。
2. **看板由 JSON 渲染**：不手工维护两套状态。
3. **单写者**：只有 `df.py` 写全局计划。执行 Agent 只写自己节点目录。
4. **证据说话**：`test-report.json` + 执行报告里的 `devflow-execution` 块，缺一不可验收。
5. **版本化**：需求、计划、报告都保留历史；返工前自动归档。
6. **零依赖**：Python 3.8+ 标准库，无第三方包，无服务端。

## 目录

```
SKILL.md              主控指令（模式路由 + 每步怎么做）
config/               可调规则：阈值、质量门槛、阶段定义
schemas/              6 个 JSON Schema
templates/            11 个文档模板
references/           7 篇方法论文档
scripts/              df.py（CLI）+ 校验 / 看板 / 依赖检查
examples/sample-project/  真实跑通的示例（含完整 .devflow 与 74 条真实测试）
docs/                 架构、工作流、数据结构、使用说明
tests/                引擎单元测试
```

## 验证情况

- 示例项目从需求分析走到交付报告，全程真实执行：7 个节点 → CR-001 变更 → 8 个节点 → 回归 → 交付。
- 示例项目测试：74 条 `unittest` 全绿（含并发与冻结链路）。
- 引擎单元测试：见 `tests/test_devflow.py`。
- 详细过程：`examples/sample-project/WALKTHROUGH.md`。

## 已知限制

- 不支持多主控并发写（文件锁只防覆盖，不合并冲突）。
- 不做子 Agent 调度，只生成提示词与并行清单。
- 不做代码质量分析（测试通过 ≠ 代码好）。
- 项目状态全量读写，节点上千会变慢。

## 文档

| 想了解 | 看 |
| --- | --- |
| 完整流程 | `docs/workflow.md` |
| 数据怎么存 | `docs/data-structure.md` |
| 为什么这么设计 | `docs/architecture.md` |
| 怎么用 | `docs/usage.md` |
| 示例怎么跑的 | `examples/sample-project/WALKTHROUGH.md` |
