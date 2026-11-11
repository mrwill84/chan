"""第三版交易持有期间的 5 分钟结构：笔、中枢、MACD、每个向上笔的红柱面积、5 分钟 1 类点。
用法：PYTHONPATH=. python _plot/v3_trade_5min.py STEPS_DIR EXIT_DIR 序号 [OUT_DIR]
  序号 = `_plot/v3_trades.py` 的图序号（按 meta 文件名排序）。
  从入场前 40 根 5 分钟画到出场后约 60 根；笔/中枢取出场之后一小段时刻的划线（比当时看到的更完整），
  所以和 `v3_trades.py` 右下角「出场时」那张有出入；引擎的 5 分钟 1 类点同理。
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


def main(steps_dir: str, exit_dir: str, n: int, out: Path) -> None:
    from backtest.costs import Costs, enter_long, exit_long, prev_day_close
    from backtest.sublevel import _read_steps, structural_exit_v3, sub_window
    from chan.buysell import find_bspoints
    from chan.macd import dif_extreme, hist_area, macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis
    from feeds import Level, cache

    metas = sorted(Path(exit_dir).glob("*.meta.json"))
    m = metas[n - 1]
    code, t0 = m.name.split(".")[0], int(m.name.split(".")[1])
    b = cache.load(code, Level.M30)
    b = [replace(x, idx=i) for i, x in enumerate(b[-4000:])]
    b5 = cache.load(code, Level.M5)
    ts5 = [x.ts for x in b5]
    meta = json.loads(m.read_text(encoding="utf-8"))
    ex = _read_steps(Path(exit_dir) / f"{code}.{t0}.jsonl")
    e = enter_long(b, t0, code, prev_day_close(b))
    x, why = structural_exit_v3(ex, b, e, meta["p1"], 160)
    tr = exit_long(b, t0, e, x, code, Costs(slippage=0.001), ref=prev_day_close(b), intraday=True)

    w = sub_window(b5, ts5, b[min(tr.exit_bar + 10, len(b) - 1)].ts, 6000)
    idx = {y.ts: i for i, y in enumerate(w)}
    ie = next(i for i, y in enumerate(w) if y.ts > b[tr.entry_bar].ts) - 1
    ix = idx[b[tr.exit_bar].ts]
    left, right = max(0, ie - 40), min(len(w), ix + 60)
    a = analyze(w, check=True)
    legs = legs_from_bis(a.bis)
    zss = build_zhongshus(legs)
    mm = macd([y.close for y in w])
    pts = find_bspoints(legs, zss, a.bis, mm, include_quasi=True)

    f, (ax, ax2) = plt.subplots(2, 1, figsize=(20, 11.5), sharex=True,
                                gridspec_kw={"height_ratios": [3.2, 1.3], "hspace": 0.04})
    candles(ax, w, left, right)
    print(f"{code} 入场 {w[ie].ts} {b[tr.entry_bar].close:.2f}  出场 {w[ix].ts} {b[tr.exit_bar].close:.2f}  原因 {why}")
    print(" #  方向  起点            止点            价格              同号柱面积   DIF极值")
    k = 0
    for y in a.bis:
        s0, s1 = y.start.peak_idx, y.end.peak_idx
        if s1 < left or s0 > right:
            continue
        up = y.direction.name == "UP"
        ax.plot([s0, s1], [y.start.price, y.end.price], lw=1.9, zorder=4, color=UP if up else DOWN,
                ls="-" if y.confirmed else ":")
        k += 1
        ar = hist_area(mm, s0, s1, positive=up)
        de = dif_extreme(mm, s0, s1, want_max=up)
        ax.text(s1, y.end.price, f"{k}", fontsize=8.5, ha="center", va="bottom" if up else "top",
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.75), zorder=6)
        ax2.axvspan(s0, s1, color=UP if up else DOWN, alpha=0.05, lw=0)
        if up:
            ax2.text((s0 + s1) / 2, 0, f"{k}\n{ar:.1f}", fontsize=8, ha="center", va="top", color=UP)
        print(f"{k:2d}  {'上' if up else '下'}  {w[s0].ts[5:16]}  {w[s1].ts[5:16]}  "
              f"{y.start.price:8.2f}→{y.end.price:8.2f}  {ar:10.1f}  {de:8.2f}{'' if y.confirmed else '  (未确认)'}")
    for j, z in enumerate(zss):
        s0 = a.bis[legs[z.start_leg].src_start].start.peak_idx
        s1 = a.bis[legs[z.core_end].src_end].end.peak_idx
        if s1 < left or s0 > right:
            continue
        ax.add_patch(Rectangle((max(s0, left), z.zd), s1 - max(s0, left), z.zg - z.zd, facecolor="#e0a050",
                               alpha=0.22, edgecolor="#a8681a", lw=1.2))
        ax.text(max(s0, left), z.zg, f" Z{j} [{z.zd:.2f},{z.zg:.2f}]", fontsize=8.5, color="#a8681a", va="bottom")
    for p in pts:
        if p.kind in (1, 3) and left <= p.bar < right:
            tag = f"{'类' if p.quasi else ''}{p.kind}{'买' if p.is_buy else '卖'}"
            ax.scatter([p.bar], [w[p.bar].low if p.is_buy else w[p.bar].high], marker="^" if p.is_buy else "v",
                       s=120, color="#0b6e4f" if p.is_buy else "#b00020", zorder=9, edgecolor="white")
            ax.annotate(f"{tag}\n{w[p.bar].ts[5:16]}", (p.bar, w[p.bar].low if p.is_buy else w[p.bar].high),
                        xytext=(3, -26 if p.is_buy else 8), textcoords="offset points", fontsize=8,
                        color="#0b6e4f" if p.is_buy else "#b00020")
            print(f"   5分钟 {tag} @ {w[p.bar].ts[5:16]} {w[p.bar].high if not p.is_buy else w[p.bar].low:.2f}")
    ax.scatter([ie], [b[tr.entry_bar].close], marker="^", s=200, color=BUY, zorder=10, edgecolor="white")
    ax.annotate(f"入场 {b[tr.entry_bar].close:.2f}", (ie, b[tr.entry_bar].close), xytext=(-60, 25),
                textcoords="offset points", fontsize=10, color=BUY)
    ax.scatter([ix], [b[tr.exit_bar].close], marker="v", s=200, color=INK, zorder=10)
    ax.annotate(f"出场 {b[tr.exit_bar].close:.2f}  {tr.net:+.2%}", (ix, b[tr.exit_bar].close), xytext=(8, -34),
                textcoords="offset points", fontsize=10, color=INK)
    ax.axhline(meta["p1"], color=BUY, lw=0.8, ls="--", alpha=0.6)
    ax.set_title(f"{code} 持有期间的 5 分钟结构（实线 = 笔，点线 = 未确认；橙框 = 5 分钟笔中枢；三角 = 引擎的 5 分钟 1/3 类点；"
                 f"下图数字 = 向上笔红柱面积）", fontsize=11, loc="left")
    xs = list(range(left, right))
    ax2.bar(xs, [mm.hist[i] for i in xs], color=[UP if mm.hist[i] >= 0 else DOWN for i in xs], width=0.8, alpha=0.75)
    ax2.plot(xs, [mm.dif[i] for i in xs], color="#222", lw=1.0)
    ax2.plot(xs, [mm.dea[i] for i in xs], color="#e8a33d", lw=1.0)
    ax2.axhline(0, color="#888", lw=0.6)
    ticks = list(range(left, right, max(1, (right - left) // 16)))
    ax2.set_xticks(ticks, [w[i].ts[5:16] for i in ticks], fontsize=8)
    ax2.set_xlim(left - 1, right)
    for a_ in (ax, ax2):
        a_.spines[["top", "right"]].set_visible(False)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v3_trade5_{n:02d}_{code}.png"
    f.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(f)
    print("写出", path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), Path(sys.argv[4] if len(sys.argv) > 4 else "_plot/v3e"))
