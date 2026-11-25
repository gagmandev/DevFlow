# DevFlow

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
```

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
templates/            10 个文档模板
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
