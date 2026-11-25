# CR-001 影响评估

- 变更：退款场景：积分冻结与解冻
- 类型：rule_change
- 基线：需求 v1 / 计划 v1
- 评估时间：2026-11-25T14:05:00+08:00

## 1. 结论

改动集中在**数据模型**和**一条新的写路径**。8 个节点里：
2 个 modify（DF-002、DF-006）、1 个 add（DF-008）、1 个文档 modify（DF-007）、
3 个 reusable（DF-001、DF-003、DF-004）、1 个 review 后判定 reusable（DF-005）。

**DF-004 不需要返工**：v1 里 `redeem` 已经按 `account.available` 校验，
冻结生效后自动受约束。这是当初把 `available` 做成独立属性的收益。

## 2. 新旧差异

| 维度 | 变更前（v1） | 变更后（v2） |
| --- | --- | --- |
| 业务规则 | 退款不处理积分（人工） | 退款时冻结等额积分，运营解冻或转扣减 |
| 数据模型 | Account.balance / frozen（恒 0）；LedgerEntry 无冻结信息 | frozen 真正生效；LedgerEntry 新增 `frozen_delta` |
| 接口 | open / check_in / reward_order / redeem / balance | 新增 freeze / unfreeze / frozen_amount |
| 对账 | `sum(amount) == balance` | 两条：`sum(amount) == balance`、`sum(frozen_delta) == frozen` |
| 非功能 | 并发不超扣 | 并发冻结不超额，冻结与扣减互斥 |

## 3. 逐节点影响

| 节点 | 动作 | 依据 | 需重新测试 |
| --- | --- | --- | --- |
| DF-001 | reusable | 异常体系够用，冻结复用 `InvalidArgument` / `DuplicateRequest` | 否 |
| DF-002 | modify | `LedgerEntry` 必须加 `frozen_delta`；`amount=0` 仅允许在冻结流水；`from_dict` 要兼容旧数据 | 是 |
| DF-003 | reusable | 仓储接口未变；`exists` 可直接做冻结幂等 | 否 |
| DF-004 | reusable | `redeem` 已按 `available` 校验（见 `service.py` 的 `redeem`） | 否 |
| DF-005 | review → reusable | 查询层不按类型过滤，新增类型不需要改代码 | 否 |
| DF-006 | modify | 冻结改变可用额度语义，必须补集成/并发用例并重跑全量 | 是 |
| DF-007 | modify | README「本期不做冻结」、DEPLOY 配置项已过时 | 否 |
| DF-008 | add | 冻结/解冻是独立写路径，单独成节点 | 是 |

判定依据不是任务名称，而是逐个看了 v1 的代码：

- `service.py` 的 `redeem` 用的是 `account.available`，不是 `account.balance`；
- `repository.py` 的接口签名与冻结无关；
- `queries.py` 只做过滤与分页，不看 `type`。

## 4. 数据与兼容性

- **数据库结构**：无数据库。JSON 文件新增字段，`from_dict` 用 `data.get("frozen_delta", 0)` 容错，
  历史流水不需要重写。
- **历史数据迁移**：不需要迁移脚本。旧流水 `frozen_delta` 视为 0，对账仍成立。
- **接口向后兼容**：`balance()` 返回结构不变；`redeem` 在冻结后更严格（符合新规则），
  属于**语义收紧**，已在变更说明里标注。
- **回滚方式**：`frozen` 全部解冻后移除 DF-008 的调用入口即可；数据字段留着不影响旧代码。

## 5. 风险

| 风险 | 等级 | 应对 |
| --- | --- | --- |
| 对账公式变化导致旧校验失效 | 中 | DF-006 补两条对账断言 |
| 旧流水缺 `frozen_delta` 读不出来 | 中 | DF-002 的 `from_dict` 容错 + 用例 |
| 已验收节点返工引入回归 | 中 | DF-002 返工后重跑全量；DF-004/005 不重跑但由 DF-006 覆盖 |

## 6. 推荐实施顺序

1. DF-002：数据模型加 `frozen_delta`（其余节点都依赖它）
2. DF-008：冻结/解冻服务
3. DF-006：集成 + 并发 + 全量回归
4. DF-007：文档同步

## 7. 需要用户确认的点

- [x] 冻结期间是否允许运营直接扣除（本期只做解冻，扣除走 `redeem`）
- [x] 冻结是否需要过期时间（本期不做）
- [x] 是否允许部分解冻（允许，按金额）

## 8. 对已完成工作的保护

| 已完成节点 | 是否重跑 | 理由 |
| --- | --- | --- |
| DF-001 | 否 | 异常体系未变 |
| DF-003 | 否 | 仓储接口未变 |
| DF-004 | 否 | 余额校验已用 available |
| DF-005 | 否 | 查询层不感知流水类型 |
| DF-002 | 是 | 数据结构变更，必须重新验收 |
