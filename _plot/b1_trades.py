"""1买（区间套）每一笔交易一张图：30 分钟的 A、B、C 和 c 末背驰 + 入场/止损/出场 + 入场时/出场时的 5 分钟。
用法：PYTHONPATH=. python _plot/b1_trades.py ARM_DIR ENTRY_DIR EXIT_DIR [OUT_DIR]
  上：30 分钟 K 线 + 布防那一刻看到的笔（实线）+ 之后的笔（虚线）；橙框 A、B（A→B 下跌），青框 C（c 里面的中枢，
      有的话），蓝星 = c 最低那段的终点（1买 候选），蓝▲ = 入场，红虚线 = 止损价，黑▼ = 出场（T+1 之后的实际成交）。
  左下：入场时的 5 分钟（默认划线的笔/中枢，绿▲ = 5 分钟买点，蓝▲ = 入场）。
  右下：出场信号那一刻的 5 分钟（红▼ = 触发出场的卖点；跌破止损时没有卖点）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, "_plot")
from fake3b import BUY, DOWN, INK, UP, candles  # noqa: E402
from v3_trades import GREEN, boxes, draw_bis, five_axes  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
ORANGE, TEAL = "#a8681a", "#1f7a6f"


def box(ax, a, legs, z, name, color, left):
    s0 = a.bis[legs[z.start_leg].src_start].start.peak_idx
    s1 = a.bis[legs[z.core_end].src_end].end.peak_idx
    ax.add_patch(Rectangle((max(s0, left), z.zd), s1 - max(s0, left), z.zg - z.zd, facecolor=color, alpha=0.22,
                           edgecolor=color, lw=1.4))
    ax.text(max(s0, left), z.zg, f" {name} [{z.zd:.4g},{z.zg:.4g}]", fontsize=9.5, color=color, va="bottom",
            fontweight="bold")


def one(code, ev, exit_dir, out, n, stop_mode="c"):
    """``stop_mode``：``"c"`` = 30 分钟 c 最低点（现行止损）；``"5min"`` = 5 分钟买点低点（最早那版）；
    ``"off"`` = 没启用止损，只把 c 最低点画成灰色参考线。"""
    from backtest.costs import Costs, enter_long, exit_long, prev_day_close
    from backtest.sublevel import _read_steps, c_inner_zhongshu, sub_window
    from chan.buysell import find_bspoints
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis
    from feeds import Level, cache

    b30 = cache.load(code, Level.M30)
    b30 = [replace(x, idx=i) for i, x in enumerate(b30[-4000:])]
    b5 = cache.load(code, Level.M5)
    ts5 = [x.ts for x in b5]
    ts30 = {x.ts: i for i, x in enumerate(b30)}
    ref5 = prev_day_close(b5)
    e5, arm_t, stop = ev["e5"], ev["arm_t"], ev["stop"]
    ex = _read_steps(Path(exit_dir) / f"{code}.{e5}.jsonl")
    last = max(ex)
    why = ex[last].get("trigger") or "cap"
    e = enter_long(b5, e5, code, ref5)
    tr = exit_long(b5, e5, e, last, code, Costs(slippage=0.001), ref=ref5, intraday=True)
    sells = ex[last].get("sells", [])
    ent_ts, exit_ts, sig_ts = b5[e].ts, b5[tr.exit_bar].ts, b5[last].ts
    leg_bar = ts30[ev["leg_start"]]

    # ---- 30 分钟 ----
    pre = b30[: arm_t + 1]
    a = analyze(pre, check=True)
    legs = legs_from_bis(a.bis)
    zss = build_zhongshus(legs)
    k = [i for i, z in enumerate(zss) if z.depart_leg is not None][-1]
    A, B = zss[k - 1], zss[k]
    j = next(i for i, y in enumerate(legs) if a.bis[y.src_start].start.peak_idx == leg_bar)
    C = c_inner_zhongshu(legs, B.core_end + 1, j)
    if stop_mode != "5min":
        stop = legs[j].low
    s_col = "#888888" if stop_mode == "off" else "#b00020"
    s_lab = {"c": "止损（30分钟收盘跌破 c 最低点）", "5min": "止损（5分钟买点低点）",
             "off": "c 最低点（本图未启用止损）"}[stop_mode]
    ie30 = next(i for i, y in enumerate(b30) if y.ts >= ent_ts)
    ix30 = next(i for i, y in enumerate(b30) if y.ts >= exit_ts)
    lo_bar = a.bis[legs[A.start_leg].src_start].start.peak_idx
    lo, hi = max(0, lo_bar - 15), min(len(b30), ix30 + 40)
    fin = analyze(b30[:hi], check=False)
    f = plt.figure(figsize=(20, 12.5))
    gs = f.add_gridspec(2, 2, height_ratios=[1.5, 1], hspace=0.22, wspace=0.08)
    ax = f.add_subplot(gs[0, :])
    candles(ax, b30, lo, hi)
    draw_bis(ax, a, left=lo)
    for y in fin.bis:
        if y.start.peak_idx >= arm_t - 2 and y.end.peak_idx < hi:
            ax.plot([y.start.peak_idx, y.end.peak_idx], [y.start.price, y.end.price], lw=1.2, ls="--", alpha=0.5,
                    color=UP if y.direction.name == "UP" else DOWN, zorder=3)
    box(ax, a, legs, A, "A", ORANGE, lo)
    box(ax, a, legs, B, "B", ORANGE, lo)
    if C is not None:
        box(ax, a, legs, C, "C", TEAL, lo)
    ax.scatter([a.bis[j].end.peak_idx], [legs[j].low], marker="*", s=320, color=BUY, zorder=8, edgecolor="white")
    ax.annotate(f"c 最低段终点 {legs[j].low:.4g}\n{b30[a.bis[j].end.peak_idx].ts[5:16]}（布防时 c 对 b 背驰）",
                (a.bis[j].end.peak_idx, legs[j].low), xytext=(8, -44), textcoords="offset points", fontsize=9.5, color=BUY)
    ax.scatter([ie30], [ev["price"]], marker="^", s=190, color=BUY, zorder=9, edgecolor="white")
    ax.annotate(f"入场 {ev['price']:.4g}\n{ent_ts[5:16]}", (ie30, ev["price"]), xytext=(-10, 36), textcoords="offset points",
                fontsize=9.5, color=BUY, ha="right")
    ax.axhline(stop, color=s_col, lw=1.0, ls="--", alpha=0.7)
    ax.text(lo, stop, f" {s_lab} {stop:.4g}", color=s_col, fontsize=9, va="top")
    ax.scatter([ix30], [b5[tr.exit_bar].close], marker="v", s=190, color=INK, zorder=9)
    ax.annotate(f"出场 {b5[tr.exit_bar].close:.4g}\n{exit_ts[5:16]}  {tr.net:+.2%}（{why}）", (ix30, b5[tr.exit_bar].close),
                xytext=(10, -48), textcoords="offset points", fontsize=9.5, color=INK)
    ticks = list(range(lo, hi, max(1, (hi - lo) // 12)))
    ax.set_xticks(ticks, [b30[i].ts[2:10] for i in ticks])
    ax.set_xlim(lo - 1, hi)
    ax.set_title(f"{code}：入场 {ent_ts[:16]} → 出场 {exit_ts[:16]}，持有 {tr.exit_bar - tr.entry_bar} 根 5 分钟，净 {tr.net:+.2%}，"
                 f"原因 {why}" + (f"（{[('类' if q else '') + str(kk) + '卖' for kk, _, q in sells]}）" if sells else "")
                 + f"；A→B 下跌，B 之后 c 一路下跌{'，c 里面有 C' if C is not None else ''}",
                 fontsize=11, loc="left")
    ax.spines[["top", "right"]].set_visible(False)

    # ---- 入场时 5 分钟 ----
    w = sub_window(b5, ts5, ent_ts, 4000)
    a5 = analyze(w, check=True)
    l5 = legs_from_bis(a5.bis)
    z5 = build_zhongshus(l5)
    ax2 = f.add_subplot(gs[1, 0])
    wi = {y.ts: i for i, y in enumerate(w)}
    left = max(0, len(w) - 230)
    candles(ax2, w, left, len(w))
    draw_bis(ax2, a5, left=left)
    boxes(ax2, a5, l5, z5, "#e0a050", ORANGE, left=left, label="Z")
    if ev["point_ts"] in wi:
        ax2.scatter([wi[ev["point_ts"]]], [w[wi[ev["point_ts"]]].low], marker="^", s=170, color=GREEN, zorder=9,
                    edgecolor="white")
    ax2.scatter([len(w) - 1], [w[-1].close], marker="^", s=200, color=BUY, zorder=10, edgecolor="white")
    ax2.axhline(stop, color=s_col, lw=1.0, ls="--", alpha=0.7)
    five_axes(ax2, w, left, f"入场时的 5 分钟（{ent_ts[5:16]}）：绿▲ = 5 分钟买点（{ev['point_ts'][5:16]}），蓝▲ = 入场，虚线 = {s_lab}")

    # ---- 出场信号时 5 分钟 ----
    wx = sub_window(b5, ts5, sig_ts, 4000)
    ax_ = analyze(wx, check=True)
    lx = legs_from_bis(ax_.bis)
    zx = build_zhongshus(lx)
    ax3 = f.add_subplot(gs[1, 1])
    wxi = {y.ts: i for i, y in enumerate(wx)}
    leftx = max(0, len(wx) - 230)
    candles(ax3, wx, leftx, len(wx))
    draw_bis(ax3, ax_, left=leftx)
    boxes(ax3, ax_, lx, zx, "#e0a050", ORANGE, left=leftx, label="Z")
    if ent_ts in wxi:
        ax3.scatter([wxi[ent_ts]], [ev["price"]], marker="^", s=170, color=BUY, zorder=10, edgecolor="white")
    for kk, t, q in sells:
        if t in wxi:
            ax3.scatter([wxi[t]], [wx[wxi[t]].high], marker="v", s=150, color="#b00020", zorder=10, edgecolor="white")
            ax3.annotate(f"{'类' if q else ''}{kk}卖 {t[5:16]}", (wxi[t], wx[wxi[t]].high), xytext=(4, 10),
                         textcoords="offset points", fontsize=9, color="#b00020")
    ax3.axhline(stop, color=s_col, lw=1.0, ls="--", alpha=0.7)
    ax3.scatter([len(wx) - 1], [wx[-1].close], marker="v", s=170, color=INK, zorder=10)
    five_axes(ax3, wx, leftx, f"出场信号那一刻的 5 分钟（{sig_ts[5:16]}）：红▼ = 引擎划出的卖点；{'收盘跌破止损' if why == 'broken' else ''}")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"b1_trade_{n:02d}_{code}_{e5}.png"
    f.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(f)
    print(f"{n:2d} {code} 入场 {ent_ts[5:16]} {ev['price']:.4g} 止损 {stop:.4g} | 出场 {exit_ts[5:16]} {b5[tr.exit_bar].close:.4g} "
          f"{tr.net:+.2%} {why} | C={'有' if C is not None else '无'}")


def main(arm_dir, ent_dir, exit_dir, out):
    n = 0
    for p in sorted(Path(ent_dir).glob("*.entries.jsonl")):
        code = p.name.split(".")[0]
        for line in p.read_text(encoding="utf-8").splitlines():
            n += 1
            one(code, json.loads(line), exit_dir, out, n)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4] if len(sys.argv) > 4 else "_plot/b1"))
