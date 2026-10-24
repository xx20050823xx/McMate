# WorkBuddy 开发对话上下文

本文件记录本项目在 WorkBuddy 中的完整开发过程，用于核验联动活动奖励条件，也可作为复现指南。

## 基本信息

| 项 | 值 |
|---|---|
| 开发工具 | WorkBuddy（Agent 模式） |
| 开发时间 | 2026-10-24 16:33 ~ 17:05（GMT+8） |
| 工作目录 | `C:\Users\xx200\WorkBuddy\Claw` |
| 产出目录 | `mcmate/` |
| MCP Server | `mcd-mcp`（`https://mcp.mcd.cn`，麦当劳中国官方） |
| 运行时 | Python 3.13.12（脚本无第三方依赖） |

## 需求原文

> 帮我调用 mcd-mcp 辅助制作一个 skill，项目名称为"麦当劳选择困难症终结者"，项目描述：在用户给出价格范围时自动帮助用户搭配套餐。
>
> 项目所需包含的文件：
> 1. README.md —— 项目介绍、安装方法、使用示例和目标用户（必须，文件名不可改动）
> 2. MCP_INTEGRATION.md —— 说明实际使用的麦当劳 MCP Server、Tool、调用流程和业务价值（必须，文件名不可改动）
> 3. 源代码 —— 项目主体代码或可运行内容（必须，形式不限）
> 4. workbuddy.md —— 使用 WorkBuddy 开发时的对话上下文，用于核验是否符合联动活动奖励条件，可自行导出（参加 WorkBuddy 专项奖励时必须提交，文件名不可改动）

## 对话与执行时间线

**16:33 · 启用 MCP**

- 用户："使用mcd-mcp"
- 检查 `~/.workbuddy/mcp.json`，发现已存在 `mcd-mcp` 配置，但 `Authorization` 头缺少 `Bearer ` 前缀。
- 修正为 `Authorization: Bearer <token>`，提示用户到连接器管理页信任该服务。

**16:35 · 连通性验证**

- 用户："现在mcd-mcp正常了吗"
- 工具列表中出现 `mcp__mcd-mcp__*` 共 30+ 个工具。
- 调用 `order-list` 实测成功（traceId `3dbae6b4...`），返回 2 笔历史订单：
  - 2026-08-18 天津滨海机场T2餐厅 · 麦香鸡中套餐 ¥36
  - 2026-05-03 昆明朝云街餐厅 · 人气超值四件套随心选 ¥37.9
- 结论：链路正常，可进入开发。

**16:48 · 创建 skill 任务**

- 用户提出完整需求（见上）。
- 加载 `skill-creator` 规范，确定项目结构：`SKILL.md` + `main.py`（主体代码）+ `references/` + `assets/` + 三份必须文档。

**16:49 · 采集真实数据（不臆造菜单）**

- `query-nearby-stores(searchType=1, beType=1)` → `storeCode=3570064`，麦当劳玉溪极中心餐厅（traceId `de1ea665...`）
- `query-meals(storeCode=3570064, orderType=1, beType=1)` → 12 个分类、60+ 商品（traceId `cddd1a8f...`）
- 观察到三个真实世界的坑：
  1. `人气超值四件套随心选` 28 元是"随单购麦金卡优惠"价，`withOrder` 字段非空；
  2. `人气经典随心配` 14.9 元是 1+1 可选项，需 `query-meal-detail` 选子项；
  3. 菜单里混着"薯条脆卜卜毛绒周边""旋旋星愿贴"等周边商品。

**16:50 · 编写搭配引擎**

- 新建 `main.py`（纯标准库）：
  - 角色识别：汉堡 / 炸鸡 / 小食 / 饮品 / 甜品 / 套餐 / 随心配 / 周边；
  - 组合枚举：现成套餐、堡+小食+饮、堡+饮、堡+小食、随心配+加购、双人自由拼、双套餐；
  - 打分：结构分 + 预算用足度 + 折扣力度 − 麦金卡降权 − 学生专享降权；
  - 多样性：每种结构最多留 1 条，整体最多 3 条。
- 内置 `--demo` 样例菜单与 `--json` 输出。

**16:51 · 第一轮测试与修复**

| 问题 | 现象 | 修复 |
|---|---|---|
| 分类误判 | 「小食甜品/其他」混排分类把薯条判成甜品，导致"堡+薯+饮"全被过滤 | 甜品改为按名称识别（冰淇淋/麦旋风/新地/圆筒/派），分类兜底 |
| 结构单一 | 三组推荐清一色"套餐" | 引入结构族（FAMILY），每种吃法最多 1 条 |
| 双人无结果 | 双人场景全部结构叫"自定义"，去重后只剩 1 条 | 拆为"多人自由拼""双套餐"，分别入族 |
| 同款重复展示 | 出现"鲜萃咖啡 + 鲜萃咖啡" | `make_combo` 合并同 code 数量 |

**16:53 · 官方算价复核（关键验证）**

- 用 `calculate-price` 复核第一组候选（龙焰鸡腿堡 + 中薯条 + 小杯鲜萃咖啡）：
  - 返回 `price=3990` 分（¥39.90），`discount=810` 分（¥8.10），traceId `a81b7c48...`
  - 脚本估算 ¥39.9 / 省 ¥8.1 —— 完全一致。
- 取餐方式返回 `eat-in`（堂食）/ `take-in-store`（外带），确认可作为 `create-order` 的 `takeWayCode`。

**16:56 · 真实菜单快照入库**

- 将真实菜单节选写成 `assets/sample-menu.json`（保留真实编码与价格），供离线测试与复现。
- 实测：`--menu assets/sample-menu.json --budget 30-40` 输出与线上一致。

**17:00 · 文档落盘**

- `SKILL.md`：六步流程（解析→找店→拉菜单→组合→算价→推荐→下单）+ 坑位表。
- `references/mcd-mcp-tools.md`：工具参数矩阵、真实请求/响应、错误处理。
- `README.md`、`MCP_INTEGRATION.md`、`workbuddy.md`（本文件）。

**17:00 · v1.1 交互流程改版（追加需求）**

- 用户追加需求原文：
  > 改进一下，检测距离用户附近的门店后询问用户在哪个门店就餐，用户做出选择后询问用户就餐人数，然后询问用户就餐预算价格区间，最后根据以上信息为客户选择套餐
- 改版内容：把原来"先要预算 → 再找店"的顺序，改成 **门店 → 人数 → 预算 → 推荐** 的四步引导式对话，并明确"一问一答、不三连问、信息已给则跳过"的交互原则。
- 同步改动：
  - `SKILL.md`：重写标准流程为 Step 1 门店（含编号展示格式、回答解析表、`distance=0` 处理）/ Step 2 人数 / Step 3 预算（含参考锚点话术）/ Step 4-8 拉菜单、组合、算价、推荐、下单；新增"完整对话示例"。
  - `references/mcd-mcp-tools.md`：补充"门店选择三个要点"（收藏店 distance=0 无坐标、searchType=2 才需要 city+keyword、换店必须重新拉菜单）。
  - `README.md`：使用示例改为四步对话；坑位表补充 `distance=0`。
  - `MCP_INTEGRATION.md`：调用流程图加入"选店 → 人数 → 预算"三段，并说明为什么门店要放第一步（菜单按 storeCode 出价）。
- 设计理由：菜单与价格是"按门店"取的，先定门店才能保证后续报价都是该店真实在售；且"去哪吃"比"花多少钱"更容易回答，符合真实决策顺序。

**17:02 · 验证位置搜索降级路径**

- 调用 `query-nearby-stores(searchType=2, beType=1, city="昆明", keyword="朝云街")`（traceId `0532a221...`），返回 5 家门店，`distance` 为米（1355 / 1411 / 4958 / 5156 / 9576）。
- 据此在文档中明确：`searchType=1` 收藏店的 `distance=0` 不可用于排序；`searchType=2` 的 `distance` 单位是米；展示时 >1000 转 km、`distance=0` 不显示距离。

**17:10 · v1.2 项目更名 McMate**

- 用户确认英文名 **McMate**（McDonald's + 搭子，麦记饭搭子），中文副标题保留「麦当劳选择困难症终结者」。
- 改动：目录 `mcd-choice-terminator/` → `mcmate/`；`SKILL.md` 的 `name: mcmate`、描述前缀加 "McMate（…）"；README / MCP_INTEGRATION / references / 本文件全文替换旧名；技能目录与安装包同步更名。

**17:27 · v1.3 源代码统一为 main.py**

- 用户要求："注意：使用 Python 进行编写，源代码的文件名为 main.py"。
- 改动：`scripts/combo_builder.py` → 根目录 **`main.py`**（`scripts/` 目录移除）；`main.py` 文件头补充项目名与四步流程说明；SKILL.md 调用路径改为 `<skill_dir>/main.py`；README 目录结构与全部示例命令、MCP_INTEGRATION 流程图、references 调用链同步更新。
- 验证：`python main.py --demo --budget 30-40` 输出与之前一致（三组结构不同的方案）。

## 设计决策记录

1. **两段式：本地搜索 + 官方校验**
   组合搜索放在本地脚本（快、可离线、可测试），最终报价必须过 `calculate-price`。理由：菜单接口的 `currentPrice` 不含运费/打包费/优惠券叠加，直接拿来报价会出错。

2. **角色由规则推断，不写死商品编码**
   用"分类名 + 商品名特征词"判断，换门店、换季节菜单（如 2026 年 10 月的"龙焰"系列）无需改代码。代价是规则需要维护，已在坑位表里记录边界。

3. **随单购价格降权而非剔除**
   这类商品确实便宜且真实存在，剔除会损失好方案；降权 12 分 + 显式标注"需麦金卡"，把选择权交回用户。

4. **结构多样性 > 单一最省**
   用户说"选择困难"，给三个一模一样的"套餐"没有意义。按结构族去重，保证"套餐 / 自由拼 / 加购"各有一条。

5. **脚本零依赖**
   比赛与开源场景下，`python xxx.py` 能跑比"先装 requirements.txt"重要得多。

## 可复现命令

```bash
# 1. 离线体验（内置样例）
python main.py --demo --budget 30-40

# 2. 真实菜单（assets 内为 2026-10-24 玉溪店快照）
python main.py --menu assets/sample-menu.json --budget 30-40
python main.py --menu assets/sample-menu.json --budget 60-80 --people 2
python main.py --menu assets/sample-menu.json --budget 35 --avoid 辣

# 3. 全链路（需 mcd-mcp 已连接）
#    query-nearby-stores → query-meals → 存 menu.json → main.py
#    → calculate-price 复核 → create-order
```

## 事实核验清单

| 事项 | 证据 |
|---|---|
| MCP 已启用 | `order-list` 返回 2 笔历史订单，traceId `3dbae6b4ac33c4d8d9baf1834d4874ad` |
| 门店数据真实 | `storeCode=3570064`，玉溪极中心餐厅，traceId `de1ea6656ddc6add878c35e8786d88ba` |
| 菜单数据真实 | 12 分类、60+ 商品，traceId `cddd1a8f5d4c21b1c2a4f4770e63669d` |
| 算价闭环成立 | `calculate-price` 返回 3990 分，traceId `a81b7c487de73c2f73e04f0bc8736671` |
| 算法可复现 | `assets/sample-menu.json` + 三条 CLI 命令，结果稳定 |
