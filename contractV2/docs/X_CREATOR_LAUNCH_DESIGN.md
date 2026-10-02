# X 创作者发币与费用结算设计

调研日期：2026-09-29。本文是架构提案，未实现登录、Bot、收款、智能账户或出金代码；不代表这些功能已随 V2 测试网上线。

## 结论

可以在 V2 外围实现“X 登录或 Bot 下单 → 确认到账 → 自动发币 → 创作者领取费用”。推荐让每位已验证用户的稳定智能账户充当链上 creator，账户执行配置和发币，Bot 负责订单与受限代执行。这个方案不要求改变 Factory、curve、hook 的核心规则；新账户及外围系统仍需独立安全审查。

X Money 应作为可替换、默认关闭的出金适配器。当前无法承诺第三方自动批量代付可用，不应使发币或链上领取依赖它。

## 已核实的产品事实

- [UsePaid 官方文档](https://usepaid.app/docs)在调研时明确显示 X Money 付款暂停；符合条件的费用通过 X 登录后申请 SOL 或保留的原资产，由 worker 处理。X 上的 claim 命令仍标注为计划功能。
- [UsePaid 资金流说明](https://usepaid.app/capital-flow)区分新费用和历史 X Money 债务；未来通过 X Money 结算取决于支持方案。不能把历史支付记录解释成当前可用的开放 API。
- [X Money 官方站](https://money.x.com/en)显示向美国部分成年用户开放，提供美元账户与 X 内转账。本次查阅未确认公开的第三方商户批量付款 API、加密资产入金 API 或本项目的代收代付准入。需要正式确认，而非推定不存在。
- X Corp 的该产品域名是 `money.x.com`；不要把 `xmoney.com` 的同名商户产品当作它。

以上描述是对应日期的页面内容，接入前应重新核实；不复用 UsePaid 的分成比例或历史债务政策。

## 与 V2 的接口关系

| 当前源码 | 设计影响 |
| --- | --- |
| `src/HedgeFunFactory.sol`：`_launch` 要求调用者为 `q.creator` 或受信 launcher | 不把可以任意指定 creator 的 Bot EOA 加入白名单。智能账户直接作为 creator 调用。 |
| `src/HedgeFunFactory.sol`：`_salt` 包含 symbol、creator、nonce；`predict` 返回 terms | 固定订单参数与预测地址；报价变化必须重新确认，不能静默替换。 |
| `src/v2/CurveDeployer.sol`：`setCurveConfig`；`src/v2/V2TreasuryDeployer.sol`：`setStrategyKind`、`setEngineConfig` | 配置按 `msg.sender` 派生 salt，必须由同一个 creator 账户执行。普通 Bot 调用会配置 Bot 自己的命名空间。 |
| `src/HedgeFunToken.sol`：deployer、metadata、socials | creator 还拥有元数据管理权。twitter 链接只是描述，不能证明身份。 |
| `src/v2/HedgeFunBondingCurve.sol`：`sell`、`claimFees` | 卖出税以 stock 分给 protocol、creator、treasury；第三方可以触发领取，但无法重定向收款人。买入税烧毁新 token。 |
| `src/hooks/HedgeFunHook.sol`：费用 sweep、`claim`、`claimFor` | 毕业后费用仍按 stock 分账；第三方只能为固定收款人领取。 |
| `src/HedgeFunFactory.sol`：`_chargeLaunchFee` | 发币费由部署配置决定，可为 None、Native、Stock 或 USDG，不能假定为 USDT。 |
| `src/v2/HedgeFunV2TradeRouter.sol`：`buy` | 支持经有效 canonical V3 路径把普通 ERC20 换成 stock 后买币；流动性和路径须验证。 |
| `src/HedgeFunLaunchRouter.sol`：初始买入 | 旧路由直接买 V4，不适合 V2 未毕业曲线。原子发币加首买需账户批量执行 factory 与 V2TradeRouter。 |

## 身份与订单

使用 [X OAuth 2.0 PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)，验证 state、PKCE 和会话绑定，再用 [`GET /2/users/me`](https://docs.x.com/x-api/users/get-my-user)取得用户身份。以 X user ID 为内部主键，handle 为可更新的展示字段；handle 变更不得转移费用权益。

分开记录付款人、发起者和费用受益人：

- 本人登录并确认发行参数，才展示“由该用户创建”。
- A 出资指定 B，只能展示“为 B 创建”或“费用受益人为 B”。B 尚未授权时，不把 B 描述为已验证创作者或背书方；费用进入隔离的待认领账户，待 B 验证身份后认领。
- 链上 creator 是地址，不是 X handle。未认领场景下它代表隔离账户，界面应明确其托管及认领状态。不能仅凭写入 handle 把账户控制权交给付款人。

每个订单固定：订单 ID、付款人、受益 X ID、creator account、chainId、付款资产合约和金额、退款地址、完整 launch request、terms、metadata/config 承诺、nonce、有效期和预测地址。平台和 Bot 共用订单服务；模型只能提出结构化参数，不能绕过确认和执行策略。

每个用户使用稳定 creator 智能账户。账户执行配置、发币和可选首买；Bot 代付 gas 或使用限定目标合约、方法、金额、链、nonce、有效期的权限。恢复或更换控制人应保留账户地址，因为曲线 creator 与 token metadata deployer 不可变，毕业后的 hook 收款角色又有独立规则。

## 收款、执行和退款

为订单使用独立收款地址或可唯一对应的 payment intent。核验资产合约、链、金额、真实到账及链的最终确认；Bot 消息或付款截图不构成到账证据。未提供官方收款接口前，不把 X Money 消息或页面抓取当支付回调。

状态：`quoted → awaiting_payment → confirmed → launching → launched`；异常进入 `retryable`、`refund_due` 或 `manual_review`。

- 用 `(chainId, txHash, logIndex)` 去重付款，用订单 ID 去重发行，数据库唯一约束与 worker 锁共同防止重复执行。
- 广播前持久化交易与 nonce。超时先查 receipt、预测地址和 Launched 事件；未知结果不等于失败，不能换 nonce 再发一枚币。
- 固定用户批准的参数与费用上限，模拟调用并检查报价有效性；terms 变化后重新确认。账户授权绑定完整参数，不能只签一个金额。
- 明确定义少付、多付、错资产、过期到账、最终失败的处理；退款使用事先验证的地址。交易结果未厘清前不可同时退款和重试。链重组需撤销未最终确认的内部状态。
- 首买独立记录预算、滑点、截止时间及未用金额退款；发币成功不等于首买成功，非原子流程须分别展示结果。

## 费用与出金

默认资金流：

`creator stock fees → creator account / isolated escrow → optional stock→USDC → optional off-ramp to USD → payout adapter`

实际费用是各币对应的 **stock 资产，不一定是 USDT 或 USDC**。用户用 USDT 支付只是入金选择；是否能兑换取决于目标链、资产和流动性。USDT→USDC 也不等于法币出金，X Money 美元账户需要另行验证的 USD 通道。

单独维护 protocol fee、creator fee、strategy treasury 资金与未认领余额。不得拿策略 treasury 的资本支付创作者；如协议收入额外补贴创作者，须另列明确规则，不能与 creatorBps 重复记账。

账本按 token、受益 X ID、原资产、金额及来源事件追踪；只将最终确认且实际收到的款项计入可支付余额。兑换保留报价、实收、手续费和汇率，出金保留外部交易 ID、状态和对账凭证。付款使用幂等键；queued、submitted、settled、returned 不可混为已付款。退款或拒付回到原受益人的负债，不默认为协议收入。

原资产或链上稳定币领取作为第一阶段路径。X Money 适配器仅在官方接入、账户资格、商业用途及结算方式得到确认后启用；不可依赖未公开接口或浏览器自动点击建立资金系统。实际法币业务上线还需出入金服务方确认代收代付资格及相关身份核验要求。

## 实施边界与验收

第一阶段新增：身份服务、订单接口、收款监听、执行 worker、智能账户接入、链上费用索引、分账与退款账本、领取门户、模拟出金适配器。本文未实现这些组件。

若要求 relayer 直接替 EOA 发币，或把发起者与费用受益人原生拆成两个链上角色，才需要另外设计签名授权和配置代签接口；现有 Factory 没有这类入口，应作为独立合约版本审查。

测试覆盖：身份与 handle 变化、未授权指定他人、重复到账事件、重组、交易超时与重试、配置 salt 一致性、报价过期、发币加首买、退款、曲线及毕业后费用归属、部分兑换与出金失败后的对账。

**Go**：外围 MVP 与测试网模拟结算，不阻塞 V2 核心测试。

**No-go**：当前承诺全自动 X Money 实盘代付。阻塞项是官方接入与业务准入未确认、目标链真实兑换及出金通道未验证、外围资金系统尚未实现和测试。
