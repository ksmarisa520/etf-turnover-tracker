# -*- coding: utf-8 -*-
"""
宽基ETF量价跟踪器 · 数据管线
============================
纯 Python 标准库，无第三方依赖。每天收盘后跑一次，生成 data/snapshot.js。

用法:
    python update_data.py              # 抓快照（5分钟K + 实时报价）
    python update_data.py --no-live    # 不抓实时报价，只靠5分钟K推算
    python update_data.py --test       # 只跑前3只，验证接口是否可用

数据源（全部公开接口，无需 token）:
    1. 新浪 5分钟K线  quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData
       → 单日成交额（精确到元）、日内48个时点的成交额累计分布、尾盘占比
    2. 腾讯实时快照   qt.gtimg.cn/q=sh510300,...
       → 现价、涨跌幅、成交额、外盘/内盘、总市值、场内份额（GBK编码）

设计要点（这些是从坑里爬出来的，改代码前请先读 README）:
    · 5分钟K的 amount 字段是"元"，直接对一天48根求和就是精确日成交额，
      比"日K成交量 × 均价"更准，所以20日均额也用5分钟K算，不用日K。
    · 沪市ETF的份额字段是 T+1 的：今天收盘跑脚本，拿到的是昨天的份额。
      所以建议每天跑两次：09:45后跑一次（补昨日份额），收盘后再跑一次（当日量价）。
    · 任何一只抓失败都沿用上一份快照的值，绝不写成 0。
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import OrderedDict

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
SNAP_JS = os.path.join(DATA_DIR, "snapshot.js")
SNAP_JSON = os.path.join(DATA_DIR, "snapshot.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ---------------------------------------------------------------- 标的池
# group=core 计入合计与结论；group=watch 只作对照，不计入。
# huijin = 2025年报中央汇金前十大持有人持仓份额（亿份，历史标签，非当前持仓）。
# 这一列可以留空，留空页面显示 "—"，不影响其它功能。
ETFS = [
    # code,   name,                  index,        group, huijin(亿份)
    ("510300", "沪深300ETF华泰柏瑞",  "沪深300",     "core", 735.1),
    ("510310", "沪深300ETF易方达",    "沪深300",     "core", 558.9),
    ("510050", "上证50ETF华夏",       "上证50",      "core", 487.6),
    ("510330", "沪深300ETF华夏",      "沪深300",     "core", 420.3),
    ("159919", "沪深300ETF嘉实",      "沪深300",     "core", 355.0),
    ("512100", "中证1000ETF南方",     "中证1000",    "core", 221.7),
    ("588080", "科创50ETF易方达",     "科创50",      "core", 216.1),
    ("159915", "创业板ETF易方达",     "创业板指",    "core", 170.2),
    ("510500", "中证500ETF南方",      "中证500",     "core", 142.2),
    ("159845", "中证1000ETF华夏",     "中证1000",    "core", 137.3),
    ("560010", "中证1000ETF广发",     "中证1000",    "core", 105.5),
    ("510180", "上证180ETF华安",      "上证180",     "core", 52.3),
    ("159629", "中证1000ETF富国",     "中证1000",    "core", 36.3),
    ("510230", "金融ETF国泰",         "上证180金融", "core", 25.6),
    ("159922", "中证500ETF嘉实",      "中证500",     "core", 23.1),
    ("512500", "中证500ETF华夏",      "中证500",     "core", 20.5),
    ("515800", "中证800ETF汇添富",    "中证800",     "core", 13.1),
    ("159952", "创业板ETF广发",       "创业板指",    "core", 10.9),
    ("588050", "科创50ETF工银",       "科创50",      "core", 9.7),
    ("159901", "深证100ETF易方达",    "深证100",     "core", 7.2),
    ("588000", "科创50ETF华夏",       "科创50",      "watch", None),
    ("159338", "中证A500ETF国泰",     "中证A500",    "watch", None),
    ("512050", "A500ETF华夏",         "中证A500",    "watch", None),
    ("159339", "A500ETF银华",         "中证A500",    "watch", None),
]

LOOKBACK_DAYS = 20      # 基准窗口
HIST_DAYS = 30          # 表格里近30日成交额柱
BARS_PER_DAY = 48       # 5分钟K一天的根数（09:35~11:30 + 13:05~15:00）
KLINE_LEN = 1250        # 一次拉多少根（约26个交易日，够20日基准+冗余）
TAIL_START = "14:35"    # 尾盘区间起点（含）


def http_get(url, referer="https://finance.sina.com.cn", gbk=False, tries=3, timeout=25):
    """带重试的 GET。失败抛最后一次异常，由调用方决定降级策略。"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": referer,
                "Accept": "*/*",
            })
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            return raw.decode("gbk", "ignore") if gbk else raw.decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise last


def tcode(code):
    """6位代码 → 带交易所前缀。5/6 开头沪市，其余深市。"""
    return ("sh" if code[0] in "56" else "sz") + code


# ---------------------------------------------------------------- 5分钟K
def fetch_5min(code):
    """返回 [{day:'2026-11-04', label:'09:35', amt: 元, close: 价}, ...] 按时间升序。"""
    sym = tcode(code)
    urls = [
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        "?symbol=%s&scale=5&ma=no&datalen=%d" % (sym, KLINE_LEN),
        # 备用：jsonp 包装版，个别时段 json 版会返回空
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%%20_=/CN_MarketDataService.getKLineData"
        "?symbol=%s&scale=5&ma=no&datalen=%d" % (sym, KLINE_LEN),
    ]
    rows = None
    for u in urls:
        try:
            txt = http_get(u).strip()
            if "var _=" in txt:
                txt = txt[txt.index("var _=") + 6:].strip().rstrip(";")
            if not txt or txt.strip() == "null":
                continue
            rows = json.loads(txt)
            if rows:
                break
        except Exception as e:  # noqa: BLE001
            print("    5min 取数异常: %s" % e)
    if not rows:
        return []

    out = []
    for r in rows:
        d = r.get("day", "")
        if " " not in d:
            continue
        date, hhmm = d.split(" ", 1)
        out.append({
            "day": date,
            "label": hhmm[:5],
            "amt": float(r.get("amount") or 0),
            "close": float(r.get("close") or 0),
        })
    out.sort(key=lambda x: (x["day"], x["label"]))
    return out


def daily_agg(bars):
    """把5分钟K按交易日聚合：{date: {'amt':元, 'bars':[(label,amt)], 'close':价, 'n':根数}}"""
    days = OrderedDict()
    for b in bars:
        d = days.setdefault(b["day"], {"amt": 0.0, "bars": [], "close": 0.0})
        d["amt"] += b["amt"]
        d["bars"].append((b["label"], b["amt"]))
        d["close"] = b["close"]
    for d in days.values():
        d["n"] = len(d["bars"])
    return days


def shape_of(bars, total):
    """单日累计成交额占比曲线（48个时点）。"""
    cum, out = 0.0, []
    for label, amt in bars:
        cum += amt
        out.append((label, (cum / total) if total > 0 else 0.0))
    return out


# ---------------------------------------------------------------- 实时快照
def fetch_live(codes):
    """腾讯行情快照。GBK 编码、~ 分隔、字段位置要靠位置索引。"""
    url = "https://qt.gtimg.cn/q=" + ",".join(tcode(c) for c in codes)
    txt = http_get(url, referer="https://gu.qq.com/", gbk=True)
    out = {}
    for line in txt.split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        body = line.split("=", 1)[1].strip().strip('"')
        a = body.split("~")
        if len(a) < 46:
            continue

        def n(i, default=0.0):
            try:
                return float(a[i])
            except Exception:  # noqa: BLE001
                return default

        out[a[2]] = {
            "name": a[1],
            "price": n(3),
            "prev_close": n(4),
            "volume_lots": n(6),      # 成交量（手）
            "outer_lots": n(7),       # 外盘 = 主动买（手）
            "inner_lots": n(8),       # 内盘 = 主动卖（手）
            "ts": a[30] if len(a) > 30 else "",
            "pct": n(32),
            "amount_wan": n(37),      # 成交额（万元）
            "turnover": n(38),
            "mktcap_yi": n(45),       # 总市值（亿元）= 价格 × 份额
            "shares": n(72) if len(a) > 72 else 0.0,   # 场内份额（份）
        }
    return out


# ---------------------------------------------------------------- 主流程
def build(no_live=False, limit=None):
    prev = load_prev()
    prev_items = {it["code"]: it for it in prev.get("items", [])}
    shares_hist = dict(prev.get("shares_history") or {})

    pool = ETFS[:limit] if limit else ETFS
    codes = [e[0] for e in pool]

    live = {}
    if not no_live:
        try:
            live = fetch_live(codes)
            print("实时报价: %d/%d 只" % (len(live), len(codes)))
        except Exception as e:  # noqa: BLE001
            print("实时报价失败，全部退回5分钟K口径: %s" % e)

    items, warns = [], []
    for code, name, index, group, huijin in pool:
        print("[%s] %s ..." % (code, name), end="", flush=True)
        bars = fetch_5min(code)
        if not bars:
            old = prev_items.get(code)
            if old:
                items.append(old)
                warns.append("%s 5分钟K抓取失败，沿用上一份快照" % code)
                print(" 沿用旧值")
            else:
                warns.append("%s 5分钟K抓取失败且无历史，已跳过" % code)
                print(" 失败")
            continue

        days = daily_agg(bars)
        full = [d for d, v in days.items() if v["n"] >= BARS_PER_DAY]
        if not full:
            old = prev_items.get(code)
            if old:
                items.append(old)
                warns.append("%s 无完整交易日数据，沿用旧快照" % code)
            print(" 数据不完整")
            continue

        trade_date = full[-1]
        today = days[trade_date]
        # 基准：trade_date 之前的 20 个完整交易日
        base_days = [d for d in full if d < trade_date][-LOOKBACK_DAYS:]
        if len(base_days) < LOOKBACK_DAYS:
            warns.append("%s 基准窗口只有 %d 天（不足20天）" % (code, len(base_days)))
        base_amts = [days[d]["amt"] for d in base_days]
        avg20 = sum(base_amts) / len(base_amts) if base_amts else 0.0

        # 日内分布：对基准日逐时点取均值
        labels = [l for l, _ in today["bars"]]
        shape_map = {}
        for d in base_days:
            cum = 0.0
            tot = days[d]["amt"] or 1.0
            for l, amt in days[d]["bars"]:
                cum += amt
                shape_map.setdefault(l, []).append(cum / tot)
        shape = [[l, round(sum(shape_map.get(l, [0.0])) / max(1, len(shape_map.get(l, [1]))), 6)]
                 for l in labels]

        # 尾盘占比（14:35~15:00 成交额 / 全天）
        def tail_of(day_total, day_bars):
            t = sum(a for l, a in day_bars if l >= TAIL_START)
            return (t / day_total) if day_total > 0 else None

        tail_ratio = tail_of(today["amt"], today["bars"])
        base_tails = [tail_of(days[d]["amt"], days[d]["bars"]) for d in base_days]
        base_tails = [x for x in base_tails if x is not None]
        tail_base = sum(base_tails) / len(base_tails) if base_tails else None

        # 快照一律记「完整交易日」口径；实时叠加交给浏览器去做。
        # 关键：只有当实时报价的日期 == 快照交易日，才允许用实时字段覆盖，
        # 否则会把「今天的盘中成交额」记到「昨天收盘」这一行上，倍数直接算错。
        q = live.get(code)
        live_date = (q["ts"][:8] if q and len(q["ts"]) >= 8 else "")
        same_day = bool(q) and live_date == trade_date.replace("-", "")

        price = today["close"]
        prev_close_f = days[full[-2]]["close"] if len(full) >= 2 else price
        pct = (price / prev_close_f - 1) * 100 if prev_close_f else 0.0
        amount_yi = today["amt"] / 1e8
        outer = inner = 0.0
        turnover = 0.0
        shares_f = 0.0
        mktcap = 0.0
        ts = trade_date.replace("-", "") + "150000"

        if q:
            # 份额随时可取（沪市T+1，见 README）；其余字段只在同日覆盖
            shares_f = q["shares"] or 0.0
            if same_day:
                price = q["price"] or price
                pct = q["pct"]
                outer, inner = q["outer_lots"], q["inner_lots"]
                mktcap = q["mktcap_yi"]
                turnover = q["turnover"]
                ts = q["ts"]
                if q["amount_wan"]:
                    amount_yi = q["amount_wan"] / 1e4

        old = prev_items.get(code) or {}
        if not same_day and old.get("date") == trade_date.replace("-", "") and old.get("outer_lots"):
            # 次日补跑时实时接口已经翻到新的一天，外盘/内盘只对当天有效。
            # 这时不要写 0（会把主动净买入抹成 0），沿用上一份同交易日快照的值。
            outer, inner = old["outer_lots"], old["inner_lots"]
            q_note = " 外盘内盘沿用上次同日快照"
        else:
            q_note = ""
        if not shares_f:
            shares_f = old.get("shares", 0.0)
        if not mktcap:
            mktcap = shares_f * price / 1e8

        items.append(OrderedDict([
            ("code", code),
            ("tcode", tcode(code)),
            ("name", name),
            ("index", index),
            ("group", group),
            ("huijin_yi", huijin),
            ("price", round(price, 4)),
            ("prev_close", round(prev_close_f, 4)),
            ("pct", round(pct, 2)),
            ("amount_yi", round(amount_yi, 4)),
            ("amount_wan", round(amount_yi * 1e4, 2)),
            ("mktcap_yi", round(mktcap, 2)),
            ("shares", shares_f),
            ("shares_yi", round(shares_f / 1e8, 4) if shares_f else 0.0),
            ("turnover", turnover),
            ("outer_lots", outer),
            ("inner_lots", inner),
            ("ts", ts),
            ("date", trade_date.replace("-", "")),
            ("avg20_yi", round(avg20 / 1e8, 4)),
            ("tail_ratio", round(tail_ratio, 4) if tail_ratio is not None else None),
            ("tail_ratio_base", round(tail_base, 4) if tail_base else None),
            ("shape", shape),
            ("hist", [{"date": d, "amt": round(days[d]["amt"] / 1e8, 4)} for d in full[-HIST_DAYS:]]),
        ]))
        print(" ok  %s 成交%.2f亿  20日均%.2f亿  倍数%.2f%s" %
              (trade_date, amount_yi, avg20 / 1e8, (amount_yi / (avg20 / 1e8)) if avg20 else 0, q_note))
        time.sleep(0.25)   # 别把接口打急

    # 份额历史：按"观测日"记账，不是交易日（沪市份额T+1）
    obs_date = max((it["date"] for it in items if it.get("date")), default="")
    if obs_date and items:
        shares_hist[obs_date] = OrderedDict(
            (it["code"], round(it["shares_yi"], 4)) for it in items if it.get("shares_yi")
        )
    prev_keys = sorted(k for k in shares_hist if k < obs_date) if obs_date else sorted(shares_hist)

    trade_date = max((it["date"] for it in items if it.get("date")), default="")
    snap = OrderedDict([
        ("generated_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("trade_date", trade_date),
        ("source", "新浪5分钟K线 quotes.sina.cn（成交额/日内分布）；腾讯行情 qt.gtimg.cn（现价/份额/外盘内盘）"),
        ("huijin_basis", "2025年报汇金前十大持有人口径持仓份额（亿份），历史标签，非当前持仓"),
        ("prev_share_date", prev_keys[-1] if prev_keys else ""),
        ("warns", warns),
        ("items", items),
        ("shares_history", shares_hist),
    ])
    return snap


def load_prev():
    for p in (SNAP_JSON, SNAP_JS):
        if not os.path.exists(p):
            continue
        try:
            if p.endswith(".json"):
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            with open(p, encoding="utf-8") as f:
                t = f.read()
            t = t[t.index("{"):].rstrip().rstrip(";")
            return json.loads(t)
        except Exception as e:  # noqa: BLE001
            print("读取上份快照失败(%s): %s" % (p, e))
    return {}


def save(snap):
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    with open(SNAP_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    with open(SNAP_JS, "w", encoding="utf-8") as f:
        f.write("window.SNAPSHOT = " + payload + ";\n")
    print("\n已写入 %s（%.0f KB）" % (SNAP_JS, len(payload) / 1024))


if __name__ == "__main__":
    no_live = "--no-live" in sys.argv
    limit = 3 if "--test" in sys.argv else None
    if limit:
        print("测试模式：只跑前 %d 只\n" % limit)
    snap = build(no_live=no_live, limit=limit)
    if limit:
        print("\n测试模式不落盘。")
    else:
        save(snap)
        if snap["warns"]:
            print("警告 %d 条：" % len(snap["warns"]))
            for w in snap["warns"]:
                print("  ·", w)
