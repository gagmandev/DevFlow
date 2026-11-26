# 04 · 权限边界

> 先给结论：**DevFlow 没有操作系统级沙箱**。所谓"权限"由三部分组成：
> ① CLI 是否允许某命令执行（`MUTATING_COMMANDS` / `ACCEPTED_ALLOWED` / 前置校验）；
> ② CLI 是否接受某份数据（Schema + 语义校验）；
> ③ 事后能否发现越界（Git 实际变更 + `files_scope` + 符号链接检查）。
> 执行 Agent 在文件系统层面**能**写任何文件；系统靠 ②③ 兜底，而不是靠"写不进去"。

## 一、角色定义

| 角色 | 实际身份 | 能做什么 | 不能做什么（约定） |
| --- | --- | --- | --- |
| 主控 Agent | 调用 `df.py` 的 AI | 执行所有 CLI 命令、写需求/计划/CR 文档、验收节点 | 不应手改 `backlog-vN.json`（但技术上可行） |
| 执行 Agent | 被投喂 `prompt.md` 的 AI | 改 `files_scope` 内业务代码、写本节点两份报告、跑测试 | 不应改全局状态、其他节点、需求基准 |
| CLI / 状态机 | `scripts/df.py` | 读写全部 `.devflow` 状态、加锁、事务、校验 | 不执行业务代码、不生成测试证据 |
| 文件系统 / Git | OS + git | 提供原子写、文件锁、变更检测 | 不做业务判断 |
| 用户 | 人 | 确认需求、批准计划、验收交付、决策 CR | — |

## 二、角色 × 数据 × 操作矩阵

图例：**✓** 允许且被强制；**△** 允许但只靠约定/事后校验；**✗** 应禁止（含"CLI 拒绝"或"约定禁止"）。

| 数据 | 主控 | 执行 Agent | CLI | 用户 | 强制手段 |
| --- | --- | --- | --- | --- | --- |
| `project.json` | ✓ 只能通过 `init/project-set/migrate/change-apply/release-*` | ✗ | ✓ 独占 | ✗（只读） | `PROJECT_PROTECTED_FIELDS`（`df.py:98`）+ 命令前置校验 |
| `requirements/traceability*.json` | ✓ `req-import` | ✗ | ✓ | ✗ | Schema + `acceptance_spec_problems`（`dfcore.py:1898`） |
| `confirmed-requirements-vN.md` | △ 直接写文件 | ✗ | ✗（只读校验） | △ 口述 | **无强制**：`req-confirm` 只查存在/长度/关键词（`df.py:435-448`） |
| `plans/backlog-vN.json` | ✗ 应只用 CLI | ✗ | ✓ 独占 | ✗ | 无文件级防篡改；依赖"只有 CLI 写"的约定 |
| `plans/board-vN.md` / `dependency-graph-vN.md` | △ 可重渲染 | ✗ | ✓ 渲染 | ✗ | 派生文件；手改会被下次 `refresh` 覆盖 |
| `nodes/<ID>/prompt.md` | ✓ `prompt-init` | ✗（只读） | ✓ | ✗ | 版本头 5 项绑定（`dfcore.py:1258`） |
| `nodes/<ID>/execution-report.md` | △ 可代写 | **△ 直接写** | ✗（只读） | ✗ | 事后校验：`evidence_problems`（`dfcore.py:2086`） |
| `nodes/<ID>/test-report.json` | △ 可代写 | **△ 直接写** | ✗ | ✗ | 同上（统计自洽、`attempt_id` 绑定） |
| `changes/CR-xxx/change-request.json` | ✓ `change-new/set` | ✗ | ✓ | ✗ | Schema + `change-apply` 基准/绑定校验 |
| `changes/CR-xxx/*.md` | △ 直接写 | ✗ | ✗ | ✗ | 无（人读） |
| `regression/final-report.json` | ✗ | ✗ | ✓ `regression-import` | ✗ | Schema + 必需项 + 快照 + 版本绑定 |
| `releases/release-report.{md,json}` | ✗ | ✗ | ✓ `release` | ✗ | `release-ready` 校验章节/版本/占位符 |
| `.devflow/.lock` / `.txn/*` | ✗ | ✗ | ✓ 独占 | ✗ | `ProjectLock` / `Transaction` |
| 业务代码 | ✗（除紧急修复） | ✓ 仅 `files_scope` | ✗ | ✓ | Git 实际变更 ∪ 报告 → `scope_violations`（`dfcore.py:710`） |
| `.gitignore` / 构建产物 | △ | △ | ✗ | ✓ | **无强制**（ignored 文件不在 Git 变更里） |

### 关键缺口（按严重度）

1. **需求基准文档没有写保护**（`confirmed-requirements-vN.md`）：任何人可改内容不改版本，回归/交付都不会失效。
2. **计划文件没有防篡改**：`backlog-vN.json` 手改后 `validate` 不会发现（没有内容哈希、没有签名）。
3. **ignored 文件无法归属**：`git status --porcelain` 不列 ignored 文件（`dfcore.py:952-967`），
   所以"改了一个被 ignore 的配置文件"既不会被报告，也不会被 CLI 发现。
4. **执行证据是"自证"**：`test-report.json` 由 Agent 写，CLI 只能检查自洽性（统计、退出码、`attempt_id`），
   无法证明命令真的跑过。

## 三、命令级权限（谁可以调用什么）

| 命令组 | 命令 | 谁调用 | 前置校验 |
| --- | --- | --- | --- |
| 初始化 | `init` | 用户/主控 | 目录可写；已存在需 `--force` |
| 档案 | `project-set` | 主控 | 字段白名单 + 档位受控（`df.py:330`） |
| 需求 | `req-import` / `req-confirm` | 主控 | Schema + 验收标准 + 追踪表复查；accepted 拒绝 |
| 计划 | `plan-import` / `plan-activate` | 主控 | 结构校验；**激活无 CR/状态前置（B2/B3）** |
| 提示词 | `prompt-init` | 主控 | 计划版本；`--force` 归档 |
| 执行 | `start` | 主控（可代执行 Agent 触发） | 计划 active、依赖完成、提示词版本、上下文变更 |
| 同步/验收 | `sync` / `accept` | 主控 | 证据 + 实时 Git + 范围（`df.py:1220`、`1341`） |
| 异常 | `block` / `fail` / `retry` / `cancel` / `supersede` | 主控 | 状态机 + 尝试次数 |
| 变更 | `change-new` / `change-set` / `change-apply` | 主控 | CR 状态 + 基准 + `based_on` + 迁移 + 事务 |
| 回归 | `regression-check` / `regression-import` | 主控 | 无 open 节点 + 报告自洽 + 快照 |
| 交付 | `release` / `release-ready` / `release-accept` | 主控 / 用户 | 门槛 + 报告完整 + 快照 + 状态 |
| 维护 | `recover` / `migrate` / `doctor` / `validate` | 主控/用户 | 事务存在 / schema 版本 / 只读 |

**accepted 白名单**（`df.py:78`）：`status/next/task/validate/doctor/self-test/board/graph/
change-new/change-set/plan-import/prompt-init/change-apply/recover`。其余写命令一律退出码 2。

## 四、文件系统与 Git 的边界

### 4.1 路径安全

| 攻击面 | 实现 | 证据 |
| --- | --- | --- |
| 绝对路径 / `..` | `normalize_rel` 拒绝（`dfcore.py:618`） | `_ABS_PATH_RE`、`..` 检查 |
| 目录前缀逃逸（`src` vs `src-evil`） | 要求 `dir/` 前缀；`scope_match`（`dfcore.py:676`） | `ScopeTest.test_directory_prefix_cannot_escape` |
| 符号链接逃逸 | `realpath` + `commonpath` 祖先判断（`dfcore.py:686`） | `ScopeTest.test_symlink_prefix_sibling_escape` |
| 通配符绕过 | `_glob_to_regex` + 否定前瞻（`dfcore.py:640`） | `ScopeTest.test_double_star_glob` |
| 不存在的路径 | 检查最近的已存在祖先（`dfcore.py:692-700`） | 同上 |

### 4.2 真实变更识别

- 基准：`git_snapshot`（HEAD + dirty 列表，`dfcore.py:952`）。
- 差集：`git_changes_since`（`dfcore.py:970`）：`当前 dirty ∪ (HEAD..工作区 diff)`，再减去 baseline dirty。
- 归属：**不区分 Agent**；并行节点共享工作区时，A 的改动会出现在 B 的"实际变更"里。
- 盲区：
  1. baseline 就 dirty 的文件，执行期间再改 → 被整份剪掉（`dfcore.py:993`）；
  2. ignored 文件 → 不可见；
  3. 非 Git 仓库 / 项目不是仓库根 → 返回 `None`（production 下阻断验收）。
- 证据：`GitScopeTest`、`AcceptLiveGitTest`、`SourceSnapshotTest`。

### 4.3 失败恢复

| 场景 | 机制 | 证据 |
| --- | --- | --- |
| 多文件提交（仅 change-apply） | 快照 + journal + 回滚 | `dfcore.py:781`、`df.py:1706` |
| 进程崩溃 | `.txn/*` 标记 open → `recover` 回滚 | `df.py:2641` |
| 其他多文件写（req-import / release / regression-import） | **无 journal** | 见 `02-data-model.md` G2/G4/G5 |
| 锁 | OS 文件锁 + 每次 `ProjectLock` 不删锁文件 | `dfcore.py:200-355` |

## 五、权限边界层面的结论

1. **状态写权限收得比较紧**：所有 project/plan 状态字段的写入都在 CLI 内，且 accepted 白名单默认拒绝。
2. **证据与需求基准的写权限基本没收**：靠"事后校验 + 人读"，这是当前最大的权限缺口（与 INV-002/016/021 对应）。
3. **文件系统层没有隔离**：执行 Agent 技术上可改任何文件；`files_scope` 是**检测**而不是**阻止**。
   因此"多 Agent 隔离"必须靠工作区隔离（每节点一个 clone/worktree）才能成立，当前实现不支持。
4. **Git 归属**是并行协作的硬瓶颈：没有 per-node 工作区，`git_changes_since` 的结果会互相污染。
