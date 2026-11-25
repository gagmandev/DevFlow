# DF-004 · 账户服务：开户 / 签到 / 奖励 / 扣减 / 余额

> 这份提示词是给一个**没有参与过前面任何对话**的 Code Agent 的。
> 它不需要读原始需求文档，只需要读完这份提示词 + 下面指定的文件，就能独立完成这个节点。

## 1. 角色

你是 积分账户服务 项目的开发工程师。你只负责当前这一个节点，不负责整体架构决策。
遇到与需求冲突、缺少必要信息、或需要改动本节点范围外的代码时，**停下来反馈**，不要自行扩大范围。

## 2. 任务

- 节点编号：DF-004
- 节点名称：账户服务：开户 / 签到 / 奖励 / 扣减 / 余额
- 阶段 / 优先级：Business / P1
- 目标：实现 WalletService 的核心写路径：开户、签到、订单奖励、兑换扣减、余额查询，并保证并发不超扣。

## 3. 项目背景

积分账户服务：为会员体系提供积分开户、入账、扣减、流水查询能力。本期只做账户与流水，不含积分商城页面、不含支付。

## 4. 关联需求

- **REQ-001 · 开通积分账户**：为指定 user_id 开通积分账户，初始余额 0；同一 user_id 重复开户幂等返回已有账户。
  - 验收：重复开户不产生第二个账户且不报错
  - 验收：新账户 balance=0、frozen=0
- **REQ-002 · 每日签到送积分**：每个自然日首次签到发放 10 积分，当天重复签到不重复入账。
  - 验收：当天首次签到余额 +10
  - 验收：当天第二次签到余额不变且不新增流水
- **REQ-003 · 订单完成奖励积分**：订单完成后按实付金额发放积分，1 元 = 1 积分，向下取整；同一来源单号幂等。
  - 验收：1999 分（19.99 元）奖励 19 分
  - 验收：同一订单号重复调用只入账一次
- **REQ-004 · 兑换扣减积分**：兑换商品时扣减积分；可用余额不足时拒绝，余额与流水均不变。
  - 验收：余额不足抛 InsufficientBalance 且无副作用
  - 验收：允许扣减到 0，不允许扣成负数
- **REQ-006 · 余额查询**：返回 balance、frozen、available，available = balance - frozen。
  - 验收：available = balance - frozen
  - 验收：不存在的用户抛 AccountNotFound

需求原文不在本提示词里，按上面列出的编号去 `.devflow/requirements/confirmed-requirements-v1.md` 查对应条目。
**不要擅自改变业务规则**；需求没写到的行为，按「技术约束」里的默认约定处理，并在执行报告里记录假设。

## 5. 前置依赖

- **DF-003 · 账户与流水仓储层**（当前状态：pending）
  - 已产出：src/points/repository.py, tests/test_repository.py
  - 它保证的事：AccountRepo.get_or_create 对同一 user_id 幂等

依赖产物已经存在于代码库中。**不要重写它们**，只按它们的现有接口调用。
如果发现依赖产物的实际接口与本提示词描述不一致，先停下来报告，不要自己改依赖节点的代码。

## 6. 必读文件

- src/points/repository.py
- src/points/models.py
- src/points/errors.py
- .devflow/requirements/confirmed-requirements-v1.md（只查上面列出的需求编号）
- tests/test_repository.py
- .devflow/decisions.md（只看与本节点相关的决策）

## 7. 修改范围

允许新增/修改：
- src/points/service.py
- tests/test_service.py

## 8. 实现步骤

1. `service.py` 定义 `WalletService(store, clock=None)`：
   - `clock` 是可注入的 `Callable[[], datetime]`，默认 `datetime.now`；签到日期与流水时间都走它（便于测试跨天）。
   - 内部 `self._lock = store.lock()`，**所有写方法整体包在 `with self._lock:` 里**：读 → 校验 → 改 → 落盘，中间不允许释放锁。
2. 方法签名与行为：
   - `open_account(user_id) -> Account`：`user_id` 为空抛 `InvalidArgument`；已存在直接返回（不重置余额）。
   - `check_in(user_id) -> dict`：`source_id = f"checkin:{today.isoformat()}"`；若 `LedgerRepo.exists` 命中 → 返回 `{"credited": False, "reason": "already_checked_in"}`，不写流水；否则 +10 并写 `type="checkin"` 流水，返回 `{"credited": True, "amount": 10}`。
   - `reward_order(user_id, amount_cents, order_id) -> dict`：`amount_cents <= 0` 抛 `InvalidArgument`；积分 = `amount_cents // 100`；若为 0 则不入账、不写流水，返回 `{"credited": False, "reason": "amount_too_small"}`；`source_id=order_id` 已存在则返回 `{"credited": False, "reason": "duplicate"}`；否则入账 + 流水 `type="order_reward"`。
   - `redeem(user_id, amount, source_id) -> dict`：`amount <= 0` 抛 `InvalidArgument`；`source_id` 重复抛 `DuplicateRequest`；`available < amount` 抛 `InsufficientBalance(required=amount, available=available)`，**抛之前不能有任何写入**；成功则扣减 + 流水 `type="redeem"`，amount 记为负数。
   - `balance(user_id) -> dict`：返回 `{"balance", "frozen", "available"}`；账户不存在抛 `AccountNotFound`。
3. 流水 `entry_id` 用 `uuid4().hex`，`note` 写人类可读原因。
4. `tests/test_service.py` 用 `tempfile.TemporaryDirectory` 建真实存储（不要 mock store），覆盖测试要求里的全部场景；跨天用注入 clock 验证"第二天能再签到一次"。
5. 自查：`balance` 与 `sum(ledger.amount)` 必须一致，测试里加一条对账断言。

注意：不要改 `repository.py`。如果发现它缺方法，停下来报告，不要自己加。

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

- [ ] 同一 user_id 重复开户幂等，不产生第二个账户
- [ ] 当天首次签到 +10，第二次签到返回 already_checked_in 且余额与流水不变
- [ ] 订单奖励 1999 分 → 19 积分；同一 source_id 重复调用只入账一次
- [ ] 余额不足时抛 InsufficientBalance，余额与流水均不变
- [ ] balance 返回 balance/frozen/available 三个值，available = balance - frozen

## 12. 测试要求

- 开户幂等（两次调用只一个账户）
- 签到幂等（同一天两次只加一次）
- 奖励向下取整（1999→19，100→1，99→0）
- 扣减成功 / 余额不足 / 扣到 0
- 不存在的账户查余额抛 AccountNotFound
- 记录每条命令的退出码和通过/失败数量，写进 test-report.json。

测试必须**实际执行**。不允许：
- 声称"测试通过"但没有命令输出；
- 为了让测试变绿而删改断言、跳过用例；
- 用 mock 把被测逻辑本身mock掉。

## 13. 交付物

- src/points/service.py
- tests/test_service.py
- `.devflow/nodes/DF-004/execution-report.md`
- `.devflow/nodes/DF-004/test-report.json`

## 14. 执行报告

完成后写两个文件：

1. `.devflow/nodes/DF-004/execution-report.md`（人读）——按 `.devflow` 的执行报告模板，必须包含一个机器可读证据块：

   ```devflow-execution
   {
     "node_id": "DF-004",
     "result": "success",
     "modified_files": ["相对项目根的路径"],
     "acceptance": [{"criterion": "验收标准原文", "met": true, "evidence": "测试名/命令"}],
     "assumptions": [],
     "leftovers": []
   }
   ```

2. `.devflow/nodes/DF-004/test-report.json`（机器读）——测试命令、退出码、通过/失败数、用例清单。

写完后**不要**修改 `.devflow/plans/*.json` 或看板。状态由主控 Skill 同步。
