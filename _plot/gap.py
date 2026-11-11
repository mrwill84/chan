"""走势类型之间的连接段被丢掉了——上一层的折线因此是断的。

用法：PYTHONPATH=. python _plot/gap.py <code> <level> <out.png> [start]

上图  本级别（L0）的全部段。四个走势类型各占一条色带，**没有任何走势类型
      认领的段**用紫色加粗标出来。
下图  这四个走势类型升上去变成 L1 的四根「段」，画在它们真实的位置上。
      两根之间的红色虚线就是丢掉的那根连接段：价格真的走过那一程，而 L1
      看不见它。`build_zhongshus` 在 L1 上求重叠时，用的是一条断掉的折线。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

from chan.pipeline import analyze
from chan.tree import build_tree, trend_to_leg
from chan.zhongshu import legs_from_segs
from feeds import Level, cache

UP, DOWN, ORPHAN = "#d62728", "#2ca02c", "#9b1fb0"
BANDS = ["#9ecae1", "#fdae6b", "#a1d99b", "#bcbddc", "#fdd0a2", "#c7e9c0"]


def main(code, level, out, start=0):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    bis = a.bis
    L = build_tree(legs_from_segs(a.segs, bis), start=start)[0]
    legs, trends = L.legs, L.trends

    x0 = lambda i: bis[legs[i].src_start].start.peak_idx
    x1 = lambda i: bis[legs[i].src_end].end.peak_idx

    used = set()
    for t in trends:
        used |= set(range(t.start_leg, t.end_leg + 1))
    orphans = sorted(set(range(len(legs))) - used)

    fig, axes = plt.subplots(2, 1, figsize=(16, 11), sharex=True, sharey=True)

    # ---------------- 上：L0 ----------------
    ax = axes[0]
    ys = []
    for i, g in enumerate(legs):
        orph = i in orphans
        ax.plot([x0(i), x1(i)], [g.start_price, g.end_price],
                color=ORPHAN if orph else (UP if g.end_price >= g.start_price else DOWN),
                lw=4.5 if orph else 1.6, zorder=6 if orph else 4,
                solid_capstyle="round")
        ys += [g.start_price, g.end_price]
    rng = max(ys) - min(ys)
    for i in orphans:
        g = legs[i]
        ax.annotate(f"段{i}\n{g.start_price:.2f}→{g.end_price:.2f}\n没人要",
                    xy=((x0(i) + x1(i)) / 2, (g.start_price + g.end_price) / 2),
                    xytext=((x0(i) + x1(i)) / 2, max(ys) + rng * .03),
                    ha="center", va="bottom", fontsize=9.5, color=ORPHAN,
                    fontweight="bold", linespacing=1.35,
                    arrowprops=dict(arrowstyle="->", color=ORPHAN, lw=1.6))
    base = min(ys) - rng * .07
    for t in trends:
        ax.plot([x0(t.start_leg), x1(t.end_leg)], [base, base],
                lw=9, color=BANDS[t.index % len(BANDS)], solid_capstyle="butt")
        ax.text((x0(t.start_leg) + x1(t.end_leg)) / 2, base - rng * .035,
                f"走势{t.index} {t.kind}\n段{t.start_leg}..{t.end_leg}",
                ha="center", va="top", fontsize=9, linespacing=1.3)
    ax.set_title(f"L0　{len(legs)} 根段，{len(used)} 根被走势类型认领，"
                 f"{len(orphans)} 根没人要：段{orphans}", fontsize=12.5)

    # ---------------- 下：L1 ----------------
    ax = axes[1]
    l1 = [trend_to_leg(t, i) for i, t in enumerate(trends)]
    pts = []
    for g, t in zip(l1, trends):
        xa, xb = x0(t.start_leg), x1(t.end_leg)
        # 走势类型内部越过两端的那一截（71课 p190：端点未必是极值）。灰影是
        # 真实振幅，直线只是两端连线——不画灰影的话，眼睛会以为价格没去过那里。
        lo, hi = min(g.start_price, g.end_price), max(g.start_price, g.end_price)
        if g.low < lo - 1e-9 or g.high > hi + 1e-9:
            ax.add_patch(plt.Rectangle((xa, g.low), xb - xa, g.high - g.low,
                                       fc="#999", ec="none", alpha=.15, zorder=1))
        ax.plot([xa, xb], [g.start_price, g.end_price],
                color=UP if g.direction.name == "UP" else DOWN, lw=3.2, zorder=5)
        ax.plot([xa, xb], [g.start_price, g.end_price], "o", ms=7,
                color="#222", zorder=6)
        ax.text((xa + xb) / 2, (g.start_price + g.end_price) / 2 + rng * .035,
                f"L1 leg{g.index}\n{g.start_price:.2f}→{g.end_price:.2f}",
                ha="center", va="bottom", fontsize=9, linespacing=1.3)
        pts.append((xa, xb, g, t))

    for (xa0, xb0, g0, t0), (xa1, xb1, g1, t1) in zip(pts, pts[1:]):
        d = g1.start_price - g0.end_price
        if abs(d) < 1e-9:
            continue
        miss = [i for i in orphans if t0.end_leg < i < t1.start_leg]
        ax.plot([xb0, xa1], [g0.end_price, g1.start_price],
                color=ORPHAN, lw=2.6, ls="--", zorder=7)
        ax.annotate(f"缺口 {abs(d):.2f} 元\n丢掉的是 段{miss[0] if miss else '?'}",
                    xy=((xb0 + xa1) / 2, (g0.end_price + g1.start_price) / 2),
                    xytext=((xb0 + xa1) / 2, min(ys) + rng * .10),
                    ha="center", va="bottom", fontsize=10, color=ORPHAN,
                    fontweight="bold", linespacing=1.35,
                    arrowprops=dict(arrowstyle="->", color=ORPHAN, lw=1.8))
    breaks = sum(abs(b[2].start_price - a[2].end_price) > 1e-9
                 for a, b in zip(pts, pts[1:]))
    ax.set_title("L1　这几根就是上一层的「段」。" + (
        f"紫虚线处折线是断的（{breaks} 处）——`build_zhongshus` 却假设它首尾相接"
        if breaks else "折线首尾相接，没有断点 ✓（C-join）"), fontsize=12.5)

    ax.set_ylim(min(ys) - rng * .18, max(ys) + rng * .16)
    ticks = range(0, len(legs), max(1, len(legs) // 11))
    ax.set_xticks([x0(t) for t in ticks])
    ax.set_xticklabels([bars[x0(t)].ts[:10] for t in ticks], fontsize=8, rotation=18)
    for a_ in axes:
        a_.grid(alpha=.22, lw=.5)
    fig.suptitle(f"{code} {level}　走势类型之间的连接段去哪了"
                 + (f"（起点 段{start}）" if start else ""), fontsize=14, y=.985)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(f"{out}  没人要的段 {orphans}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3],
         int(sys.argv[4]) if len(sys.argv) > 4 else 0)
