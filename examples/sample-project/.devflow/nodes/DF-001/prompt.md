# DF-001 · 项目骨架与领域异常

> 这份提示词是给一个**没有参与过前面任何对话**的 Code Agent 的。
> 它不需要读原始需求文档，只需要读完这份提示词 + 下面指定的文件，就能独立完成这个节点。

## 1. 角色

你是 积分账户服务 项目的开发工程师。你只负责当前这一个节点，不负责整体架构决策。
遇到与需求冲突、缺少必要信息、或需要改动本节点范围外的代码时，**停下来反馈**，不要自行扩大范围。

## 2. 任务

- 节点编号：DF-001
- 节点名称：项目骨架与领域异常
- 阶段 / 优先级：Foundation / P0
- 目标：建立 src/points 包结构、测试入口和领域异常体系，让后续所有节点有统一的错误语义。

## 3. 项目背景

积分账户服务：为会员体系提供积分开户、入账、扣减、流水查询能力。本期只做账户与流水，不含积分商城页面、不含支付。

## 4. 关联需求

- 本节点不直接对应功能需求（基础设施 / 交付类）。

需求原文不在本提示词里，按上面列出的编号去 `.devflow/requirements/confirmed-requirements-v1.md` 查对应条目。
**不要擅自改变业务规则**；需求没写到的行为，按「技术约束」里的默认约定处理，并在执行报告里记录假设。

## 5. 前置依赖

无前置依赖，可以从零开始。

依赖产物已经存在于代码库中。**不要重写它们**，只按它们的现有接口调用。
如果发现依赖产物的实际接口与本提示词描述不一致，先停下来报告，不要自己改依赖节点的代码。

## 6. 必读文件

- requirements.md
- .devflow/requirements/confirmed-requirements-v1.md（只查上面列出的需求编号）
- .devflow/decisions.md（只看与本节点相关的决策）

## 7. 修改范围

允许新增/修改：
- src/points/__init__.py
- src/points/errors.py
- tests/test_errors.py

## 8. 实现步骤

1. 建 `src/points/__init__.py`，写入 `__version__ = "0.1.0"`，不导出具体实现（避免循环导入）。
2. 建 `src/points/errors.py`：
   - `PointsError(Exception)` 作为基类，构造参数 `message`，属性 `code` 由子类类属性提供；
   - 子类：`InvalidArgument`(`INVALID_ARGUMENT`)、`AccountNotFound`(`ACCOUNT_NOT_FOUND`)、`InsufficientBalance`(`INSUFFICIENT_BALANCE`)、`DuplicateRequest`(`DUPLICATE_REQUEST`)；
   - `InsufficientBalance` 额外带 `required` / `available` 两个数值属性，便于调用方判断。
3. 建 `tests/test_errors.py`：逐个断言 `code`、断言都是 `PointsError` 子类、断言 `str(exc)` 包含传入信息。
4. 运行 `python -m unittest discover -s tests -v`，确认发现并执行了测试（不是 0 tests）。

注意：不要在这里引入任何存储或业务代码，DF-002 才做。

每完成一步就运行一次相关测试，不要全部写完再跑。

## 9. 技术约束

- 技术栈：{"language": "Python", "version": "3.11+", "runtime_deps": "无（仅标准库）", "storage": "本地 JSON 文件（原子写入）", "test": "unittest", "layout": "src/ 布局"}
- 测试命令：python -m unittest discover -s tests -v
- 构建命令：（无）
- 硬约束：只使用 Python 标准库，不引入第三方运行时依赖
- 硬约束：积分必须是整数，禁止用 float 表示或参与运算
- 硬约束：任何写操作必须原子落盘，进程中断不能留下半截数据
- 硬约束：余额不允许为负；并发扣减不允许超扣
- 硬约束：不得把密钥、令牌写入代码或报告
- 约定 · 目录：业务代码放 src/points/，测试放 tests/
- 约定 · 错误处理：领域异常统一定义在 src/points/errors.py，不抛裸 Exception
- 约定 · 命名：模块与函数 snake_case，类 CamelCase
- 约定 · 存储：所有落盘走 store.atomic_write，不直接 open('w')
- 遵循现有代码的命名、分层、错误处理风格；不引入新的第三方运行时依赖。

## 10. 禁止事项

- 不修改本节点 files_scope 之外的业务代码
- 不修改 .devflow/plans/*.json、看板、其他节点的目录
- 不改写需求基准文档；发现需求冲突时停下来报告
- 不重复实现已存在的公共能力（先读依赖产物）
- 不删除或跳过已有测试来让流水线变绿
- 不把没执行过的测试写成 passed
- 不提交任何密钥、令牌、真实用户数据

## 11. 验收标准

- [ ] python -m unittest discover -s tests -v 可执行并通过
- [ ] src/points/errors.py 定义 InvalidArgument / AccountNotFound / InsufficientBalance / DuplicateRequest 四个异常，均带 code 属性
- [ ] 包可从 src 布局导入（sys.path 处理写在测试里，不改全局环境）

## 12. 测试要求

- 断言四个异常都能被捕获且 code 正确
- 断言异常继承自 PointsError（统一基类）
- 记录每条命令的退出码和通过/失败数量，写进 test-report.json。

测试必须**实际执行**。不允许：
- 声称"测试通过"但没有命令输出；
- 为了让测试变绿而删改断言、跳过用例；
- 用 mock 把被测逻辑本身mock掉。

## 13. 交付物

- src/points/__init__.py
- src/points/errors.py
- tests/test_errors.py
- `.devflow/nodes/DF-001/execution-report.md`
- `.devflow/nodes/DF-001/test-report.json`

## 14. 执行报告

完成后写两个文件：

1. `.devflow/nodes/DF-001/execution-report.md`（人读）——按 `.devflow` 的执行报告模板，必须包含一个机器可读证据块：

   ```devflow-execution
   {
     "node_id": "DF-001",
     "result": "success",
     "modified_files": ["相对项目根的路径"],
     "acceptance": [{"criterion": "验收标准原文", "met": true, "evidence": "测试名/命令"}],
     "assumptions": [],
     "leftovers": []
   }
   ```

2. `.devflow/nodes/DF-001/test-report.json`（机器读）——测试命令、退出码、通过/失败数、用例清单。

写完后**不要**修改 `.devflow/plans/*.json` 或看板。状态由主控 Skill 同步。
