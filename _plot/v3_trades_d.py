"""新样本（oos400d）每一笔交易一张图：30 分钟全貌（1买、入场、两种卖法的出场）+ 入场时 5 分钟 + 出场时 5 分钟。
用法：PYTHONPATH=. python _plot/v3_trades_d.py CONF_DIR EXIT4_DIR EXIT5_DIR [OUT_DIR]
  CONF_DIR = 含 cand+confirm 的步骤目录（.tmp/now3b7_d），EXIT4_DIR = 第三版卖法走查 + 1买 meta（.tmp/exit4_d），
  EXIT5_DIR = 第四版 5 分钟卖法走查（.tmp/exit5_d）。
  上：30 分钟 K 线 + 出场那刻看到的笔（实线）+ 之后终版的笔（虚线）+ 从 1买 重划的笔中枢（紫框）；
      蓝星 = 1买，蓝虚线 = 1买价，蓝▲ = 入场，黑▼ = 第四版（5 分钟）出场，灰空心▽ = 第三版（30 分钟）出场，
      红倒三角 = 触发第四版出场的 5 分钟卖点。
  左下：入场那一步的 5 分钟（从回拉的顶重划的中枢，绿▲ = 5 分钟买点，大绿▲ = 用来确认入场的那个）。
  右下：第四版出场那一刻的 5 分钟（默认划法的笔/中枢，红▼ = 触发出场的卖点）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, "_plot")
from fake3b import BUY, INK, UP, DOWN, candles  # noqa: E402
from v3_trades import GREEN, NEWZS, boxes, draw_bis, five_axes  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def one(code, t0, conf_dir, ex4_dir, ex5_dir, out, n):
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
    ref30, ref5 = prev_day_close(b), prev_day_close(b5)
    steps = _read_steps(Path(conf_dir) / f"{code}.steps.jsonl")
    c = steps[t0]["cand"]
    conf_ts = steps[t0]["confirm"]
    meta = json.loads((Path(ex4_dir) / f"{code}.{t0}.meta.json").read_text(encoding="utf-8"))
    lo1, p1 = meta["lo1"], meta["p1"]
    e30 = enter_long(b, t0, code, ref30)
    e5 = ts5.index(b[e30].ts)
    st5 = _read_steps(Path(ex5_dir) / f"{code}.{t0}.jsonl")
    last5 = max(st5)
    r5 = st5[last5]
    tr5 = exit_long(b5, e5, e5, last5, code, Costs(slippage=0.001), ref=ref5, intraday=True)
    ex4 = _read_steps(Path(ex4_dir) / f"{code}.{t0}.jsonl")
    x3, why3 = structural_exit_v3(ex4, b, e30, p1, 160)
    tr3 = exit_long(b, t0, e30, x3, code, Costs(slippage=0.001), ref=ref30, intraday=True)
    xts = b5[tr5.exit_bar].ts
    xe30 = next(i for i, y in enumerate(b) if y.ts >= xts)
    sells = r5.get("sells", [])

    # ---- 30 分钟 ----
    a = analyze(b[: xe30 + 1], check=True)
    legs = legs_from_bis(a.bis)
    i0 = next((i for i, y in enumerate(legs) if a.bis[y.src_start].start.peak_idx >= lo1), None)
    zss = build_zhongshus(legs, start=i0) if i0 is not None else []
    hi = min(len(b), max(xe30, tr3.exit_bar if not isinstance(tr3, str) else 0) + 40)
    fin = analyze(b[:hi], check=False)
    lo = max(0, lo1 - 50)
    f = plt.figure(figsize=(20, 12.5))
    gs = f.add_gridspec(2, 2, height_ratios=[1.5, 1], hspace=0.22, wspace=0.08)
    ax = f.add_subplot(gs[0, :])
    candles(ax, b, lo, hi)
    draw_bis(ax, a, left=lo)
    for y in fin.bis:
        if y.start.peak_idx >= xe30 - 2 and y.end.peak_idx < hi:
            ax.plot([y.start.peak_idx, y.end.peak_idx], [y.start.price, y.end.price], lw=1.2, ls="--", alpha=0.5,
                    color=UP if y.direction.name == "UP" else DOWN, zorder=3)
    boxes(ax, a, legs, zss, NEWZS, NEWZS, left=lo, label="从1买重划的 Z")
    ax.axhline(p1, color=BUY, lw=1.0, ls="--", alpha=0.8)
    ax.scatter([lo1], [p1], marker="*", s=300, color=BUY, zorder=8, edgecolor="white")
    ax.annotate(f"1买 {p1:.2f}  {b[lo1].ts[5:16]}", (lo1, p1), xytext=(8, -26), textcoords="offset points",
                fontsize=9.5, color=BUY)
    ax.scatter([e30], [b[e30].close], marker="^", s=190, color=BUY, zorder=9, edgecolor="white")
    ax.annotate(f"入场 {b[e30].close:.2f}\n{b[e30].ts[5:16]}", (e30, b[e30].close), xytext=(-10, 34),
                textcoords="offset points", fontsize=9.5, color=BUY, ha="right")
    ax.scatter([xe30], [b5[tr5.exit_bar].close], marker="v", s=190, color=INK, zorder=9)
    ax.annotate(f"第四版出场 {b5[tr5.exit_bar].close:.2f}\n{xts[5:16]}  {tr5.net:+.2%}", (xe30, b5[tr5.exit_bar].close),
                xytext=(10, -48), textcoords="offset points", fontsize=9.5, color=INK)
    if not isinstance(tr3, str):
        ax.scatter([tr3.exit_bar], [b[tr3.exit_bar].close], marker="v", s=170, facecolors="none",
                   edgecolors="#777", linewidths=1.8, zorder=9)
        ax.annotate(f"第三版出场 {b[tr3.exit_bar].close:.2f}\n{b[tr3.exit_bar].ts[5:16]}  {tr3.net:+.2%}",
                    (tr3.exit_bar, b[tr3.exit_bar].close), xytext=(10, 28), textcoords="offset points",
                    fontsize=9, color="#555")
    ticks = list(range(lo, hi, max(1, (hi - lo) // 12)))
    ax.set_xticks(ticks, [b[i].ts[2:10] for i in ticks])
    ax.set_xlim(lo - 1, hi)
    ax.set_title(f"{code}：入场 {b[e30].ts[:16]} → 第四版（5分钟）出场 {xts[:16]}，持有 {tr5.exit_bar - tr5.entry_bar} 根 5 分钟，"
                 f"净 {tr5.net:+.2%}，触发 {[('类' if q else '') + str(k) + '卖@' + t[5:16] for k, t, q in sells]}；"
                 f"对照第三版（30分钟）{'-' if isinstance(tr3, str) else format(tr3.net, '+.2%')}（{why3}）",
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
    widx = {y.ts: i for i, y in enumerate(w)}
    for q in pts:
        if q.kind == 1 and q.is_buy and q.bar > top:
            big = widx.get(conf_ts) == q.bar
            ax2.scatter([q.bar], [w[q.bar].low], marker="^", s=260 if big else 110, color=GREEN, zorder=9,
                        edgecolor="white")
    five_axes(ax2, w, left, f"入场时的 5 分钟（30分钟 {b[t0].ts[5:16]} 收盘）：星 = 回拉的顶，绿▲ = 5分钟 1买/类1买"
                            f"（大的 = 确认用的 {conf_ts[5:16] if conf_ts else '-'}）")

    # ---- 出场时 5 分钟 ----
    wx = sub_window(b5, ts5, xts, 4000)
    ax_ = analyze(wx, check=True)
    lx = legs_from_bis(ax_.bis)
    zx = build_zhongshus(lx)
    ax3 = f.add_subplot(gs[1, 1])
    leftx = max(0, len(wx) - 260)
    candles(ax3, wx, leftx, len(wx))
    draw_bis(ax3, ax_, left=leftx)
    boxes(ax3, ax_, lx, zx, "#e0a050", "#a8681a", left=leftx, label="Z")
    wxi = {y.ts: i for i, y in enumerate(wx)}
    if b[e30].ts in wxi:
        ax3.scatter([wxi[b[e30].ts]], [b[e30].close], marker="^", s=170, color=BUY, zorder=10, edgecolor="white")
    for k, t, q in sells:
        if t in wxi:
            ax3.scatter([wxi[t]], [wx[wxi[t]].high], marker="v", s=150, color="#b00020", zorder=10, edgecolor="white")
            ax3.annotate(f"{'类' if q else ''}{k}卖 {t[5:16]}", (wxi[t], wx[wxi[t]].high), xytext=(4, 10),
                         textcoords="offset points", fontsize=9, color="#b00020")
    ax3.scatter([len(wx) - 1], [wx[-1].close], marker="v", s=170, color=INK, zorder=10)
    five_axes(ax3, wx, leftx, f"第四版出场时的 5 分钟（{xts[5:16]}）：红▼ = 引擎划出的卖点（触发出场），蓝▲ = 入场，黑▼ = 出场")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v3d_trade_{n:02d}_{code}_{t0}.png"
    f.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(f)
    print(f"{n:2d} {code} 入场 {b[e30].ts[5:16]} {b[e30].close:.2f} | 第四版出场 {xts[5:16]} {b5[tr5.exit_bar].close:.2f} {tr5.net:+.2%} "
          f"卖点{[('类' if q else '') + str(k) + '卖@' + t[5:16] for k, t, q in sells]} | 第三版 "
          f"{'-' if isinstance(tr3, str) else format(tr3.net, '+.2%')}({why3})")


def main(conf_dir, ex4_dir, ex5_dir, out):
    metas = sorted(Path(ex4_dir).glob("*.meta.json"))
    for n, m in enumerate(metas, 1):
        code, t0 = m.name.split(".")[0], int(m.name.split(".")[1])
        if not (Path(ex5_dir) / f"{code}.{t0}.jsonl").exists():
            continue
        one(code, t0, conf_dir, ex4_dir, ex5_dir, out, n)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4] if len(sys.argv) > 4 else "_plot/v3f"))
