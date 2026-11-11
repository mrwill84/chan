"""第三版例子「回拉」前后的 5 分钟详图：K 线 + 笔 + 笔中枢 + MACD 面积对比 + 5 分钟 1 类点。
用法：PYTHONPATH=. python _plot/v3_5min_detail.py STEPS_DIR 例子序号 [OUT_DIR] [之后多少根30分钟]
  实线 = 30 分钟第 t 根收盘那一刻 5 分钟上看到的笔（虚线 = 之后终版的笔）；橙框 = 当时的 5 分钟笔中枢。
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


def main(steps_dir: str, which: int, out: Path, after30: int) -> None:
    from backtest.sublevel import sub_points, sub_window
    from chan.macd import dif_extreme, hist_area, macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis, relation
    from feeds import Level, cache

    code, t, cand, conf = list(events(steps_dir, need_confirm=False))[which - 1]
    b30 = cache.load(code, Level.M30)
    b30 = [replace(x, idx=i) for i, x in enumerate(b30[-4000:])]
    b5 = cache.load(code, Level.M5)
    ts5 = [x.ts for x in b5]
    cut, st = b30[t].ts, cand[5]
    leave_ts = b30[cand[1]].ts                       # 30 分钟离开 A' 那一笔的起点
    w = sub_window(b5, ts5, cut, 4000)               # 当时看到的
    w2 = sub_window(b5, ts5, b30[min(t + after30, len(b30) - 1)].ts, 5000)   # 之后也画出来
    off = next(i for i, x in enumerate(w2) if x.ts == w[0].ts)
    idx2 = {x.ts: i for i, x in enumerate(w2)}

    a = analyze(w, check=True)
    legs = legs_from_bis(a.bis)
    zss = build_zhongshus(legs)
    fin = analyze(w2, check=False)
    m = macd([x.close for x in w2])

    i_leave = next(i for i, x in enumerate(w2) if x.ts > leave_ts)
    i_st = idx2[next(x.ts for x in w if x.ts > st)]
    i_now = idx2[w[-1].ts]
    lo = min(range(i_st - off, len(w)), key=lambda i: w[i].low) + off
    left, right = max(0, i_leave - 90), min(len(w2), i_now + 6 * after30 + 10)
    print(f"{code} 30分钟 t={t} {cut}；离开段起 {leave_ts}；回拉起点 {st}；回拉最低 {w2[lo].ts} {w2[lo].low:.2f}")

    f, (ax, ax2) = plt.subplots(2, 1, figsize=(19, 11.5), sharex=True,
                                gridspec_kw={"height_ratios": [3.2, 1.3], "hspace": 0.04})
    candles(ax, w2, left, right)

    # 笔：当时看到的（实线 / 未确认点线）+ 之后终版（虚线）
    seen = set()
    n = 0
    print("\n当时的 5 分钟笔（窗口内）：")
    print(" #  方向  起点              止点              价格               同号MACD面积   DIF极值")
    for x in a.bis:
        s0, s1 = x.start.peak_idx + off, x.end.peak_idx + off
        if s1 < left:
            continue
        up = x.direction.name == "UP"
        ax.plot([s0, s1], [x.start.price, x.end.price], color=UP if up else DOWN, lw=2.0,
                ls="-" if x.confirmed else ":", zorder=4)
        n += 1
        ar = hist_area(m, s0, s1, positive=up)
        de = dif_extreme(m, s0, s1, want_max=up)
        ax.text(s1, x.end.price, f"{n}", fontsize=9, color="#222", ha="center",
                va="bottom" if up else "top",
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.75), zorder=6)
        ax2.axvspan(s0, s1, color=UP if up else DOWN, alpha=0.06, lw=0)
        ax2.text((s0 + s1) / 2, 0, f"{n}\n{ar:+.1f}", fontsize=8, ha="center",
                 va="bottom" if not up else "top", color=UP if up else DOWN)
        print(f"{n:2d}  {'上' if up else '下'}  {w[x.start.peak_idx].ts[5:16]}  {w[x.end.peak_idx].ts[5:16]}  "
              f"{x.start.price:8.2f}→{x.end.price:8.2f}  {ar:10.2f}  {de:9.2f}"
              f"{'' if x.confirmed else '  (未确认)'}")
        seen.add((x.start.peak_idx, x.end.peak_idx))
    for x in fin.bis:
        s0, s1 = x.start.peak_idx, x.end.peak_idx
        if s0 + 0 >= i_now - 5 and x.end.peak_idx < right:
            ax.plot([s0, s1], [x.start.price, x.end.price], color=UP if x.direction.name == "UP" else DOWN,
                    lw=1.3, ls="--", alpha=0.55, zorder=3)

    # 笔中枢
    for k, z in enumerate(zss):
        s0 = a.bis[legs[z.start_leg].src_start].start.peak_idx + off
        s1 = a.bis[legs[z.core_end].src_end].end.peak_idx + off
        if s1 < left:
            continue
        rel = relation(zss[k - 1], z) if k else "-"
        ax.add_patch(Rectangle((max(s0, left), z.zd), s1 - max(s0, left), z.zg - z.zd,
                               facecolor="#e0a050", alpha=0.25, edgecolor="#a8681a", lw=1.3))
        ax.text(max(s0, left), z.zg, f" Z{k} [{z.zd:.2f},{z.zg:.2f}] 对前一个:{rel}", fontsize=9.5,
                color="#a8681a", va="bottom", fontweight="bold")

    # 5 分钟 1 类点（含类 1买）
    pts = sub_points(w, include_quasi=True, verify=1)
    for ts, is_buy in pts:
        if ts in idx2 and left <= idx2[ts] < right:
            i = idx2[ts]
            ax.scatter([i], [w2[i].low if is_buy else w2[i].high], marker="^" if is_buy else "v",
                       s=120, color=BUY, zorder=8, edgecolor="white")
            ax.text(i, w2[i].low if is_buy else w2[i].high, f" 5分钟{'买' if is_buy else '卖'}1类点\n {ts[5:16]}",
                    fontsize=8.5, color=BUY, va="top" if is_buy else "bottom")

    # 30 分钟边界
    for i, lab in ((i_leave, "30分钟 离开段起"), (i_st, "30分钟 回拉起点（离开段顶）"),
                   (i_now, "当前（30分钟第 t 根收盘）")):
        ax.axvline(i, color=INK, lw=0.9, ls=":")
        ax.text(i, ax.get_ylim()[1], f" {lab}", va="top", fontsize=9.5)
    ax.scatter([lo], [w2[lo].low], marker="*", s=300, color=BUY, zorder=8, edgecolor="white")
    ax.annotate(f"回拉最低 {w2[lo].low:.2f}\n{w2[lo].ts[5:16]}", (lo, w2[lo].low), xytext=(8, -42),
                textcoords="offset points", fontsize=9.5, color=BUY)
    ax.set_title(f"{code} 回拉前后的 5 分钟结构（当时 {cut}）：红绿 = 5分钟笔（编号同下表/下图），橙框 = 5分钟笔中枢，"
                 "虚线 = 之后终版的笔", fontsize=11, loc="left")

    # MACD
    xs = list(range(left, right))
    ax2.bar(xs, [m.hist[i] for i in xs], color=[UP if m.hist[i] >= 0 else DOWN for i in xs],
            width=0.8, alpha=0.75)
    ax2.plot(xs, [m.dif[i] for i in xs], color="#222", lw=1.0, label="DIF")
    ax2.plot(xs, [m.dea[i] for i in xs], color="#e8a33d", lw=1.0, label="DEA")
    ax2.axhline(0, color="#888", lw=0.6)
    ax2.legend(loc="upper left", fontsize=8, frameon=False)
    ax2.set_ylabel("MACD（数字 = 该笔同号柱面积）")
    ticks = list(range(left, right, max(1, (right - left) // 14)))
    ax2.set_xticks(ticks, [w2[i].ts[5:16] for i in ticks], fontsize=8)
    ax2.set_xlim(left - 1, right)
    for a_ in (ax, ax2):
        a_.spines[["top", "right"]].set_visible(False)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v3_5min_detail_{which:02d}_{code}.png"
    f.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(f)
    print("\n写出", path)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/v3c"),
         int(sys.argv[4]) if len(sys.argv) > 4 else 12)
