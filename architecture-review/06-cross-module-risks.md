# 06 · 跨模块风险审查

> 每条给出：现象 → 代码证据 → 影响 → 复现思路（静态推导，标注"未执行"）→ 级别。
> 本阶段不运行任何会改状态的命令，复现思路均可在隔离副本上验证。

## A. 需求与 CR

### A1 计划版本可无 CR 直接替换（P0）

- **现象**：`change-apply` 严格校验 CR 绑定，但另一条路 `plan-import` + `plan-activate` 完全不需要 CR。
- **证据**：
  - `df.py:522` 计划 `based_on` 直接取 `raw.get("based_on")`，允许为空；
  - `validate.py:516-523` 只有 `based_on.plan_version` 存在时才跑 `migration_problems`；
  - `df.py:587-640` `plan-activate` 只校验计划自身状态、需求基准存在、validate 通过，**不校验来源与当前项目状态**。
- **影响**：可以新导入一个 v2 计划，悄悄改 `files_scope`、删除节点、把节点标 `completed`，`plan-activate` 后
  项目回到 `planned`，再回归 → 交付。整条链没有 CR、没有影响评估、没有用户确认。这是当前最严重的"门禁可绕过"。
- **复现思路（未执行）**：复制 `backlog-v1.json` → 改 `plan_version=2`、把某节点 `files_scope` 扩到全仓库 →
  `plan-import --version 2` → `plan-activate --version 2` → `validate` 全部通过。
- **级别**：P0

### A2 需求基准内容无指纹（P0）

- **现象**：`confirmed-requirements-vN.md` 由主控/Agent 直接写，CLI 只在确认时看"长度 ≥ 200 + 三个关键词"。
- **证据**：`df.py:435-448`；全仓库没有对该文件做哈希或比对的代码（`grep sha256` 只命中交付物/源码快照）。
- **影响**：改需求文字（例如把"必须冻结积分"改成"不冻结"）而版本号不变 → 回归报告仍绑定 `requirement_version=1`，
  `release` 照样通过。等价于 INV-002 被击穿。
- **复现思路（未执行）**：在示例项目上直接编辑 `confirmed-requirements-v1.md`，`validate` 仍 0 错误。
- **级别**：P0

### A3 需求版本指针可能漂移（P1）

- **现象**：`project.current_requirement_version`、`traceability.json` 内的 `requirement_version`、
  `traceability-vN.json` 三者没有一致性校验。
- **证据**：`df.py:423-426` 两次写；`validate.py:299-304` 只检查文件存在；`grep` 未发现 `traceability["requirement_version"]`
  与 `project` 的比对。
- **影响**：中断或手工修改后，计划/报告绑定的是一个版本、实际读的是另一个版本；`regression-check` 用的是
  `plan.requirement_version`，可能指向不存在的语义。
- **级别**：P1

### A4 同版本需求导入覆盖历史（P1）

- **证据**：`df.py:423` 无 `--force` 守卫、无归档；对比 `plan-import`（`df.py:501-509`）有 `--force` + `.history/`。
- **影响**：`traceability-v1.json` 被静默替换，历史需求内容不可追溯（违反 INV-018）。
- **级别**：P1

### A5 CR 的 `rejected` 状态没有凭据要求（P2）

- **证据**：schema 允许 `rejected`；`validate.py:326-333` 只校验 schema 与 `change_id` 一致；无 reason 要求。
- **影响**：拒绝原因丢失，审计链断裂。
- **级别**：P2

## B. Git 与 Agent

### B1 baseline 就 dirty 的文件被整份剔除（P0）

- **现象**：`git_changes_since` 用 `changes -= protected` 把执行前已 dirty 的文件**全部**减掉。
- **证据**：`dfcore.py:993-999`。
- **影响**：用户开工前改了 `a.py` 的一半，执行期间 Agent 又改了 `a.py` 的越界部分 → 该文件从"实际变更"里消失，
  范围校验看不到它（如果 Agent 也漏报，则完全不可见）。
- **正确做法**：记录 baseline 时对 dirty 文件做内容指纹，事后比较指纹是否变化，而不是整份剔除。
- **级别**：P0

### B2 ignored 文件不可见（P1）

- **证据**：`git status --porcelain -z`（`dfcore.py:961`）默认不列 ignored 文件；`git_changes_since` 没有 `--ignored`。
- **影响**：改 `.env`、构建产物、被 `.gitignore` 的配置 → 既不在报告里，也不在 Git 变更里。若这类文件属于
  "运行必需配置"，交付证据会漏。
- **级别**：P1（Git 语义限制，需要显式策略：禁止改、或要求声明 `ignored_allowlist`）

### B3 并行节点共享工作区，变更无法归属（P1）

- **证据**：`git_changes_since` 只按 baseline 与当前工作区做差集，不区分是哪个 Agent 改的；`accept` 复算同样如此
  （`df.py:1359`）。
- **影响**：A、B 并行时，B 的越界改动会出现在 A 的 `scope_violations` 里（假阳性），或反过来被 A 的
  `--allow-scope` 豁免掩盖（假阴性）。
- **现状**：靠"不同节点不同 `files_scope`"降低概率，但没有机制保证。
- **级别**：P1

### B4 非 Git 项目在 development 下静默降级（P2）

- **证据**：`git_snapshot` 返回 `None`（`dfcore.py:952-967`）；`evaluate_node_changes` 里 `unavailable=True`，
  仅当 `git.required` 才阻断（`df.py:1148-1200`）。
- **影响**：development 档位下"实际变更"完全不做校验，只剩报告自述。
- **级别**：P2（production 已强制）

### B5 符号链接与路径逃逸（已覆盖）

- **证据**：`_symlink_escape`（`dfcore.py:686-708`）、`normalize_rel`（`dfcore.py:618-638`）、
  `ScopeTest`（含 `proj` vs `proj-evil`）。
- **结论**：现阶段未发现新的绕过；但只对"被检测到的路径"生效，若文件从未出现在报告/Git 变更里，检查不会触发。

## C. 状态机与权限

### C1 项目状态无迁移表（P0）

- **证据**：`PROJECT_STATUSES`（`dfcore.py:29-43`）只有枚举；8 处直接赋值（`df.py` 468/628/1080/1736/2006/2435/2594/2627）；
  没有 `transition_project()`。
- **影响**：
  - `plan-activate` 可从 `regression`/`release_candidate` 回退到 `planned`（`df.py:628`）；
  - `release` 可从任意状态跳到 `release_candidate`（`df.py:2246` 只拒 accepted）；
  - `analyzing`/`clarifying` 是死状态（无写入点）。
- **级别**：P0

### C2 accepted 之外的写命令缺少"按状态"的准入（P1）

- **现象**：只有 accepted 有统一守卫；其他状态靠各命令自己检查（有的只检查节点状态，不检查项目状态）。
- **证据**：`main()` 只在 `accepted` 时调用 `accepted_denial`（`df.py:3550-3565`）；`cmd_release` 只拒 accepted。
- **影响**：状态与命令的组合空间没有被穷举过，容易出现"某状态下的意外写入"。
- **级别**：P1

### C3 `doctor` 写依赖图但不在锁集合（P2）

- **证据**：`df.py:3338` 调用 `_run_dependency_check(..., --write)`；`MUTATING_COMMANDS`（`df.py:47`）不含 `doctor`。
- **影响**：并发 `doctor` 与 `plan-activate` 同时写 `dependency-graph-vN.md` 可能互相覆盖（派生文件，影响小）。
- **级别**：P2

### C4 `init --force` 在已有项目上不落盘（P2）

- **证据**：`df.py:287-297` 修改本地 `project` 字典后没有 `save()`；只有 `decisions.md`/`.gitignore` 会写。
- **影响**：用户以为改了 `--type`/`--profile`，实际没变（也不会报错）。
- **级别**：P2

### C5 执行凭证不可强绑定到"人"（P2）

- **证据**：`release-accept --by` 只是字符串（`df.py:2627-2629`）；`accepted_by` 无身份校验。
- **影响**：可以把任意名字写成验收人。
- **级别**：P2（单机工具的现实约束）

## D. 回归与发布

### D1 回归只绑定需求版本号，不绑定内容（P0）

- 见 A2 / INV-020。`_regression_data_problems` 只比 `requirement_version`（`df.py:2100-2105`）。

### D2 `release` 在门禁失败时仍改状态（P1）

- **证据**：`df.py:2435` 无条件 `status = release_candidate`，之后才 `if blockers: return 1`（`df.py:2447`）。
- **影响**：项目看起来"已交付候选"，实际门禁未过；下游 `release-ready` 会拦住，但状态语义被污染。
- **级别**：P1

### D3 交付报告与回归结果可能不同步（已缓解）

- **证据**：`release_report.json` 记录生成时的 `blockers` 与版本（`df.py:2422-2432`）；`release-ready` 重新跑
  `delivery_blockers` + 报告校验（`df.py:2584-2598`）。
- **剩余风险**：报告正文由生成器写，用户可在 `release-ready` 之前手工改正文（改完仍可能通过章节校验）。
  系统无法区分"生成器写的"与"人手改的"（无签名/哈希）。
- **级别**：P2

### D4 `release-accept` 里有一段不可达代码（P2）

- **证据**：`df.py:2604-2607` 判断 `status == "accepted"` 后返回 0；但 `main()` 的 accepted 守卫会先拒绝
  `release-accept`（`ACCEPTED_ALLOWED` 不含它）。该分支在 CLI 下不可达。
- **影响**：维护误导（读代码以为可以重复验收）。
- **级别**：P2

### D5 交付门禁的"证据真实性"上限（P0，设计性）

- 见 INV-023。当前所有证据都是 Agent 自述 + 自洽校验；`source_snapshot` 只能证明"代码没变"，不能证明
  "命令真的跑过"。这是架构层面的硬限制，需要产品决策（是否接受"自证 + 抽查"）。

## E. 并发与事务

### E1 只有 change-apply 有事务（P1）

- **证据**：`Transaction` 使用点仅 `df.py:1706`（`grep -n "core.Transaction("`）；`req-import`/`release`/
  `regression-import` 都是多次 `atomic_write_json`。
- **影响**：中断后可能出现"指针已更新、版本文件未写"或"报告已写、状态未改"，只能靠 `doctor` 发现部分问题。
- **级别**：P1

### E2 锁覆盖与读改写（部分）

- **证据**：`main()` 在 `MUTATING_COMMANDS` 内加锁并重读 project（`df.py:3550-3565`）；`cmd_start` 在锁内重读计划。
- **缺口**：`doctor`（写图）、`init`（写 `.gitignore`）不在锁内；只读命令不持锁，可能读到"正在写"的中间态
  （原子写保证单文件完整，但跨文件组不保证）。
- **级别**：P2

### E3 跨机器/共享目录（无法确认）

- 锁是 `msvcrt.locking` / `fcntl.flock`；在 NFS/SMB 上语义不保证。代码未检测文件系统类型。
- **级别**：P2（当前定位是单机）

## F. 数据与报告一致性

### F1 示例项目无法用当前代码复现（P1）

- **证据**：`examples/sample-project/.devflow/project.json` 为 `schema_version=1.1`、`status=accepted`；
  目录缺 `regression/final-report.json`、`releases/release-report.json`，也没有 legacy 标记。
- **影响**：新人拿示例做基线时，`release-ready` 会失败；示例测试只跑 `validate` 与业务单测（
  `examples/sample-project/tests/`），不覆盖交付门禁。
- **级别**：P1

### F2 看板不展示验收拦截原因（P2）

- **证据**：`accept` 拒绝路径只 `save_plan`（`df.py:1366`），不 `refresh`；看板无 `scope_violations` 字段。
- **影响**：人从看板看不出"为什么没验收"。
- **级别**：P2

### F3 `sessions/` 目录无写入者（P2）

- **证据**：`Paths.sessions`（`dfcore.py:555`）+ `init` 建目录（`df.py:253`）；`grep sessions` 无其他写入。
- **影响**：文档/结构承诺的"会话记录"实际不存在（文档声明，代码未证实）。
- **级别**：P2

## 汇总

| 级别 | 数量 | 编号 |
| --- | --- | --- |
| P0 | 6 | A1 A2 B1 C1 D1 D5 |
| P1 | 8 | A3 A4 B2 B3 C2 D2 E1 F1 |
| P2 | 11 | A5 B4 C3 C4 C5 D3 D4 E2 E3 F2 F3 |
| 合计 | 25 | （B5 为"已覆盖"，不计入） |
