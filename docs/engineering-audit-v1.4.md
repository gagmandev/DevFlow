# DevFlow V1.4 工程审计

- 审计对象：V1.3（commit `4ea4c7c`）当前 main 分支实际代码
- 审计方法：逐项读代码 + 写复现用例 + 修复后回归
- 结论：V1.3 的"硬门槛"仍有多处可以被**报告自身**或**命令入口**绕过；
  交付环节缺少"报告完整性"和"代码快照"两道证据；生产/开发质量档位混用。

## 一、缺陷清单

编号沿用前两轮（P0 / P1 / P2 的内部编号保留在发布说明里），本轮新发现记为 V14-xx。

| 编号 | 位置 | 缺陷 | 风险 | 复现方式（修复前） |
| --- | --- | --- | --- | --- |
| V14-01 | `df.derive_regression_status` | 用报告自己的 `required` 字段推导整体状态 | 报告把 `build.required=false` 就能让失败项不参与推导 | 提交 `build{required:false,status:failed}`，整体仍可 `passed` |
| V14-02 | `_regression_data_problems` | 只检查必需项"存在"，不检查 `required=true` | 同上，且能凭空发明"必需项" | 同上 |
| V14-03 | `df.cmd_sync` | `files_scope` 只校验 Agent 报告的 `modified_files` | 漏报的越界文件不被拦 | 报告只写 `a.py`，实际改了 `b.py`，`accept` 通过 |
| V14-04 | `dfcore.evidence_problems` | `modified_files` 缺失时按"没有改动"处理 | 不写字段即可绕过范围校验 | 证据块不带该字段，`sync` 通过 |
| V14-05 | `dfcore._symlink_escape` | 用字符串包含判断是否在项目根内 | `/tmp/proj-evil` 因含 `/tmp/proj` 被放行 | 项目内符号链接指向 `proj-evil/secret.py`，判定为"在项目内" |
| V14-06 | `df.cmd_regression_import` | 未纳入 `MUTATING_COMMANDS` / `FROZEN_ON_ACCEPTED` | 无锁写入；`accepted` 后还能替换回归依据 | 验收后重新导入，`final-report.json` 被覆盖 |
| V14-07 | `df.cmd_release_ready` | `regression` 状态可直接进入 `release_ready` | 跳过交付报告 | 回归通过后直接 `release-ready` 成功 |
| V14-08 | `df.cmd_release` | 交付报告写死"（待补充…）"占位，且无机器可读元信息 | `release-ready` 无法判断报告是否写完 | 生成报告即"通过" |
| V14-09 | `dfcore._summary_problems` | `cases` 缺失/为空时直接返回 | 新执行可以不给用例明细 | 报告只写 `summary`，`sync` 通过 |
| V14-10 | `df._requirement_evidence_missing` | 只要有一条 `met=true` 就算需求已验证 | 一条需求多条验收标准时只测一条 | REQ-001 两条标准，只证一条仍通过 |
| V14-11 | `df.cmd_regression_import` / `release-*` | 回归报告不绑定代码版本 | 回归后改代码仍能用旧报告交付 | 导入报告后改源码，`release-ready` 仍通过 |
| V14-12 | `df.cmd_change_apply` | `based_on` 缺失或 `change_id=null` 时放行 | 来路不明的计划可被"应用" | 计划不带 `based_on`，`change-apply` 成功 |
| V14-13 | `dfcore.prompt_binding_problems` | 未比较 `prompt_version` | 计划记录了新版本、文件还是旧的 | 手改 `prompt_version` 后仍可 `start` |
| V14-14 | `dfcore.scope_overlap` | 只做字符串/目录比较 | `src/**/*.cs` 与 `src/service.cs` 判为不冲突 | 两个并行节点实际改同一文件仍被允许 |
| V14-15 | `dfcore.parallel_exceptions` | 例外可整组豁免 | 一条例外放过组内全部冲突 | 只写 `group` 不写 `nodes` 即放行 |
| V14-16 | `config/quality-gates.yaml` | `empty_scope: warn` 与 accept 拦截互相矛盾 | 空范围规则不可预测 | 空 `files_scope` 时"提示放行"但 `accept` 失败 |
| V14-17 | `dfcore._LOCKS` | 仅按项目路径做重入判断 | 同进程两个线程被当成同一重入 | 两线程同时写，第二个不等待 |
| V14-18 | `schemas/change.schema.json` | 缺 `applied_at`（V1.3 遗留） | 变更后 `validate` 必报错 | 走一次 `change-apply` 再 `doctor` |

## 二、修复映射

| 编号 | 修复文件 | 关键改动 |
| --- | --- | --- |
| V14-01/02 | `scripts/df.py`、`config/quality-gates.yaml` | `derive_regression_status(checks, required_ids)` 按配置推导；报告降级/缺项/未知必需项一律拒绝 |
| V14-03/04 | `scripts/df.py`、`scripts/dfcore.py` | `sync` 合并"报告声明 ∪ Git 实际"再做范围校验；新执行缺 `modified_files` 判失败；Git 不可用按 `git.required` 处理 |
| V14-05 | `scripts/dfcore.py` | `_symlink_escape` 改用 `commonpath` 祖先判断，处理盘符/大小写 |
| V14-06 | `scripts/df.py` | `regression-import` 加入锁集合与 `accepted` 终态集合；新增"写命令必须在锁集合"守卫测试 |
| V14-07/08 | `scripts/df.py`、`schemas/project.schema.json` | `release` 写 `release-report.json` 元信息并按真实数据生成章节；`release-ready` 只接受 `release_candidate` 且校验章节/版本/占位符 |
| V14-09 | `scripts/dfcore.py`、`config/quality-gates.yaml` | 新执行 `cases` 必填、条数与统计一致 |
| V14-10 | `scripts/dfcore.py`、`schemas/requirements.schema.json`、`templates/*` | `requirement_acceptance` 逐条映射；需求验收标准支持稳定 id；旧字符串格式兼容 |
| V14-11 | `scripts/dfcore.py`、`scripts/df.py` | `source_snapshot()`（git HEAD + 内容指纹 / 文件树哈希）；`regression-check` 记录、`import` 绑定、`release-ready`/`release-accept` 复核 |
| V14-12 | `scripts/df.py` | `based_on.change_id` / `plan_version` 必须与 CR 一致；`base_plan=0` 也参与比较 |
| V14-13 | `scripts/dfcore.py` | `prompt_binding_problems` 纳入 `prompt_version` |
| V14-14/15 | `scripts/dfcore.py`、`schemas/backlog.schema.json` | 通配符交集判定（保守策略）；例外必须 `nodes` 点名两个节点 |
| V14-16 | `config/quality-gates.yaml`、`scripts/dfcore.py` | `empty_scope` 三态（deny / outputs_only / allow），删掉自相矛盾的 warn |
| V14-17 | `scripts/dfcore.py` | 锁键改为 `(路径, 线程 id)`；同线程可重入，不同线程争 OS 锁 |
| V14-18 | `schemas/change.schema.json` | 补 `applied_at`；`base_plan_version` 最小 0 |

## 三、生产/开发档位

| 项 | development（基线） | production |
| --- | --- | --- |
| `git.verify_changes` | warn | error |
| `git.required` | false | true |
| `empty_scope` | outputs_only | outputs_only |
| `regression.require_source_snapshot` | true | true |
| `release.require_release_candidate` | true | true |
| `context.require_contract_bindings` | false | true |
| `node_accept.*` | 全开 | 全开 |

项目通过 `project.json.quality_profile` 选择，`init --profile` 写入，`migrate --profile` 升级；
旧项目没有该字段时按 development 处理，`doctor` 会提示。

## 四、测试

- 引擎：`python -m unittest discover -s tests` → 195 条
- 示例项目：74 条
- 端到端：`python scripts/df.py self-test` → 60 步（主链路 + 并行 + 外部报告）

每个缺陷都有对应测试，测试文件：

- `tests/test_v14_gates.py`：回归必需项、Git 合并范围、release 报告、写命令锁、需求证据、CR 绑定、提示词版本、通配符、源码快照
- `tests/test_p0_hardening.py`：符号链接前缀逃逸、线程锁竞争
- `tests/test_p1_workflow.py`：Git 范围合并的 CLI 行为、报告归档
- `tests/test_p2_optimization.py`：self-test 并行/外部支线、CI 配置

## 五、仍然存在的限制

1. 快照是"文件内容哈希"，能证明"代码没变"，不能证明"外部 Agent 真跑了命令"。
2. 契约绑定只做文件级指纹，不解析 OpenAPI / SQL 语义。
3. 并行例外仍需人工判断，工具只负责"不静默放行"。
4. 单主控架构不变：文件锁防覆盖，不做多主控合并。
5. `self-test` 的并行场景用两个子进程，不覆盖真机多机并发。
