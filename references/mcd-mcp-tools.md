# mcd-mcp 工具参考（本 skill 实际用到的部分）

MCP Server：麦当劳中国官方 `https://mcp.mcd.cn`（Streamable HTTP，需 `Authorization: Bearer <token>`，token 在 open.mcd.cn 申请）。
工具名前缀：`mcp__mcd-mcp__`。以下参数均以 2026-10 实测返回为准。

## 1. 场景与编码矩阵（最容易错的地方）

| 场景 | beType | orderType | beCode | 其它必填 |
|---|---|---|---|---|
| 到店自取 | 1 | 1 | **不传**（传了报错） | storeCode |
| 得来速 DT | 5 | 1 | 必传（来自 query-nearby-stores） | storeCode |
| 麦乐送 | 2 | 2 | 必传（来自 delivery-query-stores） | storeCode、addressId |
| 企业团餐 | 6 | 2 | 必传 | storeCode、addressId、gmServiceCode |

## 2. 本 skill 调用链

```
query-nearby-stores  →  门店列表 → 用户选店 → storeCode
        │
   询问人数 / 预算      →  people、budget
        │
query-meals          →  该门店实时菜单（元）
        │
main.py     →  3 组候选（本地算法，无网络）
        │
calculate-price      →  官方结算价（分）
        │
query-meal-detail    →  仅当命中「随心配」，让用户选子项
        │
create-order         →  下单，返回支付链接
        │
query-order          →  支付后查状态
```

## 3. 逐个工具

### query-nearby-stores —— 找店

```json
// 收藏门店
{"searchType": 1, "beType": 1}
// 按位置搜（收藏为空时）
{"searchType": 2, "beType": 1, "city": "玉溪", "keyword": "极中心"}
```

返回 `data[]`：`storeCode`、`storeName`、`address`、`distance`、`beCode`（beType=5 才有）、`reservation`、`reservationTimeOptions`。
实测（玉溪，2026-10-24）：`storeCode=3570064`，麦当劳玉溪极中心餐厅，支持预约。

**门店选择的三个要点**

1. `searchType=1`（收藏）返回的店 `distance=0`、`longitude/latitude=0.0` —— 这是"收藏店无坐标"，不是"离你 0 米"。展示时按返回顺序，不要拿 distance 排序。
2. `searchType=2`（位置搜索）需要 `city` + `keyword`，返回的 `distance` 单位是**米**（实测 `1355` = 约 1.4km）。示例：

   ```json
   {"searchType": 2, "beType": 1, "city": "昆明", "keyword": "朝云街"}
   // → 3570063 昆明朝云街餐厅 distance=1355
   //   3570246 昆明实力心城花千坊餐厅 distance=1411
   //   3570265 昆明斗南春城花街餐厅 distance=4958
   ```
3. 展示时给编号，让用户回"1/2/3"；记录 `storeCode`（以及 beType=5 时的 `beCode`）供后续所有工具复用。用户换店要重新 `query-meals`——菜单是按门店和 beType 出价的。

### query-meals —— 菜单

```json
{"storeCode": "3570064", "orderType": 1, "beType": 1}
```

返回结构：

```json
{
  "categories": [{"name": "精选单人餐", "meals": [{"code": "9900004064", "tags": ["麦金卡"]}]}],
  "meals": {
    "9900004064": {"name": "精选单人餐三件套", "currentPrice": "34.5", "originalPrice": "46.5"},
    "9900004236": {"name": "人气超值四件套随心选", "currentPrice": "28", "originalPrice": "57.5",
                   "discountType": "随单购麦金卡优惠", "withOrder": {"membershipCode": "106", "specId": "880"}}
  },
  "frequent": {"code": "9900004236", "tags": ["我的常点"]}
}
```

要点：
- 价格是**元**，字符串；`withOrder` 非空表示该价需随单购麦金卡。
- 同一 code 会出现在多个分类，需按 code 去重。
- 分类名带换行（`"小食甜品\n/其他"`），匹配时先 `strip` 或直接子串匹配。

### calculate-price —— 算价

```json
{
  "storeCode": "3570064", "orderType": 1, "beType": 1,
  "items": [{"productCode": "521952", "quantity": 1},
            {"productCode": "4810", "quantity": 1},
            {"productCode": "520689", "quantity": 1}]
}
```

实测返回（2026-10-24，traceId `a81b7c48...`）：

```json
{"productOriginalPrice": 4800, "discount": 810, "price": 3990,
 "productList": [
   {"productCode": "521952", "productName": "龙焰鸡腿堡", "subtotal": 1590},
   {"productCode": "4810",   "productName": "中薯条",     "subtotal": 1400},
   {"productCode": "520689", "productName": "小杯鲜萃咖啡", "subtotal": 1000}],
 "takeWayList": [{"code": "eat-in", "title": "堂食"},
                 {"code": "take-in-store", "title": "外带"}]}
```

- 金额单位**分**：`3990` = ¥39.90。
- `takeWayList[].code` 是后续 `create-order` 必填的 `takeWayCode`。
- 到店场景不传 `beCode`；外送场景传地址绑定的 `beCode` 并会返回运费/打包费。

### query-meal-detail —— 套餐详情 / 随心配子项

```json
{"storeCode": "3570064", "orderType": 1, "beType": 1, "code": "9900013304"}
```

用于：套餐可换什么、随心配可选哪些子项、是否有特调。
特调下单时：选中项 key 用 `selectedKey`，未选中但带 `unselectedKey` 的组也必须传 `unselectedKey`。

### create-order —— 下单

```json
{
  "storeCode": "3570064", "orderType": 1, "beType": 1, "takeWayCode": "eat-in",
  "items": [{"productCode": "521952", "quantity": 1}],
  "remark": "无接触配送"
}
```

- 返回订单号与支付链接；支付需用户自行完成。
- 预约场景加 `reservationDate: "2026-10-24 18:30"`（格式 `yyyy-MM-dd HH:mm`）。
- 用券时在 item 上带 `couponId` / `couponCode`。

### query-order / cancel-order

```json
{"orderId": "1030125920000807025697781955"}
{"orderId": "...", "cancelReasonCode": "1"}   // 1改主意 2重复下单 3点错 4地址电话错 5时间错 -1其它
```

### 辅助工具

| 工具 | 用途 |
|---|---|
| `delivery-query-addresses` | 麦乐送地址列表 |
| `delivery-query-stores` | 地址可配送门店（拿 beCode） |
| `query-store-coupons` | 指定门店+订单类型可用券 |
| `available-coupons` | 麦麦省可领券 |
| `query-meal-assistance` | 团餐助餐服务 |
| `query-promotions` | 团餐满减规则 |
| `now-time-info` | 预约/活动需要时间基准 |
| `order-list` | 历史订单（不传参） |

## 4. 错误与降级

| 现象 | 处理 |
|---|---|
| `success:false`，message 含"门店不存在" | 重新查店，确认 storeCode 与 beType 匹配 |
| 传了 `beCode` 报参数错误 | 到店自取（beType=1）不能传 beCode |
| `calculate-price` 价格高于 `query-meals` | 外送含运费/打包费，或促销未命中；以外送结果为准 |
| 菜品在 A 店有、B 店无 | 一切以当前 storeCode 的 `query-meals` 为准，不做跨店缓存 |

## 5. 安全

- Token 只存在于本地 MCP 配置（`~/.workbuddy/mcp.json`）的 header 中，不写入本仓库任何文件。
- 所有写操作（下单/取消）都必须先经用户确认；支付动作永远由用户在麦当劳 APP/小程序完成。
