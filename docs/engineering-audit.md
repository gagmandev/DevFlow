# DevFlow 工程质量审计（V1 → V1.1）

- 审计时间：2026-11-25
- 审计范围：`SKILL.md`、`scripts/`、`schemas/`、`config/`、`templates/`、`references/`、`tests/`、`examples/sample-project/`
- 结论：架构成立（文件驱动 + 单写者 + 证据门槛），但**校验层偏软**——
  多个关键约束只打印警告、不改变状态，等于没有约束。

## 一、当前架构

```
SKILL.md            主控：16 个模式路由
df.py               状态机 CLI（22 个命令）
dfcore.py           路径 / 原子 IO / 配置 / 状态机 / 证据校验
validate.py         Schema 子集校验 + 语义校验
render_board.py     JSON → Markdown 看板
dependency_check.py 环检测 / 分层 / 关键路径
schemas/            6 个 JSON Schema
```

数据流：`df.py` 命令 → 读 `project.json` + `backlog-vN.json` → 改内存 → `atomic_write` 落盘 →
重算 ready → 渲染看板。执行 Agent 只写 `nodes/<ID>/` 下的两份报告。

## 二、已确认的缺陷

| 编号 | 位置 | 缺陷 | 影响 |
| --- | --- | --- | --- |
| D-01 | `dfcore.evidence_problems` | 用 `all(exit_code != 0)` 判断命令失败，只有**全部**失败才报错 | 一条命令失败、另一条成功 → 判定通过 |
| D-02 | `dfcore.evidence_problems` | 缺 `exit_code` 时 `int(c.get("exit_code", 0))` 默认 0 | 没跑命令也能通过 |
| D-03 | 同上 | 不校验 `summary` 内部一致性（total/passed/failed/skipped） | 统计造假可过 |
| D-04 | 同上 | 不校验 `cases` 与 `summary` 一致 | `status=passed` 但有用例 fail 可过 |
| D-05 | 同上 | `total=0` 视为通过 | 空测试套件可验收 |
| D-06 | 同上 | 不校验执行报告的 `acceptance` 是否覆盖节点验收标准 | 验收标准没做也能过 |
| D-07 | 同上 | `result=failed` 不阻止验收（只拦了 `blocked`） | 失败报告 + passed 测试可过 |
| D-08 | 同上 | 报告与执行尝试无绑定 | 上一轮报告可冒充本轮 |
| D-09 | `df.py cmd_sync` | `out_of_scope_files` 只打印警告 | 越界修改照样能 accept |
| D-10 | `dfcore.out_of_scope_files` | 字符串前缀匹配 + 未处理 `..` / 绝对路径 / 符号链接 | `src/service` 放行 `src/service-backup/x.py` |
| D-11 | `dfcore.locked` | 按锁文件年龄判陈旧；释放时 `unlink` | 误删他人锁；崩溃后 30s 空窗；无归属校验 |
| D-12 | `df.py` 各命令 | 先 `ctx()` 读状态再加锁（且多数命令根本没用锁） | 并发丢失更新 |
| D-13 | `df.py cmd_change_apply` | 依次写 4 个文件，中途异常留下半提交 | 两个 active 计划 / project 指向不存在版本 |
| D-14 | `df.py cmd_release` | 生成报告即 `status=released` | 报告存在 ≠ 用户验收 |
| D-15 | `df.py cmd_release` | 只检查 Markdown 存在与长度 | 回归失败也能出报告 |
| D-16 | `df.py cmd_regression_check` | `uncovered` 只打印，不进退出码 | 需求未覆盖也能进回归 |
| D-17 | `df.py cmd_sync` | `ready` 状态可隐式转 `in_progress` 后同步 | 绕过依赖检查 |
| D-18 | `schemas/project.schema.json` | 状态枚举无交付细分 | 无法表达"报告已生成但未验收" |

## 三、修改涉及的文件

| 文件 | 改动 |
| --- | --- |
| `scripts/dfcore.py` | 新增 OS 级文件锁、路径规范化与 scope 匹配、严格证据校验、事务快照 |
| `scripts/df.py` | 所有写命令改为"加锁 → 重读 → 改"；sync/accept 严格化；change-apply 事务化；release 三段式 |
| `scripts/validate.py` | 传入 plan 上下文做版本绑定校验；scope 空值策略提示 |
| `schemas/project.schema.json` | 新增 `release_candidate` / `release_ready` / `accepted`，`released` 降级为兼容别名 |
| `schemas/node.schema.json` | 新增 `current_attempt_id` / `attempt_number` / `scope_violations` / `scope_waivers` |
| `schemas/test-report.schema.json` | 新增 `plan_version` / `requirement_version` / `attempt_id` |
| `config/quality-gates.yaml` | 新增 `require_no_skipped` / `require_acceptance_evidence` / `out_of_scope: error` |
| `tests/test_devflow.py` | 新增证据、scope、锁、事务、交付状态测试 |
| `docs/*`、`SKILL.md`、`references/release-rules.md` | 同步状态机与新命令 |

## 四、数据兼容风险

| 风险 | 处理 |
| --- | --- |
| 旧 `project.status = released` | 读取时映射为 `accepted`；`df.py migrate` 显式改写 |
| 旧节点无 `current_attempt_id` | 视为"历史节点"，跳过 attempt 绑定校验；新 `start` 才写入 |
| 旧 `test-report.json` 无 `attempt_id` | 仅对**新尝试**强制；已 completed 的历史节点不受影响 |
| 旧 `backlog-vN.json` 无新字段 | Schema 中全部为可选；`plan-import` 归一化时补默认值 |
| 旧回归 Markdown 报告 | `release` 仍接受 Markdown 作为降级依据，但要求包含结论段；JSON 校验在 P1 实现 |

## 五、修复方案（本轮 P0）

1. **证据**：重写 `evidence_problems`，逐项硬校验；新增 `attempt_id` 绑定。
2. **范围**：`normalize_rel` + `scope_match`（支持目录、精确文件、`*`/`**` 通配），
   拒绝绝对路径、`..`、符号链接逃逸；越界 → `scope_violations` → accept 阻断。
3. **锁**：`msvcrt.locking` / `fcntl.flock` 的 OS 级排他锁，进程退出自动释放；
   进程内可重入；锁文件保留不删除；写命令统一"加锁 → 重读 → 修改 → 落盘"。
4. **事务**：`change-apply` 走快照 + 日志 + 回滚；`df.py recover` 处理未完成事务。
5. **交付**：`release` → `release_candidate`；`release-ready` 检查条件；`release-accept` 才 `accepted`。

## 六、测试方案

| 层 | 覆盖 |
| --- | --- |
| 单元 | 证据 12 类反例、scope 8 类路径、状态机迁移、事务回滚 |
| 并发 | 8 个进程同时 start 不同节点，断言无丢失更新；锁占用超时报错 |
| 端到端 | 现有 `LifecycleTest` 扩展为 start → sync → accept → regression → release → release-ready → release-accept |
| 回归 | 引擎 37 条 + 示例项目 74 条必须继续全绿 |

## 七、不在本轮范围

P1（回归 JSON 报告、CR 版本冲突检测、并行安全、提示词版本绑定、Git 变更核对、sync 外部导入）
与 P2（SKILL.md 瘦身、上下文变更检测、self-test）留到后续轮次。
