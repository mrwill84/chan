"""为什么「5分钟1类点确认30分钟段结束」失败了：一个例子 + 全体总览。

用法：PYTHONPATH=. python _plot/sublevel_why.py <1|2>
  1 例子  600004 那个 3买：上涨途中每个小顶都冒出5分钟1卖，3买 跟着亮、灭五次
  2 总览  55 个 3买 按「基线确认时刻」对齐：提前那一截是断的，确认之后才连续
数据：.tmp/sublevel_example_data.py 先跑出例子；总览直接读 .tmp/sublevel/*.steps.jsonl。
"""
from __future__ import annotations

import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

SUB = "#1f77b4"      # 5分钟确认那一组
BASE = "#7f7f7f"     # 基线
SIG = "#d62728"      # 5分钟1卖
INK = "#222222"


def fig1(out: str):
    d = json.load(open(".tmp/sublevel_example_600004.json", encoding="utf-8"))
    bars, steps = d["bars"], d["steps"]
    t_first = steps[0]["t"] - 120          # bars[0] 对应的30分钟步号
    x_of = {b[0]: i for i, b in enumerate(bars)}
    xs = [x_of[s["ts"]] for s in steps]

    fig, (ax, st) = plt.subplots(2, 1, figsize=(15, 8.2), sharex=True,
                                 gridspec_kw={"height_ratios": [4, 1]})
    ax.plot(range(len(bars)), [b[4] for b in bars], color=INK, lw=1.0, label="30分钟收盘价")
    ax.axhspan(d["zd"], d["zg"], color=BASE, alpha=0.12, label=f"中枢 [ZD {d['zd']:.2f}, ZG {d['zg']:.2f}]")
    ax.axhline(d["zg"], color=BASE, lw=0.8, ls="--")
    px = x_of[d["point_ts"]]
    ax.scatter([px], [d["point_price"]], marker="*", s=260, color=SUB, zorder=6,
               edgecolor="white", lw=1, label="3买（回抽低点不破 ZG）")
    ax.annotate("3买\n" + d["point_ts"][5:16], (px, d["point_price"]),
                xytext=(-10, -46), textcoords="offset points", ha="center",
                fontsize=9, color=INK)

    # 3买 亮着的步：竖条底色
    for s, x in zip(steps, xs):
        if s["vis_sub"] and not s["vis_base"]:
            ax.axvspan(x - 0.5, x + 0.5, color=SUB, alpha=0.10, lw=0)

    # 每个新出现的5分钟1卖，标在它所在的那根30分钟上
    seen = set()
    for s in steps:
        sl = s["sub_latest"]
        if s["tail_dir"] != "UP" or not sl or sl in seen:
            continue
        seen.add(sl)
        k = next((i for i, b in enumerate(bars) if b[0] >= sl), None)
        if k is None:
            continue
        ax.scatter([k], [bars[k][2]], marker="v", s=70, color=SIG, zorder=7)
        ax.annotate("5分钟1卖", (k, bars[k][2]), xytext=(0, 10),
                    textcoords="offset points", ha="center", fontsize=8, color=SIG)

    # 撤回：上一步亮、这一步灭（基线还没确认之前）。原因逐个核对过：
    # 5分钟1卖还在、但落到一根新的还在走的向上笔上 = 创新高；1卖自己没了 = 5分钟重画
    for a, b, x in zip(steps, steps[1:], xs[1:]):
        if a["vis_sub"] and not b["vis_sub"]:
            why = "5分钟1卖\n被重画掉" if b["sub_latest"] is None else "创新高"
            ax.annotate(f"{why}\n→ 撤回", (x, bars[x][4]), xytext=(0, -46),
                        textcoords="offset points", ha="center", fontsize=8,
                        color=INK, bbox=dict(boxstyle="round,pad=0.2", fc="white",
                                             ec=BASE, lw=0.5),
                        arrowprops=dict(arrowstyle="-", color=BASE, lw=0.6))

    bx = next(x for s, x in zip(steps, xs) if s["vis_base"])
    for a in (ax, st):
        a.axvline(bx, color=INK, lw=1.0, ls=":")
    ax.annotate("基线确认（67课特征序列）\n" + bars[bx][0][5:16], (bx, ax.get_ylim()[1]),
                xytext=(6, -28), textcoords="offset points", fontsize=9, color=INK)

    ax.set_ylabel("价格（后复权）")
    ax.legend(loc="upper left", fontsize=9, frameon=False)
    ax.set_title(
        "600004 30分钟：3买 出现之后，向上离开段一路小幅创新高\n"
        "每冲出一个小顶，5分钟就出一个1卖 → 判「向上段结束」→ 3买亮（蓝底）；"
        "再创新高 → 撤回 → 3买灭。亮灭五次，最后稳定下来的那一刻和基线是同一天",
        fontsize=11, loc="left")

    # 下面一条：亮/灭时间线
    for row, key, color, name in ((1, "vis_sub", SUB, "5分钟确认"),
                                  (0, "vis_base", BASE, "基线")):
        on = [x for s, x in zip(steps, xs) if s[key]]
        st.scatter(on, [row] * len(on), marker="|", s=180, color=color, lw=2)
    st.set_yticks([0, 1], ["基线", "5分钟确认"])
    st.set_ylim(-0.6, 1.6)
    st.set_xlabel("30分钟K线（竖线 = 那一刻引擎输出里有这个 3买）")
    ticks = list(range(0, len(bars), 40))
    st.set_xticks(ticks, [bars[i][0][5:10] for i in ticks])
    for a in (ax, st):
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


def _presence(code: str, arm: str) -> dict[int, list[int]]:
    """{3买所在K线: [引擎输出里有它的那些步]}，从逐步落盘结果还原。"""
    from backtest.sublevel import _read_steps

    st = _read_steps(f".tmp/sublevel/{code}_{arm}.steps.jsonl")
    out: dict[int, list[int]] = {}
    for t in sorted(st):
        for p in st[t].get("bspoints", []):
            if p["kind"] == 3 and p["side"] == "buy":
                out.setdefault(p["bar"], []).append(t)
    return out


def fig2(out: str):
    from pathlib import Path
    from statistics import median

    codes = sorted({p.name.split("_")[0]
                    for p in Path(".tmp/sublevel").glob("*_sub.steps.jsonl")})
    rows = []       # (提前量, [亮着的步 − 基线确认步])
    for c in codes:
        sub, base = _presence(c, "sub"), _presence(c, "base")
        for bar, tl in sub.items():
            if bar in base:
                b0 = base[bar][0]
                rows.append((b0 - tl[0], [t - b0 for t in tl]))
    rows.sort(key=lambda r: r[0])
    early = [r for r in rows if r[0] > 0]
    frac = median(sum(1 for x in r[1] if x < 0) / r[0] for r in early)
    gaps_before = sum(1 for _, xs in rows for a, b in zip(xs, xs[1:]) if b != a + 1 and b <= 0)
    gaps_after = sum(1 for _, xs in rows for a, b in zip(xs, xs[1:]) if b != a + 1 and b > 0)

    lo, hi = -280, 60
    fig, ax = plt.subplots(figsize=(13, 9))
    for y, (lead, xs) in enumerate(rows):
        if lead > 0:   # 提前的那一截整段画成浅色底线，亮着的部分盖上深色
            ax.plot([max(-lead, lo), 0], [y, y], color=SUB, alpha=0.18, lw=4,
                    solid_capstyle="butt")
        xs = [x for x in xs if lo <= x <= hi]
        if not xs:
            continue
        runs, s = [], xs[0]
        for a, b in zip(xs, xs[1:]):
            if b != a + 1:
                runs.append((s, a))
                s = b
        runs.append((s, xs[-1]))
        for a, b in runs:
            ax.plot([a - 0.5, b + 0.5], [y, y], color=SUB, lw=4, solid_capstyle="butt")
    ax.axvline(0, color=INK, lw=1.2, ls=":")
    ax.text(2, len(rows) + 0.5, "基线确认（67课特征序列）", fontsize=9, color=INK)
    ax.set_xlim(lo, hi)
    ax.set_ylim(-1, len(rows) + 2)
    ax.set_yticks([])
    ax.set_ylabel(f"{len(rows)} 个 30分钟3买（两组共有的），按提前量排序")
    ax.set_xlabel("相对基线确认时刻的30分钟K线数（负数 = 比基线早）")
    ax.set_title(
        f"30只票：5分钟确认让 3买 早出现（中位）{median(r[0] for r in early):.0f} 根，"
        f"但提前的那一截中位只有 {frac:.0%} 的时间亮着\n"
        f"深色 = 那一刻引擎输出里有这个3买；浅色 = 提前段里它不在的时候。"
        f"闪断 {gaps_before} 次发生在基线确认之前，之后只有 {gaps_after} 次",
        fontsize=11, loc="left")
    ax.spines[["top", "right", "left"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


def fig3(out: str):
    """3买 那个点本身：5分钟当下就判出了回抽低点，慢的是引擎的「离开」确认。"""
    p = json.load(open(".tmp/sublevel_point_600004.json", encoding="utf-8"))
    ex = json.load(open(".tmp/sublevel_example_600004.json", encoding="utf-8"))
    b30, b5 = p["b30"], p["b5"]
    x30 = {b[0]: i for i, b in enumerate(b30)}
    zg, zd = ex["zg"], ex["zd"]

    fig, (ax, bx) = plt.subplots(2, 1, figsize=(15, 9.5),
                                 gridspec_kw={"height_ratios": [3, 2.2]})
    ax.plot(range(len(b30)), [b[3] for b in b30], color=INK, lw=1.0)
    ax.axhspan(zd, zg, color=BASE, alpha=0.12)
    ax.axhline(zg, color=BASE, lw=0.8, ls="--")
    ax.text(2, zg + 0.03, f"中枢上沿 ZG {zg:.2f}", fontsize=9, color=BASE)

    top_ts, low_ts = "2025-07-24 14:30:00", "2025-07-31 15:00:00"
    tx, lx = x30[top_ts], x30[low_ts]
    ax.annotate("向上离开中枢", (tx, b30[tx][1]), xytext=(-70, 18),
                textcoords="offset points", fontsize=9, color=INK,
                arrowprops=dict(arrowstyle="->", color=INK, lw=0.7))
    ax.annotate("", (lx, b30[lx][2]), xytext=(tx, b30[tx][1]),
                arrowprops=dict(arrowstyle="->", color=SUB, lw=1.2, alpha=0.7))
    ax.text((tx + lx) / 2 + 3, (b30[tx][1] + b30[lx][2]) / 2 + 0.05, "回试",
            fontsize=9, color=SUB)
    ax.scatter([lx], [b30[lx][2]], marker="*", s=300, color=SUB, zorder=6,
               edgecolor="white", lw=1)
    ax.annotate(f"3买：回试低点 {b30[lx][2]:.2f} 没跌破 ZG\n07-31 15:00", (lx, b30[lx][2]),
                xytext=(0, -52), textcoords="offset points", ha="center",
                fontsize=9, color=INK)

    marks = [(low_ts, SUB, "① 5分钟判出\n07-31 15:00"),
             ("2025-08-14 10:00:00", BASE, "② 旧次级别口径\n08-14（之后亮灭5次）"),
             ("2025-09-11 11:30:00", INK, "③ 引擎基线输出\n09-11")]
    ymax = max(b[1] for b in b30)
    for ts, color, text in marks:
        x = x30[ts]
        ax.axvline(x, color=color, lw=1.2, ls=":")
        ax.text(x + 1, ymax, text, fontsize=9, color=color, va="top")
    lag = x30["2025-09-11 11:30:00"] - lx
    ax.set_title(
        "600004：3买 那个点，5分钟在它那根K线收盘时就判出来了\n"
        f"慢的不是「回试有没有结束」，而是引擎还要等回试之后再走完一段才确认「离开」——"
        f"比①晚了 {lag} 根30分钟K线",
        fontsize=11, loc="left")
    ticks = list(range(0, len(b30), 40))
    ax.set_xticks(ticks, [b30[i][0][5:10] for i in ticks])
    ax.set_ylabel("30分钟收盘价")

    # 下面：5分钟，回抽见底那几天
    bx.plot(range(len(b5)), [b[3] for b in b5], color=INK, lw=0.9)
    x5 = {b[0]: i for i, b in enumerate(b5)}
    for ts, (first, last, n) in sorted(p["sub_1buy"].items()):
        if ts not in x5:
            continue
        k = x5[ts]
        final = last >= "2025-08-05"
        bx.scatter([k], [b5[k][2]], marker="^", s=110 if final else 70,
                   color=SUB if final else "white", edgecolor=SUB, lw=1.5, zorder=6)
        note = (f"5分钟1买 @{ts[11:16]}\n{first[5:16]} 收盘起出现，一直保留"
                if final else
                f"5分钟1买 @{ts[11:16]}（较早的低点）\n{first[11:16]} 出现，"
                f"{last[11:16]} 后被更低的点取代")
        bx.annotate(note, (k, b5[k][2]), xytext=(70, 18) if final else (-80, -42),
                    textcoords="offset points", ha="left" if final else "right",
                    fontsize=8.5, color=INK,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=BASE, lw=0.5),
                    arrowprops=dict(arrowstyle="-", color=BASE, lw=0.6))
    lo5 = min(b[2] for b in b5)
    bx.set_ylim(lo5 - 0.08, max(b[1] for b in b5) + 0.03)
    days = [i for i, b in enumerate(b5) if b[0].endswith("09:35:00")]
    bx.set_xticks(days, [b5[i][0][5:10] for i in days])
    bx.set_ylabel("5分钟收盘价")
    bx.set_title("5分钟：回试见底的那几天（▲ = 30分钟每根收盘时，5分钟划线里当下的1买）",
                 fontsize=10, loc="left")
    for a in (ax, bx):
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


LOSS = "#d62728"
BI = "#9aa7b8"       # 笔
SEG = "#1f3b73"      # 线段
ZS = "#e8a33d"       # 中枢


def _structure(code: str, t: int | None):
    """30分钟最近4000根上划线：t 给了就只用 bars[:t+1]（当时看到的），否则用全窗口。

    返回 (笔端点, 线段端点, 中枢[(起K线, 止K线, zd, zg)])，K线下标是窗口内的。
    """
    from dataclasses import replace

    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_segs
    from feeds import Level, cache

    b = cache.load(code, Level.M30)
    b = [replace(x, idx=i) for i, x in enumerate(b[-4000:])]
    a = analyze(b if t is None else b[: t + 1], check=False)
    bi = [(a.bis[0].start.peak_idx, a.bis[0].start.price)] + \
         [(x.end.peak_idx, x.end.price) for x in a.bis] if a.bis else []
    seg = [(a.segs[0].start.peak_idx, a.segs[0].start.price)] + \
          [(s.end.peak_idx, s.end.price) for s in a.segs] if a.segs else []
    legs = legs_from_segs(a.segs, a.bis)
    zss = []
    for z in build_zhongshus(legs):
        last = legs[z.core_end]
        # 只为画图：最后一段如果是从中枢里起步、一去不回的离开段（C-core 里「第三段
        # 自己飞走」的情形），框画到它的**起点**为止，不然整段大涨都像在中枢里
        leaving = (last.end_price > z.zg) if last.direction.name == "UP" else (last.end_price < z.zd)
        end_bi = last.src_start if leaving and z.core_end > z.start_leg + 1 else last.src_end
        end = (a.bis[end_bi].start.peak_idx if end_bi == last.src_start and leaving
               else a.bis[end_bi].end.peak_idx)
        zss.append((a.bis[legs[z.start_leg].src_start].start.peak_idx, end, z.zd, z.zg))
    return bi, seg, zss


def _draw_structure(ax, pos, st, *, solid: bool, after: int | None = None):
    """把笔/线段/中枢画到 ax 上。``pos`` 是 窗口K线下标 → 横坐标。

    ``after`` 给了就只画这根之后的部分（终版划线只叠加在买入之后，免得和当时的
    划线在买入之前打架）。
    """
    from matplotlib.patches import Rectangle

    bi, seg, zss = st
    keep = (lambda i: i in pos) if after is None else (lambda i: i in pos and i >= after)
    ls = "-" if solid else "--"
    for pts, color, lw in ((bi, BI, 0.9), (seg, SEG, 2.0)):
        # 相邻两端点都在可见范围里才连线，避免跨出画面
        for (i0, p0), (i1, p1) in zip(pts, pts[1:]):
            if keep(i0) and keep(i1):
                ax.plot([pos[i0], pos[i1]], [p0, p1], color=color, lw=lw, ls=ls,
                        alpha=0.9 if solid else 0.55, zorder=3 if lw > 1 else 2)
    lo, hi = min(pos), max(pos)
    for i0, i1, zd, zg in zss:
        if i1 < lo or i0 > hi or (after is not None and i1 < after):
            continue
        start = max(i0, lo) if after is None else max(i0, lo, after)
        x0, x1 = pos[start], pos[min(i1, hi)]
        ax.add_patch(Rectangle((x0, zd), x1 - x0, zg - zd, facecolor=ZS,
                               alpha=0.18 if solid else 0.08, edgecolor=ZS,
                               lw=1.0, ls=ls, zorder=1))


WHY_CN = {"worst": "亏得最多的之一",
          "broke": "买入后跌回 ZG 之下（3买 被破坏）那类里的中位一笔",
          "held": "3买 没破、引擎事后也认了、照样跑输那类里的中位一笔"}


def fig4(out_prefix: str):
    """样本外跑输的例子，按事先规则挑（.tmp/oos_losers.py）。一笔一张图。"""
    d = json.load(open(".tmp/oos_losers.json", encoding="utf-8"))
    for n, r in enumerate(d["picks"], 1):
        bars = r["bars"]
        pos = {b[0]: k for k, b in enumerate(bars)}
        fig, ax = plt.subplots(figsize=(14, 7.2))
        ax.plot(range(len(bars)), [b[4] for b in bars], color="#c8c8c8", lw=0.9,
                zorder=0, label="30分钟收盘价")
        # 当时看到的划线（实线）+ 买入之后的终版划线（虚线）
        _draw_structure(ax, pos, _structure(r["code"], r["t"]), solid=True)
        _draw_structure(ax, pos, _structure(r["code"], None), solid=False, after=r["t"])
        ax.axhline(r["zg"], color=ZS, lw=1.0, ls=":")
        ax.text(1, r["zg"], f" 判3买用的 ZG {r['zg']:.2f}", fontsize=9, color="#a8681a",
                va="bottom")
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        ax.legend(handles=[
            Line2D([], [], color=BI, lw=0.9, label="笔（买入时的划线）"),
            Line2D([], [], color=SEG, lw=2.0, label="线段（买入时的划线）"),
            Patch(facecolor=ZS, alpha=0.3, edgecolor=ZS, label="中枢 [ZD, ZG]"),
            Line2D([], [], color=SEG, lw=2.0, ls="--", alpha=0.55,
                   label="买入之后：终版划线（虚线）"),
        ], loc="upper left", fontsize=8.5, frameon=False)

        e, x = pos[r["t"]], pos[r["exit_bar"]]
        ax.axvspan(e, x, color=LOSS, alpha=0.07, lw=0)
        if r["leave"] in pos:
            k = pos[r["leave"]]
            ax.annotate("离开段起点", (k, bars[k][3]), xytext=(0, -30),
                        textcoords="offset points", ha="center", fontsize=8.5, color=INK,
                        arrowprops=dict(arrowstyle="->", color=INK, lw=0.6))
        if r["low_bar"] in pos:
            k = pos[r["low_bar"]]
            ax.scatter([k], [r["low"]], marker="*", s=220, color=SUB, zorder=6,
                       edgecolor="white", lw=1)
        ax.scatter([e], [r["entry"]], marker="^", s=140, color=SUB, zorder=7)
        ax.annotate(f"当下判3买，买入 {r['entry']:.2f}\n{r['ts'][5:16]}"
                    f"（5分钟1买 @{r['confirm'][5:16]}）",
                    (e, r["entry"]), xytext=(-12, 40), textcoords="offset points",
                    ha="right", fontsize=9, color=INK,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=SUB, lw=0.6),
                    arrowprops=dict(arrowstyle="-", color=SUB, lw=0.6))
        ax.scatter([x], [r["exit"]], marker="v", s=140, color=LOSS, zorder=7)
        ax.annotate(f"持有40根后卖出 {r['exit']:.2f}\n{r['exit_ts'][5:16]}\n"
                    f"扣成本 {r['net']:+.1%}（随机买入 {r['base']:+.1%}）",
                    (x, r["exit"]), xytext=(14, -10), textcoords="offset points",
                    fontsize=9, color=LOSS,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=LOSS, lw=0.6))
        if r["broke_bar"] is not None and r["broke_bar"] in pos:
            k = pos[r["broke_bar"]]
            ax.scatter([k], [bars[k][4]], marker="x", s=90, color=LOSS, zorder=7, lw=2)
            ax.annotate("收盘跌回 ZG 下", (k, bars[k][4]), xytext=(10, -26),
                        textcoords="offset points", fontsize=9, color=LOSS)

        ticks = list(range(0, len(bars), max(1, len(bars) // 10)))
        ax.set_xticks(ticks, [bars[k][1][2:10] for k in ticks])
        ax.set_ylabel("30分钟收盘价（后复权）")
        held = "持有期间没跌破 ZG" if r["broke_bar"] is None else "持有期间跌破了 ZG"
        eng = "引擎事后也认定为 3买" if r["engine"] else "引擎事后不认这是 3买"
        rule = "预登记口径" if "strict" in d.get("steps", "") else "宽松实现"
        ax.set_title(
            f"样本外跑输例 {n}（{rule}）：{r['code']}  {WHY_CN[r['why']]}\n"
            f"★ 回试低点（判出时）  ▲ 买入  ▼ 卖出  浅红 = 持有期；{held}，{eng}。"
            f"（样本外 {d['n']} 笔里跑输随机买入 {d['n_lost']} 笔）",
            fontsize=10.5, loc="left")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        path = f"{out_prefix}_{n}.png"
        fig.savefig(path, dpi=130)
        plt.close(fig)
        print("写出", path)


def fig5(out: str):
    """600460 跑输例2：判出那一刻的5分钟划线——★ 靠的是单中枢的「类1买」。"""
    from dataclasses import replace

    from matplotlib.patches import Rectangle

    from backtest.sublevel import sub_window
    from chan.buysell import find_bspoints
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_segs
    from feeds import Level, cache

    code, cut = "600460", "2026-07-14 13:30:00"
    b30 = cache.load(code, Level.M30)
    b30 = [replace(x, idx=i) for i, x in enumerate(b30[-4000:])]
    b5 = cache.load(code, Level.M5)
    w = sub_window(b5, [x.ts for x in b5], cut, 4000)
    a = analyze(w, check=False)
    legs = legs_from_segs(a.segs, a.bis)
    zss = build_zhongshus(legs)
    pts = find_bspoints(legs, zss, a.bis, macd([x.close for x in w]), include_quasi=True)
    lo = next(i for i, x in enumerate(w) if x.ts >= "2026-06-29")
    view = range(lo, len(w))

    fig, ax = plt.subplots(figsize=(15, 7.5))
    ax.plot(list(view), [w[i].close for i in view], color="#c8c8c8", lw=0.8, zorder=0)
    for pts_, color, lw in (([(b.start.peak_idx, b.start.price) for b in a.bis] +
                             [(a.bis[-1].end.peak_idx, a.bis[-1].end.price)], BI, 0.9),
                            ([(s.start.peak_idx, s.start.price) for s in a.segs] +
                             [(a.segs[-1].end.peak_idx, a.segs[-1].end.price)], SEG, 2.2)):
        xy = [(i, p) for i, p in pts_ if i >= lo]
        ax.plot([i for i, _ in xy], [p for _, p in xy], color=color, lw=lw, zorder=2)
    n_zs = 0
    for z in zss:
        i0 = a.bis[legs[z.start_leg].src_start].start.peak_idx
        i1 = a.bis[legs[z.core_end].src_end].end.peak_idx
        if i1 < lo:
            continue
        n_zs += 1
        ax.add_patch(Rectangle((max(i0, lo), z.zd), i1 - max(i0, lo), z.zg - z.zd,
                               facecolor=ZS, alpha=0.22, edgecolor=ZS, lw=1.2, zorder=1))
        ax.text(max(i0, lo) + 2, z.zg + 3, f"5分钟中枢 [{z.zd:.0f}, {z.zg:.0f}]",
                fontsize=9, color="#a8681a")
    for p in pts:
        if p.kind == 1 and p.is_buy and w[p.bar].ts == "2026-07-14 11:25:00":
            ax.scatter([p.bar], [p.price], marker="*", s=300, color=SUB, zorder=6,
                       edgecolor="white", lw=1)
            ax.annotate("★ 这就是「5分钟1买」\n引擎标记：类1买（quasi）\n"
                        "· 画面上两个框，但后框高点 GG 550.18 ≥ 前框低点 DD 549.96\n"
                        "  （只高 0.22）→ 20课中心定理二：不是下跌，合成更大级别中枢\n"
                        "· 等于只有一个中枢 → 只能算盘整背驰（37课 p96）\n"
                        "· 绿柱面积 c=125.5 ≥ b=102.8，面积没背驰，靠 DIF 一条进来\n"
                        "· 引擎注：类1买「周线以上级别才有意义」（第503行）",
                        (p.bar, p.price), xytext=(-330, -10), textcoords="offset points",
                        fontsize=9.5, color=INK,
                        bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=SUB, lw=0.8),
                        arrowprops=dict(arrowstyle="->", color=SUB, lw=0.8))
    days = [i for i in view if w[i].ts.endswith("09:35:00")]
    ax.set_xticks(days, [w[i].ts[5:10] for i in days])
    ax.set_ylabel("5分钟收盘价（后复权）")
    ax.set_title(
        f"600460  {cut[5:16]} 判出时的5分钟划线：两个框重叠 0.22 元、按规则算一个大中枢，★ 是盘整背驰\n"
        "原文的1买要求趋势背驰（a+A+b+B+c，至少两个同向中枢）。样本外 88 次判出里 86 次是这种类1买，"
        "真正的趋势1买只有 2 次",
        fontsize=10.5, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


UPBI = "#2ca02c"     # 被确认的那根5分钟向上笔


def _v2_events(steps_dir: str, n: int):
    """按股票顺序取最先判出的 n 个第二版事件（不挑，赚亏都要）。"""
    from pathlib import Path

    from backtest.sublevel import _read_steps

    out = []
    for p in sorted(Path(steps_dir).glob("*.steps.jsonl")):
        st = _read_steps(p)
        for t in sorted(st):
            r = st[t]
            if r["cand"] and r["confirm"]:
                out.append((p.name.split(".")[0], t, r))
                break
        if len(out) >= n:
            break
    return out


def fig6(out_prefix: str, steps_dir: str, n: int = 3):
    """第二版「当下3买」的例子：上 30分钟（第一个中枢），下 5分钟（回拉笔中枢 + 向上笔确认）。"""
    from dataclasses import replace

    from matplotlib.patches import Rectangle

    from backtest.costs import Costs, simulate_long
    from backtest.score import base_rate
    from backtest.sublevel import sub_window
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs
    from feeds import Level, cache

    for k, (code, t, r) in enumerate(_v2_events(steps_dir, n), 1):
        b30 = cache.load(code, Level.M30)
        b30 = [replace(x, idx=i) for i, x in enumerate(b30[-4000:])]
        _, leave, zg, low, lo_i, rs = r["cand"]
        tr = simulate_long(b30, t, 40, code, Costs(), intraday=True)
        base = base_rate(b30, 40, "buy")[1]
        a = analyze(b30[: t + 1], check=False)
        zss = build_zhongshus(legs_from_segs(a.segs, a.bis))
        legs = legs_from_segs(a.segs, a.bis)
        prev_start = a.bis[legs[zss[-2].start_leg].src_start].start.peak_idx
        lo = max(0, prev_start - 40)
        hi = min(len(b30), (tr.exit_bar if not isinstance(tr, str) else t) + 50)
        pos = {i: i - lo for i in range(lo, hi)}

        fig, (ax, bx) = plt.subplots(2, 1, figsize=(15, 11),
                                     gridspec_kw={"height_ratios": [1.15, 1]})
        ax.plot([pos[i] for i in range(lo, hi)], [b30[i].close for i in range(lo, hi)],
                color="#c8c8c8", lw=0.9, zorder=0)
        _draw_structure(ax, pos, _structure(code, t), solid=True)
        _draw_structure(ax, pos, _structure(code, None), solid=False, after=t)
        for z, name in ((zss[-2], "前一个中枢"), (zss[-1], "第一个中枢（与前一个是下跌关系）")):
            i0 = a.bis[legs[z.start_leg].src_start].start.peak_idx
            if i0 in pos:
                ax.text(pos[i0], z.zg, f" {name}\n [{z.zd:.2f}, {z.zg:.2f}]", fontsize=8.5,
                        color="#a8681a", va="bottom")
        ax.axhline(zg, color=ZS, lw=1.0, ls=":")
        if lo_i in pos:
            ax.scatter([pos[lo_i]], [low], marker="*", s=240, color=SUB, zorder=6,
                       edgecolor="white", lw=1)
        ax.scatter([pos[t]], [b30[t].close], marker="^", s=150, color=SUB, zorder=7)
        ax.annotate(f"买入 {b30[t].close:.2f}\n{b30[t].ts[5:16]}", (pos[t], b30[t].close),
                    xytext=(-14, 34), textcoords="offset points", ha="right", fontsize=9,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=SUB, lw=0.6),
                    arrowprops=dict(arrowstyle="-", color=SUB, lw=0.6))
        res = "成交不了" if isinstance(tr, str) else f"{tr.net:+.1%}"
        if not isinstance(tr, str):
            x = pos[tr.exit_bar]
            color = UPBI if tr.net > base else LOSS
            ax.axvspan(pos[t], x, color=color, alpha=0.07, lw=0)
            ax.scatter([x], [b30[tr.exit_bar].close], marker="v", s=150, color=color, zorder=7)
            ax.annotate(f"持有40根卖出 {b30[tr.exit_bar].close:.2f}\n扣成本 {tr.net:+.1%}"
                        f"（随机买入 {base:+.1%}）", (x, b30[tr.exit_bar].close),
                        xytext=(14, -10), textcoords="offset points", fontsize=9, color=color,
                        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color, lw=0.6))
        ticks = list(range(0, hi - lo, max(1, (hi - lo) // 10)))
        ax.set_xticks(ticks, [b30[lo + i].ts[2:10] for i in ticks])
        ax.set_title(f"第二版例 {k}：{code}（按股票顺序取最先判出的，不挑）  30分钟：只做第一个中枢的 3买"
                     f"，回拉低点 ★ {low:.2f} > ZG {zg:.2f}；持有40根 {res}",
                     fontsize=10.5, loc="left")

        # 下：5分钟回拉段
        b5 = cache.load(code, Level.M5)
        w = sub_window(b5, [x.ts for x in b5], b30[t].ts, 4000)
        a5 = analyze(w, check=False)
        legs5 = legs_from_bis(a5.bis)
        i_rs = next(i for i, x in enumerate(w) if x.ts > rs)
        lo5 = min(range(i_rs, len(w)), key=lambda i: w[i].low)
        v0 = max(0, i_rs - 40)
        bx.plot(range(v0, len(w)), [w[i].close for i in range(v0, len(w))],
                color="#c8c8c8", lw=0.8, zorder=0)
        for bi in a5.bis:
            if bi.start.peak_idx >= v0:
                up_conf = (bi.direction.name == "UP" and abs(bi.start.price - w[lo5].low) < 1e-6)
                bx.plot([bi.start.peak_idx, bi.end.peak_idx], [bi.start.price, bi.end.price],
                        color=UPBI if up_conf else BI, lw=2.6 if up_conf else 1.0, zorder=3)
        for z in build_zhongshus(legs5):
            s = a5.bis[legs5[z.start_leg].src_start].start.peak_idx
            e = a5.bis[legs5[z.core_end].src_end].end.peak_idx
            if e < v0:
                continue
            inside = s >= i_rs and e <= lo5
            bx.add_patch(Rectangle((max(s, v0), z.zd), e - max(s, v0), z.zg - z.zd,
                                   facecolor=ZS, alpha=0.28 if inside else 0.08,
                                   edgecolor=ZS, lw=1.2 if inside else 0.5, zorder=1))
        bx.axvline(i_rs, color=INK, lw=0.8, ls=":")
        bx.text(i_rs, bx.get_ylim()[1], " 回拉开始", fontsize=8.5, va="top")
        bx.scatter([lo5], [w[lo5].low], marker="*", s=220, color=SUB, zorder=6,
                   edgecolor="white", lw=1)
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        bx.legend(handles=[
            Patch(facecolor=ZS, alpha=0.4, edgecolor=ZS, label="回拉段里的5分钟笔中枢（走势必完美）"),
            Line2D([], [], color=UPBI, lw=2.6, label="低点之后第一根向上笔（已被后面的反向笔确认）"),
            Line2D([], [], color=BI, lw=1.0, label="5分钟笔"),
        ], loc="upper right", fontsize=8.5, frameon=False)
        days = [i for i in range(v0, len(w)) if w[i].ts.endswith("09:35:00")]
        bx.set_xticks(days, [w[i].ts[5:10] for i in days])
        bx.set_title(f"5分钟（截至 {b30[t].ts[5:16]} 已收盘的K线）：回拉先形成笔中枢，"
                     f"低点 ★ 之后向上笔被确认（{r['confirm'][5:16]}）→ 当下判 3买，不看背驰",
                     fontsize=10, loc="left")
        for z in (ax, bx):
            z.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        path = f"{out_prefix}_{k}.png"
        fig.savefig(path, dpi=125)
        plt.close(fig)
        print("写出", path)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "1"
    if which == "6":
        fig6("_plot/v2_example", sys.argv[2] if len(sys.argv) > 2 else ".tmp/now3b2_orig")
        sys.exit(0)
    if which == "5":
        fig5("_plot/sublevel_5_quasi.png")
        sys.exit(0)
    if which == "4":
        fig4("_plot/sublevel_loss")
        sys.exit(0)
    {"1": lambda: fig1("_plot/sublevel_1_example.png"),
     "2": lambda: fig2("_plot/sublevel_2_overview.png"),
     "3": lambda: fig3("_plot/sublevel_3_point.png")}[which]()
