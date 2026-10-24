#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
McMate —— 麦当劳选择困难症终结者 · 预算内套餐搭配引擎
====================================================

项目主体代码（main.py）。配合 WorkBuddy Skill 使用时的完整对话流程是：
选门店 → 报人数 → 报预算 → 本脚本生成搭配 → 官方 calculate-price 复核 → 下单。

输入：mcd-mcp 的 `query-meals` 返回的原始 JSON（也支持本地导出的菜单文件）
输出：落在预算区间内的搭配方案（JSON 或 Markdown 表格）

设计原则
--------
1. 纯标准库，无第三方依赖，任何装了 Python 3.8+ 的机器直接跑。
2. 不做价格计算以外的任何网络请求；真实价格务必再用 `calculate-price` 复核。
3. 商品角色（汉堡/小食/饮品/甜品/套餐/周边）由菜单分类 + 名称规则推断，
   不写死商品编码，换门店、换季节菜单都能用。
4. 随单购（麦金卡/早餐卡）商品的价格是有条件的，单独标记 requires_card，
   默认降权，避免给用户报出"假便宜"。

用法
----
    python main.py --menu menu.json --budget 30-40
    python main.py --menu menu.json --budget 35 --avoid 辣
    python main.py --menu menu.json --budget 60-80 --people 2 --json
    python main.py --demo            # 内置样例菜单，离线体验
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import product

# ---------------------------------------------------------------- 角色识别规则

BURGER_CATS = ("汉堡", "牛堡", "鱼堡", "卷", "堡")
CHICKEN_CATS = ("炸鸡",)
SIDE_CATS = ("小食", "配菜")
DRINK_CATS = ("饮品", "麦咖啡", "咖啡")
DESSERT_CATS = ("冰淇淋",)
# 真实菜单里「小食甜品/其他」是混排分类，甜品只能靠名称识别
DESSERT_NAMES = ("冰淇淋", "麦旋风", "新地", "圆筒", "甜筒", "派")

# 名称即套餐 / 组合餐的特征词
SET_MARKERS = (
    "套餐", "件套", "随心选", "分享餐", "拼盘", "乐园餐",
    "四件套", "三件套", "双人餐", "多人餐", "宫格", "食盘",
)
# 周边 / 玩具，必须从餐品池里剔除
MERCH_MARKERS = ("周边", "贴", "帽", "捶", "玩具", "手办", "卡牌", "徽章", "杯套")
# 1+1 随心配（需 query-meal-detail 选子项）
COMBO11_MARKERS = ("随心配",)


def classify(name: str, categories: list[str]) -> str:
    """把单个餐品归到 burger / chicken / side / drink / dessert / set / combo11 / merch / other。"""
    joined = "/".join(categories)

    if any(m in name for m in MERCH_MARKERS):
        return "merch"
    if any(m in name for m in COMBO11_MARKERS):
        return "combo11"
    if any(m in name for m in SET_MARKERS):
        return "set"
    if any(k in name for k in DESSERT_NAMES):
        return "dessert"
    if any(c in joined for c in DRINK_CATS):
        return "drink"
    if any(c in joined for c in CHICKEN_CATS):
        return "chicken"
    if any(c in joined for c in BURGER_CATS):
        return "burger"
    if any(c in joined for c in SIDE_CATS):
        return "side"
    if any(c in joined for c in DESSERT_CATS) or "冰淇淋" in name:
        return "dessert"
    return "other"


# 结构模板 -> 基础分（越高越像"一顿正经饭"）
STRUCTURE_SCORE = {
    "套餐": 95,
    "分享餐": 90,
    "堡+小食+饮": 100,
    "堡+饮": 82,
    "堡+小食": 78,
    "随心配+加购": 76,
    "堡": 62,
    "小食+饮": 55,
    "双套餐": 92,
    "多人自由拼": 96,
    "自定义": 70,
}

# 结构族：保证推荐里不同"吃法"各有一条，而不是清一色套餐
FAMILY = {
    "套餐": "set",
    "分享餐": "set",
    "堡+小食+饮": "full",
    "堡+饮": "burger_drink",
    "堡+小食": "burger_side",
    "随心配+加购": "combo11",
    "堡": "burger_only",
    "小食+饮": "snack",
    "双套餐": "double_set",
    "多人自由拼": "multi_build",
    "自定义": "custom",
}


# ---------------------------------------------------------------- 菜单解析
def build_index(menu: dict) -> tuple[list[dict], dict]:
    """把 query-meals 的原始返回整理成平铺的商品列表。"""
    cats_of: dict[str, list[str]] = {}
    for cat in menu.get("categories", []):
        for m in cat.get("meals", []):
            cats_of.setdefault(str(m.get("code")), []).append(cat.get("name", ""))

    items: list[dict] = []
    for code, info in menu.get("meals", {}).items():
        name = info.get("name", "")
        price = float(info.get("currentPrice") or 0)
        orig = float(info.get("originalPrice") or price or 0)
        if price <= 0:
            continue
        role = classify(name, cats_of.get(code, []))
        items.append(
            {
                "code": str(code),
                "name": name,
                "price": round(price, 1),
                "orig": round(max(orig, price), 1),
                "role": role,
                "tags": cats_of.get(code, []),
                "requires_card": bool(info.get("withOrder"))
                or ("随单购" in (info.get("discountType") or "")),
                "requires_student": "学生" in name,
                "time_note": "早餐时段供应" if ("薯饼" in name or "早餐" in name) else None,
                "discount_type": info.get("discountType"),
            }
        )
    return items, cats_of


def pick(bucket: list[dict], avoid: list[str], limit: int) -> list[dict]:
    """按价格升序取前 limit 个（过滤忌口），带麦金卡条件的排后。"""
    def ok(it):
        if any(w and w in it["name"] for w in avoid):
            return False
        return True

    pool = [it for it in bucket if ok(it)]
    pool.sort(key=lambda x: (x["requires_card"], x["price"]))
    return pool[:limit]


# ---------------------------------------------------------------- 搭配生成
def make_combo(label: str, entries: list[tuple[dict, int]], needs_detail: bool = False) -> dict:
    # 同款合并数量，避免出现"咖啡+咖啡"这种拆分展示
    merged: dict[str, list] = {}
    order: list[str] = []
    for it, q in entries:
        if it["code"] in merged:
            merged[it["code"]][1] += q
        else:
            merged[it["code"]] = [it, q]
            order.append(it["code"])
    entries = [(merged[c][0], merged[c][1]) for c in order]

    total = round(sum(it["price"] * q for it, q in entries), 1)
    orig = round(sum(it["orig"] * q for it, q in entries), 1)
    return {
        "structure": label,
        "items": [
            {
                "code": it["code"],
                "name": it["name"],
                "price": it["price"],
                "qty": q,
                "student": it.get("requires_student", False),
                "time_note": it.get("time_note"),
            }
            for it, q in entries
        ],
        "total": total,
        "savings": round(max(orig - total, 0), 1),
        "requires_card": any(it["requires_card"] for it, _ in entries),
        "needs_detail": needs_detail,
    }


def build_combos(items: list[dict], lo: float, hi: float, people: int = 1, avoid: list[str] | None = None) -> list[dict]:
    avoid = avoid or []
    buckets: dict[str, list[dict]] = {}
    for it in items:
        buckets.setdefault(it["role"], []).append(it)

    combos: list[dict] = []

    # 1) 现成套餐：本身就是完整一餐，价格落在区间内直接入选
    for it in pick(buckets.get("set", []), avoid, 40):
        if lo <= it["price"] <= hi:
            if people >= 2 and not any(k in it["name"] for k in ("双人", "多人", "分享", "拼盘", "宫格", "食盘", "十翅")):
                continue
            label = "分享餐" if people >= 2 else "套餐"
            combos.append(make_combo(label, [(it, 1)]))

    # 2) 到店自由拼：堡 (+小食) (+饮品)
    burgers = pick(buckets.get("burger", []), avoid, 12)
    sides = pick(buckets.get("side", []) + buckets.get("chicken", []), avoid, 6)
    drinks = pick(buckets.get("drink", []), avoid, 5)
    desserts = pick(buckets.get("dessert", []), avoid, 4)

    if people == 1:
        for b in burgers:
            combos.append(make_combo("堡", [(b, 1)]))
            for d in drinks:
                combos.append(make_combo("堡+饮", [(b, 1), (d, 1)]))
            for s in sides:
                combos.append(make_combo("堡+小食", [(b, 1), (s, 1)]))
                for d in drinks:
                    combos.append(make_combo("堡+小食+饮", [(b, 1), (s, 1), (d, 1)]))
    else:
        # 多人：N 个堡 + 共享小食 + N 杯饮；以及两个套餐组合
        for bs in product(burgers[:8], repeat=people):
            if len({b["code"] for b in bs}) < people and people <= 2:
                continue
            base = [(b, 1) for b in bs]
            for s in sides[:3]:
                for ds in product(drinks[:4], repeat=people):
                    entries = base + [(s, 1)] + [(d, 1) for d in ds]
                    combos.append(make_combo("多人自由拼", entries))
        picks = pick(buckets.get("set", []), avoid, 8)
        for a, b in product(picks, repeat=2):
            combos.append(make_combo("双套餐", [(a, 1), (b, 1)]))

    # 3) 随心配 1+1 + 加购（甜品/饮品填补预算）
    for c11 in pick(buckets.get("combo11", []), avoid, 3):
        for extra in desserts + drinks[:2]:
            combos.append(
                make_combo("随心配+加购", [(c11, 1), (extra, 1)], needs_detail=True)
            )

    # 4) 过滤 + 打分
    scored = []
    for c in combos:
        if not (lo <= c["total"] <= hi):
            continue
        c["score"] = score_combo(c, lo, hi)
        scored.append(c)

    # 5) 去重（同一组商品只留一条）+ 结构多样化：每种结构最多 1 条，整体最多 3 条
    scored.sort(key=lambda x: -x["score"])
    seen, best_by_family = set(), {}
    for c in scored:
        sig = tuple(sorted((i["code"], i["qty"]) for i in c["items"]))
        if sig in seen:
            continue
        seen.add(sig)
        fam = FAMILY.get(c["structure"], c["structure"])
        if fam not in best_by_family:
            c["reason"] = explain(c, lo, hi)
            best_by_family[fam] = c
    out = sorted(best_by_family.values(), key=lambda x: -x["score"])[:3]
    return out


def score_combo(c: dict, lo: float, hi: float) -> float:
    s = STRUCTURE_SCORE.get(c["structure"], 60)
    span = max(hi - lo, 0.01)
    s += 20 * min(max((c["total"] - lo) / span, 0), 1)      # 预算用足度
    s += min(15, 15 * c["savings"] / max(c["total"], 1))     # 折扣力度
    if c["requires_card"]:
        s -= 12                                              # 需会员卡的价格，降权
    if c["needs_detail"]:
        s -= 2                                               # 需再选子项，略降
    if any(i.get("student") for i in c["items"]):
        s -= 10                                              # 学生专享，普通用户不可用
    return round(s, 1)


def explain(c: dict, lo: float, hi: float) -> str:
    names = " + ".join(f'{i["name"]}×{i["qty"]}' if i["qty"] > 1 else i["name"] for i in c["items"])
    parts = [f"¥{c['total']:.1f} 拿下 {names}"]
    if c["savings"] > 0.1:
        parts.append(f"比原价省 ¥{c['savings']:.1f}")
    if c["requires_card"]:
        parts.append("⚠️ 此价需随单购麦金卡/早餐卡")
    if any(i.get("student") for i in c["items"]):
        parts.append("⚠️ 学生专享，需学生身份")
    notes = {i["time_note"] for i in c["items"] if i.get("time_note")}
    if notes:
        parts.append("⚠️ " + "、".join(sorted(notes)))
    if c["needs_detail"]:
        parts.append("下单前需在随心配中挑选具体子项")
    if c["total"] <= lo + (hi - lo) * 0.35:
        parts.append("预算还有余量，可再加一份甜品")
    return "，".join(parts)


# ---------------------------------------------------------------- 输出
def render_markdown(combos: list[dict], lo: float, hi: float, people: int) -> str:
    head = f"## 预算 ¥{lo:g}–{hi:g} · {people} 人 · 推荐 {len(combos)} 组\n"
    rows = ["| # | 搭配 | 总价 | 结构 | 说明 |", "|---|---|---|---|---|"]
    for idx, c in enumerate(combos, 1):
        names = " + ".join(f'{i["name"]}×{i["qty"]}' if i["qty"] > 1 else i["name"] for i in c["items"])
        rows.append(f"| {idx} | {names} | ¥{c['total']:.1f} | {c['structure']} | {c['reason']} |")
    return head + "\n" + "\n".join(rows)


def parse_budget(text: str) -> tuple[float, float]:
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        raise argparse.ArgumentTypeError("预算格式应为 30-40 或 35")
    if len(nums) >= 2:
        lo, hi = float(nums[0]), float(nums[1])
        return (min(lo, hi), max(lo, hi))
    hi = float(nums[0])
    return (round(hi * 0.6, 1), hi)


# ---------------------------------------------------------------- 样例菜单
DEMO_MENU = {
    "categories": [
        {"name": "精选单人餐", "meals": [{"code": "9900004064"}, {"code": "9900004236"}]},
        {"name": "鸡肉汉堡/卷", "meals": [{"code": "9900005462"}, {"code": "9900016073"}, {"code": "1440"}, {"code": "1406"}, {"code": "1450"}]},
        {"name": "巨无霸/牛鱼肉堡", "meals": [{"code": "9900005466"}, {"code": "1100"}, {"code": "1120"}, {"code": "9900003586"}]},
        {"name": "炸鸡", "meals": [{"code": "1401"}, {"code": "1700"}]},
        {"name": "随心配1+1", "meals": [{"code": "9900013304"}, {"code": "9900015568"}]},
        {"name": "小食甜品/其他", "meals": [{"code": "4810"}, {"code": "521741"}, {"code": "1401"}, {"code": "4437"}, {"code": "9900008754"}, {"code": "515837"}, {"code": "521744"}]},
        {"name": "饮品", "meals": [{"code": "9900008751"}, {"code": "3010"}, {"code": "515280"}, {"code": "6362"}, {"code": "520689"}]},
    ],
    "meals": {
        "9900004064": {"name": "精选单人餐三件套", "currentPrice": "34.5", "originalPrice": "46.5"},
        "9900004236": {"name": "人气超值四件套随心选", "currentPrice": "28", "originalPrice": "57.5", "discountType": "随单购麦金卡优惠", "withOrder": {"membershipCode": "106", "specId": "880"}},
        "9900005462": {"name": "板烧鸡腿堡三件套", "currentPrice": "34.5", "originalPrice": "47.5"},
        "9900016073": {"name": "龙焰鸡腿堡三件套", "currentPrice": "26.9", "originalPrice": "48"},
        "1440": {"name": "麦辣鸡腿汉堡", "currentPrice": "22.5", "originalPrice": "22.5"},
        "1406": {"name": "板烧鸡腿堡", "currentPrice": "23.5", "originalPrice": "23.5"},
        "1450": {"name": "麦香鸡", "currentPrice": "16.5", "originalPrice": "16.5"},
        "9900005466": {"name": "巨无霸三件套", "currentPrice": "37.5", "originalPrice": "50.5"},
        "1100": {"name": "巨无霸", "currentPrice": "26.5", "originalPrice": "26.5"},
        "1120": {"name": "双层吉士汉堡", "currentPrice": "23", "originalPrice": "23"},
        "9900003586": {"name": "双层深海鳕鱼堡", "currentPrice": "24", "originalPrice": "24"},
        "1401": {"name": "麦乐鸡", "currentPrice": "14.5", "originalPrice": "14.5"},
        "1700": {"name": "麦辣鸡翅", "currentPrice": "14.5", "originalPrice": "14.5"},
        "9900013304": {"name": "人气经典随心配", "currentPrice": "14.9", "originalPrice": "14.9"},
        "9900015568": {"name": "精选超值随心配", "currentPrice": "13.9", "originalPrice": "13.9"},
        "4810": {"name": "薯条", "currentPrice": "14", "originalPrice": "14"},
        "521741": {"name": "脆薯饼", "currentPrice": "8", "originalPrice": "8"},
        "4437": {"name": "玉米杯", "currentPrice": "13", "originalPrice": "13"},
        "9900008754": {"name": "经典麦旋风", "currentPrice": "15", "originalPrice": "15"},
        "515837": {"name": "圆筒冰淇淋", "currentPrice": "5", "originalPrice": "5"},
        "521744": {"name": "薯条脆卜卜毛绒周边", "currentPrice": "24.9", "originalPrice": "29.9", "discountType": "随单购麦金卡优惠", "withOrder": {"membershipCode": "106", "specId": "880"}},
        "9900008751": {"name": "可乐", "currentPrice": "9.5", "originalPrice": "9.5"},
        "3010": {"name": "雪碧", "currentPrice": "9.5", "originalPrice": "9.5"},
        "515280": {"name": "阳光柠檬红茶", "currentPrice": "9.5", "originalPrice": "9.5"},
        "6362": {"name": "100% 苹果汁(盒装)", "currentPrice": "11.5", "originalPrice": "11.5"},
        "520689": {"name": "鲜萃咖啡", "currentPrice": "10", "originalPrice": "10"},
    },
}


# ---------------------------------------------------------------- 入口
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="麦当劳选择困难症终结者：预算内自动搭配")
    ap.add_argument("--menu", help="query-meals 返回的 JSON 文件路径")
    ap.add_argument("--budget", required=False, default="30-40", help="预算，如 30-40 或 35")
    ap.add_argument("--people", type=int, default=1, help="人数，默认 1")
    ap.add_argument("--avoid", default="", help="忌口关键词，逗号分隔，如 辣,鱼")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    ap.add_argument("--demo", action="store_true", help="使用内置样例菜单")
    args = ap.parse_args(argv)

    lo, hi = parse_budget(args.budget)

    if args.demo or not args.menu:
        menu = DEMO_MENU
    else:
        with open(args.menu, "r", encoding="utf-8") as f:
            payload = json.load(f)
        menu = payload.get("data", payload)

    items, _ = build_index(menu)
    avoid = [w.strip() for w in args.avoid.split(",") if w.strip()]
    combos = build_combos(items, lo, hi, people=args.people, avoid=avoid)

    if not combos:
        if args.json:
            print(json.dumps({"budget": [lo, hi], "people": args.people, "combos": []}, ensure_ascii=False))
        else:
            print(f"预算 ¥{lo:g}–{hi:g} 内没有找到合适搭配，建议放宽 5–10 元或减少忌口限制。")
        return 1

    if args.json:
        print(json.dumps({"budget": [lo, hi], "people": args.people, "combos": combos}, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(combos, lo, hi, args.people))
    return 0


if __name__ == "__main__":
    sys.exit(main())
