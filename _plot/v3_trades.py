"""第三版正式样本每一笔交易一张图：30 分钟全貌 + 入场时 5 分钟 + 出场时 5 分钟。
用法：PYTHONPATH=. python _plot/v3_trades.py STEPS_DIR EXIT_DIR [OUT_DIR]
  STEPS_DIR = 含 cand+confirm 的步骤目录（.tmp/now3b7_new），EXIT_DIR = 卖出走查目录（.tmp/exit4_new）。
  上：30 分钟 K 线 + 出场那一步看到的笔（实线）+ 之后终版的笔（虚线）+ 从 1买 重划的笔中枢（紫框）
      + 引擎的卖点（红倒三角，标几类卖/类）；蓝星 = 1买，蓝虚线 = 1买价，蓝▲ = 入场，黑▼ = 出场。
  左下：入场那一步的 5 分钟（从回拉的顶重划的中枢 + 5 分钟 1买/类 1买）。
  右下：出场那一步的 5 分钟（默认中枢 + 5 分钟 1 类点）。
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

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
NEWZS = "#8e6cc0"
GREEN = "#0b6e4f"


def boxes(ax, a, legs, zss, color, edge, off=0, left=0, label=""):
    for k, z in enumerate(zss):
        s0 = a.bis[legs[z.start_leg].src_start].start.peak_idx + off
        s1 = a.bis[legs[z.core_end].src_end].end.peak_idx + off
        if s1 < left:
            continue
        ax.add_patch(Rectangle((max(s0, left), z.zd), s1 - max(s0, left), z.zg - z.zd,
                               facecolor=color, alpha=0.22, edgecolor=edge, lw=1.2))
        ax.text(max(s0, left), z.zg, f" {label}{k} [{z.zd:.4g},{z.zg:.4g}]", fontsize=8.5, color=edge,
                va="bottom")


def draw_bis(ax, a, off=0, left=0, ls_conf="-"):
    for x in a.bis:
        s0, s1 = x.start.peak_idx + off, x.end.peak_idx + off
        if s1 >= left:
            ax.plot([s0, s1], [x.start.price, x.end.price], lw=1.7, zorder=4,
                    color=UP if x.direction.name == "UP" else DOWN, ls=ls_conf if x.confirmed else ":")


def five_axes(ax, w, left, title):
    ticks = list(range(left, len(w), max(1, (len(w) - left) // 8)))
    ax.set_xticks(ticks, [w[i].ts[5:16] for i in ticks], fontsize=8)
    ax.set_xlim(left - 1, len(w) + 3)
    ax.set_title(title, fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)


def one(code, t0, steps, exit_dir, out, n):
    from backtest.costs import Costs, enter_long, exit_long, prev_day_close
    from backtest.sublevel import _read_steps, structural_exit_v3, sub_window, top5_index
    from chan.buysell import find_bspoints
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis
    from feeds import Level, cache

    b = cache.load(code, Level.M30)
    b = [replace(x, idx=i) for i, x in enumerate(b[-4000:])]
    b5 = cache.load(code, Level.M5)
    ts5 = [x.ts for x in b5]
    ref = prev_day_close(b)
    c = steps[t0]["cand"]
    meta = json.loads((Path(exit_dir) / f"{code}.{t0}.meta.json").read_text(encoding="utf-8"))
    lo1, p1 = meta["lo1"], meta["p1"]
    ex = _read_steps(Path(exit_dir) / f"{code}.{t0}.jsonl")
    e = enter_long(b, t0, code, ref)
    x, why = structural_exit_v3(ex, b, e, p1, 160)
    tr = exit_long(b, t0, e, x, code, Costs(slippage=0.001), ref=ref, intraday=True)
    sells = ex[x]["sells"]

    # ---- 30 分钟 ----
    pre = b[: x + 1]
    a = analyze(pre, check=True)
    legs = legs_from_bis(a.bis)
    i0 = next((i for i, y in enumerate(legs) if a.bis[y.src_start].start.peak_idx >= lo1), None)
    zss = build_zhongshus(legs, start=i0) if i0 is not None else []
    hi = min(len(b), tr.exit_bar + 40)
    fin = analyze(b[:hi], check=False)
    lo = max(0, lo1 - 50)

    f = plt.figure(figsize=(20, 12.5))
    gs = f.add_gridspec(2, 2, height_ratios=[1.5, 1], hspace=0.22, wspace=0.08)
    ax = f.add_subplot(gs[0, :])
    candles(ax, b, lo, hi)
    draw_bis(ax, a, left=lo)
    for y in fin.bis:
        if y.start.peak_idx >= x - 2 and y.end.peak_idx < hi:
            ax.plot([y.start.peak_idx, y.end.peak_idx], [y.start.price, y.end.price], lw=1.2, ls="--",
                    alpha=0.5, color=UP if y.direction.name == "UP" else DOWN, zorder=3)
    boxes(ax, a, legs, zss, NEWZS, NEWZS, left=lo, label="从1买重划的 Z")
    ax.axhline(p1, color=BUY, lw=1.0, ls="--", alpha=0.8)
    ax.scatter([lo1], [p1], marker="*", s=300, color=BUY, zorder=8, edgecolor="white")
    ax.annotate(f"1买 {p1:.2f}  {b[lo1].ts[5:16]}", (lo1, p1), xytext=(8, -26), textcoords="offset points",
                fontsize=9.5, color=BUY)
    ax.scatter([tr.entry_bar], [b[tr.entry_bar].close], marker="^", s=190, color=BUY, zorder=9, edgecolor="white")
    ax.annotate(f"入场 {b[tr.entry_bar].close:.2f}\n{b[tr.entry_bar].ts[5:16]}", (tr.entry_bar, b[tr.entry_bar].close),
                xytext=(-10, 34), textcoords="offset points", fontsize=9.5, color=BUY, ha="right")
    ax.scatter([tr.exit_bar], [b[tr.exit_bar].close], marker="v", s=190, color=INK, zorder=9)
    ax.annotate(f"出场 {b[tr.exit_bar].close:.2f}\n{b[tr.exit_bar].ts[5:16]}  {tr.net:+.2%}", (tr.exit_bar, b[tr.exit_bar].close),
                xytext=(10, -44), textcoords="offset points", fontsize=9.5, color=INK)
    for kind, bar, quasi in sells:
        ax.scatter([bar], [b[bar].high], marker="v", s=140, color="#b00020", zorder=8, edgecolor="white")
        ax.annotate(f"{'类' if quasi else ''}{kind}卖 @{b[bar].ts[5:16]}", (bar, b[bar].high), xytext=(4, 10),
                    textcoords="offset points", fontsize=9, color="#b00020")
    ax.axvline(x + 0.5, color=INK, lw=0.8, ls=":")
    ax.text(x + 0.5, ax.get_ylim()[1], f" 卖点被看到的那一步（{b[x].ts[5:16]}）", va="top", fontsize=9)
    ticks = list(range(lo, hi, max(1, (hi - lo) // 12)))
    ax.set_xticks(ticks, [b[i].ts[2:10] for i in ticks])
    ax.set_xlim(lo - 1, hi)
    ax.set_title(f"{code}：入场 {b[tr.entry_bar].ts[:16]} → 出场 {b[tr.exit_bar].ts[:16]}，持有 {tr.exit_bar - tr.entry_bar} 根，"
                 f"净 {tr.net:+.2%}，原因 {why}（引擎卖点 {[('类' if q else '') + str(k) + '卖' for k, _, q in sells]}）"
                 "；实线 = 卖点当时看到的 30 分钟笔，虚线 = 之后终版的笔，紫框 = 从 1买 重划的笔中枢",
                 fontsize=11, loc="left")
    ax.spines[["top", "right"]].set_visible(False)

    # ---- 入场时 5 分钟 ----
    w = sub_window(b5, ts5, b[t0].ts, 4000)
    a5 = analyze(w, check=True)
    l5 = legs_from_bis(a5.bis)
    top = top5_index(w, c[5])
    j0 = next((i for i, y in enumerate(l5) if a5.bis[y.src_start].start.peak_idx >= top), None)
    z5 = build_zhongshus(l5, start=j0) if j0 is not None else []
    pts = find_bspoints(l5, z5, a5.bis, macd([y.close for y in w]), include_quasi=True) if z5 else []
    ax2 = f.add_subplot(gs[1, 0])
    left = max(0, top - 70)
    candles(ax2, w, left, len(w))
    draw_bis(ax2, a5, left=left)
    boxes(ax2, a5, l5, z5, "#e0a050", "#a8681a", left=left, label="从顶重划 Z")
    ax2.scatter([top], [w[top].high], marker="*", s=200, color=INK, zorder=8)
    for q in pts:
        if q.kind == 1 and q.bar > top:
            ax2.scatter([q.bar], [w[q.bar].low if q.is_buy else w[q.bar].high], marker="^" if q.is_buy else "v",
                        s=120, color=GREEN if q.is_buy else "#b00020", zorder=9, edgecolor="white")
    five_axes(ax2, w, left, f"入场时的 5 分钟（30分钟 {b[t0].ts[5:16]} 收盘）：星 = 回拉的顶，绿▲ = 5分钟 1买/类1买，橙框 = 从顶重划的中枢")

    # ---- 出场时 5 分钟 ----
    wx = sub_window(b5, ts5, b[x].ts, 4000)
    ax_ = analyze(wx, check=True)
    lx = legs_from_bis(ax_.bis)
    zx = build_zhongshus(lx)
    ptx = find_bspoints(lx, zx, ax_.bis, macd([y.close for y in wx]), include_quasi=True) if zx else []
    ax3 = f.add_subplot(gs[1, 1])
    leftx = max(0, len(wx) - 220)
    candles(ax3, wx, leftx, len(wx))
    draw_bis(ax3, ax_, left=leftx)
    boxes(ax3, ax_, lx, zx, "#e0a050", "#a8681a", left=leftx, label="Z")
    for q in ptx:
        if q.kind == 1 and q.bar >= leftx:
            ax3.scatter([q.bar], [wx[q.bar].low if q.is_buy else wx[q.bar].high], marker="^" if q.is_buy else "v",
                        s=110, color=GREEN if q.is_buy else "#b00020", zorder=9, edgecolor="white")
    five_axes(ax3, wx, leftx, f"出场时的 5 分钟（30分钟 {b[x].ts[5:16]} 收盘）：默认划法的 5 分钟笔/中枢，三角 = 5分钟 1 类买/卖点")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v3_trade_{n:02d}_{code}_{t0}.png"
    f.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(f)
    print(f"{n:2d} {code} 入场 {b[tr.entry_bar].ts[:16]} 出场 {b[tr.exit_bar].ts[:16]} 持有{tr.exit_bar - tr.entry_bar} "
          f"净{tr.net:+.2%} {why} 卖点{[('类' if q else '') + str(k) + '卖@' + b[bar].ts[5:16] for k, bar, q in sells]}")
    print("   写出", path)


def main(steps_dir: str, exit_dir: str, out: Path) -> None:
    from backtest.sublevel import _read_steps

    metas = sorted(Path(exit_dir).glob("*.meta.json"))
    for n, m in enumerate(metas, 1):
        code, t0 = m.name.split(".")[0], int(m.name.split(".")[1])
        steps = _read_steps(Path(steps_dir) / f"{code}.steps.jsonl")
        one(code, t0, steps, exit_dir, out, n)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/v3e"))
