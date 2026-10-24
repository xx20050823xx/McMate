# McMate · 麦当劳选择困难症终结者

> **McMate** = McDonald's + 搭子，你的麦记饭搭子。告诉我你的预算，它用麦当劳官方 MCP 拉实时菜单，几秒给你一份"有主食、有小食、有饮料、还不超预算"的搭配。

## 这是什么

一个 WorkBuddy Skill（也包含一份可独立运行的 Python 搭配引擎）。

传统点餐助手让你"翻菜单自己选"；这个 skill 解决的是**决策本身**，并且按真实决策顺序引导你走完四步：

1. **先选门店**：自动列出附近可点餐的门店（名称 / 地址 / 距离），回复序号即可
2. **再报人数**：一个人还是几个人
3. **再报预算**：一句话给区间，或者给个上限
4. **拿方案**：3 组结构不同的搭配，每组都经过官方 `calculate-price` 算价

可选：直接下单（到店自取 / 得来速 / 麦乐送 / 团餐）。

真实示例（2026-10-24，麦当劳玉溪极中心餐厅，数据来自 mcd-mcp）：

| # | 搭配 | 实际价 | 结构 | 说明 |
|---|---|---|---|---|
| 1 | 龙焰鸡腿堡 + 中薯条 + 小杯鲜萃咖啡 | ¥39.9 | 堡+小食+饮 | 官方算价，比原价省 ¥8.1 |
| 2 | 巨无霸三件套 | ¥37.5 | 套餐 | 套餐直降 ¥13 |
| 3 | 不素之霸双层牛堡 + 麦辣鸡翅 | ¥40.0 | 堡+小食 | 纯肉党，不加饮料 |

## 为什么不是"随便推一个"

预算约束下的组合搜索，要处理真实菜单里的一堆坑：

| 坑 | 本项目的处理 |
|---|---|
| 收藏门店返回 `distance=0`，经纬度也全是 0 | 识别为"收藏店没有坐标"，不代表离你 0 米，仍按返回顺序展示 |
| 页面上 28 元的四件套其实是"随单购麦金卡"价 | 识别 `withOrder` / `discountType`，标注"需麦金卡"并降权 |
| 「随心配 1+1」是可选组合，不是固定套餐 | 标记 `needs_detail`，下单前先拉 `query-meal-detail` 选子项 |
| 菜单里混着毛绒玩具、贴纸、帽子 | 按名称特征剔除周边商品 |
| 「小食甜品/其他」是混排分类，薯条和麦旋风在同一栏 | 按商品名而非分类名判断角色 |
| 学生专享、早餐时段限定的商品 | 自动标注，普通用户场景降权 |
| `query-meals` 报价单位是元，`calculate-price` 是分 | 脚本统一按元计算，展示层永远用官方算价 |

## 目录结构

```
mcmate/
├── SKILL.md                      # WorkBuddy Skill 定义（主入口，含四步交互流程）
├── main.py                       # 项目主体代码：搭配引擎（分类 → 组合 → 打分 → 输出，纯标准库）
├── references/
│   └── mcd-mcp-tools.md          # mcd-mcp 工具参数矩阵、真实调用样例、错误处理
├── assets/
│   └── sample-menu.json          # 真实菜单快照（玉溪极中心店），用于离线测试
├── README.md                     # 本文件
├── MCP_INTEGRATION.md            # MCP Server / Tool / 调用流程 / 业务价值
└── workbuddy.md                  # 使用 WorkBuddy 开发本项目的对话上下文
```

## 安装

### 方式一：作为 WorkBuddy Skill（推荐）

1. 把整个目录复制到 `~/.workbuddy/skills/mcmate/`
   ```bash
   cp -r mcmate ~/.workbuddy/skills/
   ```
2. 确认 `mcd-mcp` 已连接：聊天窗口左下角「+」→ 连接器管理 → 自定义连接器 → 找到 `mcd-mcp` 点「信任」。
3. 新开一个会话，直接说："帮我搭配一份 30-40 元的麦当劳"。

### 方式二：只用 Python 引擎（不需要 MCP）

```bash
# 无依赖，Python 3.8+
python main.py --demo --budget 30-40
```

需要真实菜单时，把任意一次 `query-meals` 的 `data` 字段存成 JSON：

```bash
python main.py --menu menu.json --budget 30-40 --people 1
```

### 方式三：配置 mcp-mcd（MCP 客户端）

`~/.workbuddy/mcp.json`：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer <在 open.mcd.cn 申请的 token>" }
    }
  }
}
```

> 注意：`Authorization` 必须是 `Bearer <token>`，漏掉 `Bearer ` 前缀会 401。

## 使用示例

**对话式（WorkBuddy 内）**

```
你：不知道吃啥，帮我搭配一份麦当劳
AI：附近可以点的麦当劳有这几家：
    1. 玉溪极中心餐厅 — 棋阳路极中心购物广场一层
    2. 玉溪聂耳广场餐厅 — 聂耳路 12 号
    在哪家吃？回复序号即可
你：1
AI：好的，玉溪极中心店。几个人吃？
你：就我一个
AI：预算大概多少？20-30 吃得简单，30-40 是标准一人食。
你：35 左右
AI：（query-meals → 搭配引擎 → 官方算价）
    1. 龙焰鸡腿堡三件套 ¥26.9（省 ¥21.1）
    2. 麦香鸡 + 苹果汁 ¥28.0
    3. 麦香鸡 + 玉米杯 ¥29.5
你：1
AI：（calculate-price 复核 → create-order）已下单，请扫码支付…
你：支付完成
AI：（query-order）订单已支付，正在备餐…
```

> 如果你一开始就把信息说全（"玉溪极中心那家，两个人，40 块"），skill 会跳过提问直接出方案，只回读确认一次。

**命令行**

```bash
python main.py --demo --budget 20-30
python main.py --menu assets/sample-menu.json --budget 60-80 --people 2
python main.py --menu menu.json --budget 35 --avoid 辣,鱼 --json
```

参数：

| 参数 | 说明 | 示例 |
|---|---|---|
| `--budget` | 预算区间或上限 | `30-40`、`35` |
| `--people` | 人数 | `1`、`2` |
| `--avoid` | 忌口关键词，逗号分隔 | `辣,鱼` |
| `--menu` | query-meals 返回的 JSON | `menu.json` |
| `--demo` | 使用内置样例菜单 | — |
| `--json` | 输出 JSON（给程序用） | — |

## 目标用户

- **选择困难的重度用户**：每次点麦当劳都要翻 5 分钟菜单的人。
- **预算敏感的学生 / 上班族**：明确"就 30 块"，需要在这个数内吃到最合理的组合。
- **帮同事/家人代点的人**："两个人 70 块，一个人不吃辣"。
- **Agent 开发者**：想看一个 MCP 工具如何被 Skill 封装成可复用的决策流程（`references/mcd-mcp-tools.md` 有完整的参数矩阵和真实响应）。
- **门店/运营同学**：用同样的方法做"预算内推荐"菜单分析。

## 已知限制

- 只支持中国大陆地区（麦当劳 MCP 服务范围），不含港澳台。
- 菜单与价格随门店、时段、会员身份变化，一切以 `query-meals` / `calculate-price` 实时返回为准。
- 「随心配」类商品需要用户选子项，脚本只做占位提示，不代为选择。
- 下单后支付需用户在麦当劳 APP / 小程序完成，本 skill 不接触任何支付凭证。

## 免责声明

本项目为开发者个人作品，非麦当劳官方产品。使用麦当劳 MCP 服务需遵守 [open.mcd.cn](https://open.mcd.cn) 的服务条款。
