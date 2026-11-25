# DevFlow v1.4 发布说明

> 主题：**生产质量门禁加固**。不新增业务功能，目标是让"能通过验收"等价于
> "证据真实、范围合规、代码未变、交付完整"。

## 一、这轮修了什么

### P0：能绕过生产门禁的问题

| 编号 | 问题 | 现在的行为 |
| --- | --- | --- |
| P0-01 | 报告可把必需回归项标成 `required=false` | 必需集合由 `quality-gates.yaml` 决定；降级、缺项、重复、未知必需项一律拒绝导入 |
| P0-02 | 只校验 Agent 报告的 `modified_files` | 与 Git 实际变更**合并**后再过 `files_scope`；新执行缺该字段直接判失败；Git 不可用按 `git.required` 处理 |
| P0-03 | 符号链接用字符串前缀判断 | 改用真实路径祖先关系；`proj-evil` 这种同名前缀目录不再放行 |
| P0-04 | `regression-import` 无锁、`accepted` 后仍可导入 | 纳入写锁与终态集合；并发导入不再覆盖；新增"写命令必须加锁"守卫测试 |
| P0-05 | 可从 `regression` 直接 `release_ready` | 必须先生成完整交付报告；报告要过章节、版本、占位符校验 |

### P1：回归证据与需求追踪

| 编号 | 问题 | 现在的行为 |
| --- | --- | --- |
| P1-01 | 新执行可以不给用例明细 | `cases` 必填，条数 = `summary.total`，pass/fail/skip 与汇总一致 |
| P1-02 | 一条需求只验证一条标准也能过 | 执行证据新增 `requirement_acceptance`，**逐条**对应需求验收标准；需求验收标准支持稳定 id，旧字符串格式兼容 |
| P1-03 | 回归报告不绑定代码版本 | `regression-check` 记录源码快照（git HEAD + 内容指纹 / 文件树哈希）；导入、`release-ready`、`release-accept` 都复核 |
| P1-04 | 计划可以不带 `based_on` 被应用 | 必须 `based_on.change_id == CR` 且 `based_on.plan_version == CR.base_plan_version`；`base_plan=0` 也参与比较 |
| P1-05 | 提示词版本不参与绑定 | `start` 同时核对 `project_id / node_id / plan_version / requirement_version / prompt_version` |
| P1-06 | 通配符范围冲突检测不到 | `src/**/*.cs` 与 `src/service.cs` 判为冲突；无法证明不相交时按冲突处理 |
| P1-07 | 空 `files_scope` 语义自相矛盾 | 统一为 `deny / outputs_only / allow` 三态，默认 `outputs_only`（只允许本轮新增的 `outputs` 文件） |

### P2：遗留项与自动化

| 编号 | 内容 |
| --- | --- |
| P2-01 | 契约级绑定：`contract_bindings` 把 `api_contracts` / `db_objects` 落到文件路径；accept 记录契约文件指纹，下游 start 比对 |
| P2-02 | `self-test` 增加并行执行与外部报告两条支线（含 5 类错误路径），共 60 步 |
| P2-03 | 新增 `.github/workflows/ci.yml`：Linux + Windows × Python 3.8 / 3.12 |
| 额外 | 质量档位 `development` / `production`；锁按线程区分（同线程可重入，跨线程争 OS 锁） |

## 二、新增 / 变化的命令与配置

| 命令 / 配置 | 说明 |
| --- | --- |
| `df.py init --profile production` | 初始化时写入质量档位 |
| `df.py project-set --profile production` | 切换档位 |
| `df.py migrate --profile production [--legacy-release]` | 旧项目升级；`--legacy-release` 是唯一允许 `regression → release_ready` 的兼容开关 |
| `df.py self-test` | 现在覆盖主链路 + 并行 + 外部报告；`--fast/--json/--out/--keep` 不变 |
| `config/quality-gates.yaml` | 新增 `profiles`、`placeholders`、`release_report.required_sections`、`regression.require_source_snapshot`、`git.required`、`context.require_contract_bindings` |

## 三、数据结构与 Schema 变化

| 位置 | 变化 |
| --- | --- |
| `project.json` | 新增 `quality_profile`、`legacy_release_shortcut` |
| `backlog-vN.json` 节点 | 新增 `contract_bindings`、`scope_baseline`、`git_unavailable`；`deliverables.contracts` 记录契约文件指纹 |
| `requirements.json` | `acceptance` 支持对象 `{id, criterion}`，字符串格式仍兼容 |
| 执行报告证据块 | 新增 `requirement_acceptance`（逐条需求验收证据） |
| `regression/final-report.json` | 新增 `source_snapshot` |
| `regression/source-snapshot.json` | 新文件：`regression-check` 记录的前置代码快照 |
| `releases/release-report.json` | 新文件：交付报告元信息（版本、章节、门槛快照） |
| `change-request.json` | 补 `applied_at`；`base_plan_version` 允许 0 |
| `SCHEMA_VERSION` | `1.3` → `1.4`（新增字段全部可选） |

## 四、旧项目迁移

```bash
# 1) 先备份 .devflow/
cp -r .devflow .devflow.bak

# 2) 看当前体检结果（会提示没有 quality_profile）
python df.py doctor

# 3) 升级档案并明确档位
python df.py migrate --profile production

# 4) 如果这个项目确实没有交付报告，且短期无法补：
python df.py migrate --legacy-release   # 唯一受控的兼容路径，会写在项目档案里
```

迁移不会删除历史报告、不会重置节点状态。旧数据兼容策略：

- 没有 `current_attempt_id` 的历史节点：不追溯新证据规则
- 旧回归 Markdown 报告：仍可用 `release --legacy-regression` 降级（会记录）
- 旧提示词没有版本元数据：激活/开始前必须 `prompt-init --force` 重新生成
- 历史交付报告：`release` 重新生成前会复制到 `releases/history/`

## 五、测试结果（本机实测）

| 层 | 命令 | 结果 |
| --- | --- | --- |
| 引擎 | `python -m unittest discover -s tests -v` | **195 条全部通过** |
| 示例项目 | `cd examples/sample-project && python -m unittest discover -s tests` | 74 条通过 |
| 端到端 | `python scripts/df.py self-test` | 60 步 → accepted（含并行与外部报告） |
| 示例体检 | `python ../../scripts/df.py doctor` | 通过（提示补 `quality_profile`） |

## 六、CI

`.github/workflows/ci.yml` 已加入仓库，矩阵：`ubuntu-latest` / `windows-latest` × Python `3.8` / `3.12`，
运行引擎测试、示例项目测试、`self-test --fast`，并在 Ubuntu + 3.12 上跑全量 `self-test` 并上传报告。

**说明：本机没有 GitHub Actions 运行环境，CI 结果需以首次 workflow 运行为准。**
本地已验证的是同一组命令能在 Windows + Python 3.13 上通过；Linux 与 3.8 由 CI 覆盖。

## 七、尚未完成

- 契约绑定只做文件指纹，不解析接口语义（不打算做 AST/OpenAPI 解析）。
- 快照只能证明"代码没变"，不能证明"外部 Agent 真的执行过命令"。
- `self-test` 的并行场景是双进程同机，不覆盖多机。
- GitHub Actions 首次运行的 Linux/3.8 结果待补。

## 八、生产验收结论

- P0 五项缺陷全部修复，均有自动化用例覆盖。
- 本地三层测试（引擎 / 示例 / 端到端）全部通过。
- CI 配置已就绪，但**尚未在 GitHub 上真实运行**，因此本次不宣称"Linux/Windows CI 全绿"。
- 结论：**本地生产质量验收通过；CI 验收待首次 workflow 运行后确认。**
