"""画「5 分钟类 1买」和「回拉当前最低点所在那根 30 分钟 K 线」的相对位置。
用法：PYTHONPATH=. python _plot/v3_lowbar.py STEPS_DIR 例子序号 [OUT_DIR]
  取该次回拉里第一次出现 5 分钟 1买/类 1买 的那一步（30 分钟第 t 根收盘），画：
  黄带 = 回拉当前最低点所在那根 30 分钟 K 线（规则要求 5 分钟 1买 落在这里才买）
  蓝三角 = 引擎给出的 5 分钟 1买/类 1买；蓝星 = 回拉以来最低的那根 5 分钟
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, "_plot")
from fake3b import BUY, DOWN, INK, UP, candles  # noqa: E402
from v3_example import events  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def main(steps_dir: str, which: int, out: Path) -> None:
    from backtest.sublevel import _read_steps, sub_window, top5_index
    from chan.buysell import find_bspoints
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis
    from feeds import Level, cache

    code, t0, cand0, _ = list(events(steps_dir, need_confirm=False))[which - 1]
    steps = _read_steps(Path(steps_dir) / f"{code}.steps.jsonl")
    key = (cand0[0], cand0[1])
    b30 = cache.load(code, Level.M30)
    b30 = [replace(x, idx=i) for i, x in enumerate(b30[-4000:])]
    b5 = cache.load(code, Level.M5)
    ts5 = [x.ts for x in b5]

    hit = None
    for t in sorted(steps):
        c = steps[t]["cand"]
        if not c or (c[0], c[1]) != key:
            continue
        w = sub_window(b5, ts5, b30[t].ts, 4000)
        a = analyze(w, check=True)
        legs = legs_from_bis(a.bis)
        top = top5_index(w, c[5])
        i0 = next((i for i, x in enumerate(legs) if a.bis[x.src_start].start.peak_idx >= top), None)
        if i0 is None:
            continue
        zss = build_zhongshus(legs, start=i0)
        pts = find_bspoints(legs, zss, a.bis, macd([x.close for x in w]), include_quasi=True)
        buys = [p for p in pts if p.kind == 1 and p.is_buy and p.bar > top]
        if buys:
            hit = (t, c, w, a, legs, zss, top, buys)
            break
    if hit is None:
        print("该形态回拉后没有 5 分钟 1买/类 1买")
        return
    t, c, w, a, legs, zss, top, buys = hit
    low_open, low_ts = b30[c[4] - 1].ts, b30[c[4]].ts
    w2 = sub_window(b5, ts5, b30[min(t + 40, len(b30) - 1)].ts, 5000)
    off = next(i for i, x in enumerate(w2) if x.ts == w[0].ts)
    idx2 = {x.ts: i for i, x in enumerate(w2)}
    i_t = idx2[w[-1].ts]
    lo_i = min(range(top, len(w)), key=lambda i: w[i].low) + off
    band = [i for i, x in enumerate(w2) if low_open < x.ts <= low_ts]
    left, right = max(0, top + off - 70), min(len(w2), i_t + 160)
    pt = max(buys, key=lambda p: p.bar)
    ip = pt.bar + off
    in_band = low_open < w[pt.bar].ts <= low_ts

    f, ax = plt.subplots(figsize=(18, 8.6))
    candles(ax, w2, left, right)
    ax.axvspan(band[0] - 0.5, band[-1] + 0.5, color="#f2c94c", alpha=0.35, zorder=0)
    ax.text((band[0] + band[-1]) / 2, ax.get_ylim()[0], f"回拉当前最低点所在的 30 分钟 K 线\n"
            f"{low_open[5:16]} → {low_ts[5:16]}\n（规则要求 5 分钟 1买 落在这根里）",
            ha="center", va="bottom", fontsize=10, color="#8a6d00", fontweight="bold")
    for x in a.bis:
        s0, s1 = x.start.peak_idx + off, x.end.peak_idx + off
        if s1 >= left:
            ax.plot([s0, s1], [x.start.price, x.end.price], lw=1.8, zorder=4,
                    color=UP if x.direction.name == "UP" else DOWN, ls="-" if x.confirmed else ":")
    for k, z in enumerate(zss):
        s0 = a.bis[legs[z.start_leg].src_start].start.peak_idx + off
        s1 = a.bis[legs[z.core_end].src_end].end.peak_idx + off
        ax.add_patch(Rectangle((s0, z.zd), s1 - s0, z.zg - z.zd, facecolor="#e0a050", alpha=0.25,
                               edgecolor="#a8681a", lw=1.2))
        ax.text(s0, z.zg, f" 从顶重划的中枢 [{z.zd:.2f},{z.zg:.2f}]", fontsize=9, color="#a8681a",
                va="bottom")
    ax.scatter([top + off], [w[top].high], marker="*", s=220, color=INK, zorder=8)
    ax.annotate(f"回拉的顶 {w[top].high:.2f}\n{w[top].ts[5:16]}", (top + off, w[top].high),
                xytext=(6, 14), textcoords="offset points", fontsize=9.5)
    ax.scatter([lo_i], [w2[lo_i].low], marker="*", s=300, color=BUY, zorder=8, edgecolor="white")
    ax.annotate(f"回拉当前最低（原始K线）{w2[lo_i].low:.2f}\n{w2[lo_i].ts[5:16]}", (lo_i, w2[lo_i].low),
                xytext=(-150, -55), textcoords="offset points", fontsize=9.5, color=BUY,
                arrowprops=dict(arrowstyle="-", color=BUY, lw=0.7))
    ax.scatter([ip], [w2[ip].low], marker="^", s=190, color="#0b6e4f", zorder=9, edgecolor="white")
    ax.annotate(f"引擎的 5 分钟{'类' if pt.quasi else ''}1买\n{w2[ip].ts[5:16]}  {w2[ip].low:.2f}\n"
                f"{'在黄带里 → 算' if in_band else '不在黄带里 → 不算'}", (ip, w2[ip].low),
                xytext=(10, -70), textcoords="offset points", fontsize=10, color="#0b6e4f",
                fontweight="bold", arrowprops=dict(arrowstyle="-", color="#0b6e4f", lw=0.8))
    ax.axvline(i_t, color=INK, lw=0.9, ls=":")
    ax.text(i_t, ax.get_ylim()[1], f" 看到这个点的那一步（30分钟 {b30[t].ts[5:16]} 收盘）", va="top", fontsize=9.5)
    lo_after = min(range(i_t, right), key=lambda i: w2[i].low)
    if w2[lo_after].low < w2[lo_i].low:
        ax.scatter([lo_after], [w2[lo_after].low], marker="v", s=140, color="#b00020", zorder=8)
        ax.annotate(f"之后创出的更低点\n{w2[lo_after].ts[5:16]}  {w2[lo_after].low:.2f}", (lo_after, w2[lo_after].low),
                    xytext=(10, -40), textcoords="offset points", fontsize=9.5, color="#b00020")
    ax.set_title(f"{code}：5 分钟 1买/类 1买 与「回拉当前最低点那根 30 分钟 K 线」的位置（实线 = 当时看到的 5 分钟笔，橙框 = 从顶重划的中枢）",
                 fontsize=11, loc="left")
    ticks = list(range(left, right, max(1, (right - left) // 14)))
    ax.set_xticks(ticks, [w2[i].ts[5:16] for i in ticks], fontsize=8.5)
    ax.set_xlim(left - 1, right)
    ax.spines[["top", "right"]].set_visible(False)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v3_lowbar_{which:02d}_{code}.png"
    f.tight_layout()
    f.savefig(path, dpi=110)
    plt.close(f)
    print(code, "看到点的那步", b30[t].ts, "| 点", w[pt.bar].ts, "| 黄带", low_open, "→", low_ts, "| 在带里", in_band,
          "| 回拉当前最低", w2[lo_i].ts, w2[lo_i].low, "| 点的价位", w2[ip].low)
    print("写出", path)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/v3c"))
