# MCP 集成说明

本文说明本 skill 实际使用的麦当劳 MCP Server、Tool、完整调用流程，以及这套集成带来的业务价值。所有参数与返回均来自 2026-10-24 的真实调用。

## 1. 使用的 MCP Server

| 项 | 值 |
|---|---|
| 名称 | `mcd-mcp`（麦当劳中国官方 MCP Server） |
| 地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 认证 | HTTP Header `Authorization: Bearer <MCP Token>`，token 在 [open.mcd.cn](https://open.mcd.cn) 登录后于控制台申请 |
| 服务范围 | 中国大陆（不含港澳台） |
| 客户端配置 | `~/.workbuddy/mcp.json` 中的 `mcpServers.mcd-mcp` |

> 实测踩坑：token 必须带 `Bearer ` 前缀。首次配置时漏了前缀导致 401，补上后 `order-list` 第一次调用即返回历史订单，链路连通。

## 2. 使用的 Tool

### 主链路（这个 skill 的核心）

| # | Tool | 作用 | 关键入参 | 关键出参 |
|---|---|---|---|---|
| 1 | `query-nearby-stores` | 定位门店 | `searchType`(1收藏/2搜索)、`beType`(1到店/5得来速)、`city`+`keyword` | `storeCode`、`beCode`、`address`、`reservation` |
| 2 | `query-meals` | 拉实时菜单 | `storeCode`、`orderType`、`beType` | `categories`、`meals{code:{name,currentPrice,originalPrice,discountType,withOrder}}` |
| 3 | `calculate-price` | 官方算价（含促销/运费/打包费） | `items[{productCode,quantity}]` | `price`（分）、`discount`、`takeWayList[].code` |
| 4 | `query-meal-detail` | 套餐构成 / 随心配子项 / 特调 | `code` | `choices`、`modification`、`supportModify` |
| 5 | `create-order` | 下单，返回支付链接 | `storeCode`、`orderType`、`beType`、`takeWayCode`、`items` | `orderId`、支付链接 |
| 6 | `query-order` | 支付后查订单状态 | `orderId` | 订单状态、商品明细 |
| 7 | `cancel-order` | 取消订单 | `orderId`、`cancelReasonCode` | 取消结果 |

### 扩展链路（按场景触发）

| Tool | 场景 |
|---|---|
| `delivery-query-addresses` → `delivery-query-stores` | 麦乐送：先选地址，再拿可配送门店的 `storeCode` + `beCode` |
| `query-store-coupons` | 用户问"这家店我有什么券能用" |
| `available-coupons` | 用户问"有什么券可以领" |
| `query-meal-assistance`、`query-promotions` | 企业团餐：助餐服务 + 满减规则 |
| `order-list` | 历史订单（也用作连通性自检） |
| `now-time-info` | 预约场景的时间基准 |

### 场景编码矩阵（决定成败的一张表）

| 场景 | beType | orderType | beCode |
|---|---|---|---|
| 到店自取 | 1 | 1 | **不传**（传了报错） |
| 得来速 DT | 5 | 1 | 必传（`query-nearby-stores` 返回） |
| 麦乐送 | 2 | 2 | 必传（`delivery-query-stores` 返回） |
| 企业团餐 | 6 | 2 | 必传 + `gmServiceCode` |

## 3. 调用流程

```
用户："不知道吃啥，帮我搭配一份麦当劳"
   │
   ├─ 1. query-nearby-stores(searchType=1, beType=1)
   │      → 附近/收藏门店列表（storeCode、店名、地址、distance）
   │      → 「1. 玉溪极中心店  2. 玉溪聂耳广场店 …」让用户选
   │
   ├─ 2. 用户选店 → 记录 storeCode=3570064
   │
   ├─ 3. 询问人数 → 1 人
   │
   ├─ 4. 询问预算 → 30-40 元
   │
   ├─ 5. query-meals(storeCode=3570064, orderType=1, beType=1)
   │      → 12 个分类、60+ 商品（元）
   │      → 落盘 menu.json
   │
   ├─ 6. python main.py --menu menu.json --budget 30-40 --people 1
   │      → 角色识别（堡/小食/饮/甜品/套餐/随心配/周边）
   │      → 组合枚举（现成套餐 / 堡+小食+饮 / 堡+饮 / 随心配加购）
   │      → 打分（结构 100 + 预算用足度 20 + 折扣 15 − 麦金卡 12）
   │      → 3 组结构互不相同的候选
   │
   ├─ 7. calculate-price(items=[龙焰鸡腿堡, 中薯条, 小杯鲜萃咖啡])
   │      → price=3990 分（¥39.90），discount=810 分（¥8.10）
   │      → 与脚本估算一致 ✅
   │
   ├─ 8. 输出推荐表（用户选择）
   │
   └─ 9.（可选）create-order(takeWayCode="eat-in") → 支付链接 → query-order
```

**为什么门店放在第一步**：不同门店菜单不同价（`query-meals` 以 `storeCode` 为维度），先定门店才能保证后面报的菜和价都是这家店真实在售的；同时"去哪吃"也是用户最先能回答的问题，比先问预算更自然。

## 4. 真实调用证据

**门店查询（2026-10-24 16:49，traceId `de1ea665...`）**

```json
{"data": [{"storeCode": "3570064", "storeName": "麦当劳玉溪极中心餐厅",
           "address": "棋阳路极中心购物广场一层", "reservation": true}]}
```

**菜单抽样（2026-10-24 16:49，traceId `cddd1a8f...`）**

```json
{"9900016073": {"name": "龙焰鸡腿堡三件套", "currentPrice": "26.9", "originalPrice": "48"},
 "521952":     {"name": "龙焰鸡腿堡", "currentPrice": "15.9", "originalPrice": "24",
                "discountType": "促销优惠"},
 "9900004236": {"name": "人气超值四件套随心选", "currentPrice": "28", "originalPrice": "57.5",
                "discountType": "随单购麦金卡优惠", "withOrder": {"membershipCode": "106"}}}
```

**算价复核（2026-10-24 16:53，traceId `a81b7c48...`）**

```json
{"price": 3990, "discount": 810, "productOriginalPrice": 4800,
 "productList": [{"productName": "龙焰鸡腿堡", "subtotal": 1590},
                 {"productName": "中薯条", "subtotal": 1400},
                 {"productName": "小杯鲜萃咖啡", "subtotal": 1000}],
 "takeWayList": [{"code": "eat-in", "title": "堂食"},
                 {"code": "take-in-store", "title": "外带"}]}
```

脚本估算 ¥39.9 / 省 ¥8.1，官方 ¥39.90 / 省 ¥8.10 —— 完全吻合，说明"本地组合搜索 + 官方算价校验"的两段式设计成立。

## 5. 业务价值

**对用户**

- 把"翻菜单比较"（平均 3-5 分钟）压缩成"看一眼表格"（约 10 秒）。
- 预算即硬约束，不会推荐超预算方案；每档预算都有 3 种吃法可选。
- 避免"虚假便宜"：随单购麦金卡价、学生专享价都会被明确标注，不会到店才发现价格不对。

**对场景**

- 单人快速午餐：30 秒内完成"选什么 + 多少钱 + 直接下单"。
- 代点/多人：给出双人自由拼与国家套餐两套方案，价格可对比。
- 价格敏感场景（学生、写字楼午高峰）：预算内用足度作为打分维度，优先"同样 40 块吃得更丰盛"。

**对开发者 / 生态**

- 展示了 MCP 工具从"单点调用"到"可复用决策流程"的封装方式：工具负责事实（菜单/价格），脚本负责策略（组合/打分），Skill 负责编排与话术。
- `main.py` 与 MCP 解耦：只要输入同样结构的 JSON，就能复用于任何提供"分类+价格"的餐饮菜单，不绑定麦当劳。
- 参数矩阵与踩坑清单（`references/mcd-mcp-tools.md`）可作为后续接入麦当劳 MCP 的开发者的起点，减少试错。

## 6. 错误处理与降级

| 情况 | 策略 |
|---|---|
| MCP 未连接 / 工具不可用 | 明确告知用户去连接器页信任 `mcd-mcp`，不臆造菜单 |
| `success:false` | 原样转述 `message`，停止当前链路 |
| 收藏门店为空 | 自动降级为 `searchType=2` + 城市/关键词搜索 |
| 预算内无解 | 脚本返回非零码，提示放宽 5-10 元或减少忌口 |
| 算价与估算不符 | 以 `calculate-price` 为准，重新排序并说明原因 |
| 下单失败 | 打印失败原因，保留购物车参数供重试 |

## 7. 安全与合规

- MCP token 只保存于本地 `~/.workbuddy/mcp.json`，仓库内所有文件均不含 token。
- 下单是唯一写操作，执行前必须获得用户明确确认；支付始终由用户在官方 APP/小程序完成，skill 不接触支付凭证。
- 数据仅用于当次推荐，不做持久化存储与二次分发。
