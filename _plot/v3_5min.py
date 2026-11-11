"""第三版例子的 5 分钟回拉结构：笔、笔中枢、c 对 b 背驰，看回拉是不是 aAbBc。
用法：PYTHONPATH=. python _plot/v3_5min.py STEPS_DIR 例子序号 [OUT_DIR]
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
    from backtest.sublevel import c_beichi, sub_window
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import TREND_DOWN, build_zhongshus, legs_from_bis, relation
    from feeds import Level, cache

    ev = list(events(steps_dir, need_confirm=False))
    code, t, cand, conf = ev[which - 1]
    b30 = cache.load(code, Level.M30)
    b30 = [replace(x, idx=i) for i, x in enumerate(b30[-4000:])]
    cut, st = b30[t].ts, cand[5]
    b5 = cache.load(code, Level.M5)
    w = sub_window(b5, [x.ts for x in b5], cut, 4000)
    a = analyze(w, check=True)
    legs = legs_from_bis(a.bis)
    zss = build_zhongshus(legs)
    m = macd([x.close for x in w])
    i0 = next(i for i, x in enumerate(w) if x.ts > st)
    print(f"{code} 30分钟 t={t} {cut}  回拉起点(离开段顶) {st}  5分钟 {len(w)} 根，起点下标 {i0}")

    def span(z):
        return (a.bis[legs[z.start_leg].src_start].start.peak_idx,
                a.bis[legs[z.core_end].src_end].end.peak_idx)

    print("5分钟笔中枢（最近 6 个）：")
    for n, z in enumerate(zss[-6:], max(0, len(zss) - 6)):
        s0, s1 = span(z)
        rel = relation(zss[n - 1], z) if n else "-"
        print(f"  Z{n} {w[s0].ts[5:16]}→{w[s1].ts[5:16]} [{z.zd:.2f},{z.zg:.2f}] "
              f"GG={z.gg:.2f} DD={z.dd:.2f} 与前一个：{rel} 走掉={z.depart_leg is not None}")
    lo = min(range(i0, len(w)), key=lambda i: w[i].low)
    print(f"回拉最低 {w[lo].low:.2f} @ {w[lo].ts}")
    li = len(legs) - 1
    res = {}
    for k in range(1, len(zss)):
        z = zss[k]
        if z.depart_leg is None or relation(zss[k - 1], z) != TREND_DOWN:
            continue
        for need in (False, True):
            j = c_beichi(legs, zss, k, a.bis, m, li + 1, need)
            res[(k, need)] = j
            print(f"  以 Z{k} 为 B：c_beichi(need_c3={need}) -> "
                  f"{'无背驰' if j is None else '1买 在 ' + w[a.bis[j].end.peak_idx].ts}")
    if not res:
        print("  5分钟上没有「A→B 下跌 且 B 被离开」的结构 → 回拉不成 aAbBc")

    lo_i = max(0, i0 - 250)
    hi = len(w)
    f, ax = plt.subplots(figsize=(18, 8.8))
    candles(ax, w, lo_i, hi)
    for x in a.bis:
        if x.end.peak_idx >= lo_i:
            ax.plot([x.start.peak_idx, x.end.peak_idx], [x.start.price, x.end.price],
                    color=UP if x.direction.name == "UP" else DOWN, lw=1.8,
                    ls="-" if x.confirmed else ":", zorder=4)
    for n, z in enumerate(zss):
        s0, s1 = span(z)
        if s1 < lo_i:
            continue
        ax.add_patch(Rectangle((max(s0, lo_i), z.zd), s1 - max(s0, lo_i), z.zg - z.zd,
                               facecolor="#e0a050", alpha=0.25, edgecolor="#a8681a", lw=1.3))
        ax.text(max(s0, lo_i), z.zg, f"Z{n} [{z.zd:.2f},{z.zg:.2f}]", fontsize=9.5,
                color="#a8681a", va="bottom", fontweight="bold")
    ax.axvline(i0, color=INK, lw=0.9, ls=":")
    ax.text(i0, ax.get_ylim()[1], " 回拉起点", va="top", fontsize=10)
    ax.scatter([lo], [w[lo].low], marker="*", s=260, color=BUY, zorder=8, edgecolor="white")
    ticks = list(range(lo_i, hi, max(1, (hi - lo_i) // 12)))
    ax.set_xticks(ticks, [w[i].ts[5:16] for i in ticks])
    ax.set_xlim(lo_i - 1, hi)
    ax.set_title(f"{code} 5分钟回拉结构（截至 30 分钟 {cut}）：红绿 = 5分钟笔，橙框 = 5分钟笔中枢",
                 fontsize=11, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    f.tight_layout()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v3_5min_{which:02d}_{code}.png"
    f.savefig(path, dpi=115)
    print("写出", path)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/v3"))
