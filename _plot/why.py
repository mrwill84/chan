"""把相邻两个中枢的中心定理二判据画出来：ZD/ZG 区间 + GG/DD 包络 + 判定余量。

用法：PYTHONPATH=. python _plot/why.py <code> <level> <k> <out.png> [start]

20课 p43 中心定理二用的是**包络**不是中枢区间：
  后 GG < 前 DD  → 下跌及其延续
  后 DD > 前 GG  → 上涨及其延续
  后 ZG<前 ZD 且 后 GG>=前 DD → 高级别走势中枢
所以「两个框明明不重叠」和「判成 higher_level」可以同时成立——看的是 GG/DD
那两条线，不是框。这张图就是为了让那两条线和它们之间的余量可见。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

from chan.pipeline import analyze
from chan.tree import build_tree
from chan.zhongshu import legs_from_segs, relation
from feeds import Level, cache

UP, DOWN = "#d62728", "#2ca02c"


def main(code, level, k, out, start=0):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    bis = a.bis
    L = build_tree(legs_from_segs(a.segs, bis), start=start)[0]
    legs, zss = L.legs, L.zhongshus
    p, c = zss[k], zss[k + 1]

    x0 = lambda i: bis[legs[i].src_start].start.peak_idx
    x1 = lambda i: bis[legs[i].src_end].end.peak_idx
    lo, hi = max(0, p.start_leg - 2), min(len(legs) - 1, c.core_end + 2)

    fig, ax = plt.subplots(figsize=(14, 7))
    ys = []
    for i in range(lo, hi + 1):
        g = legs[i]
        ax.plot([x0(i), x1(i)], [g.start_price, g.end_price],
                color=UP if g.end_price >= g.start_price else DOWN, lw=1.8, zorder=4)
        ys += [g.start_price, g.end_price]
    rng = max(ys) - min(ys)
    xa, xb = x0(lo), x1(hi)

    for z, col, name in ((p, "#9ecae1", f"前中枢{k}"),
                         (c, "#fdae6b", f"后中枢{k+1}")):
        a0, a1 = x0(z.start_leg), x1(z.core_end)
        ax.add_patch(Rectangle((a0, z.zd), a1 - a0, max(z.zg - z.zd, 1e-9),
                               fc=col, ec="#444", lw=1.1, alpha=.65, zorder=3))
        ax.text((a0 + a1) / 2, z.zg + rng * .012,
                f"{name}  ZD{z.zd:.2f}~ZG{z.zg:.2f}", ha="center", va="bottom",
                fontsize=10, fontweight="bold")
        for val, lbl in ((z.gg, "GG"), (z.dd, "DD")):
            ax.hlines(val, a0, a1, color="#333", ls="--", lw=1.2, zorder=5)
            ax.text(a1, val, f" {lbl} {val:.2f}", va="center", fontsize=9, color="#333")

    # 判据余量：后GG 与 前DD 的关系（下跌判据）
    gap = c.gg - p.dd
    ax.hlines([p.dd, c.gg], xa, xb, color="#d62728", lw=1.0, alpha=.5, zorder=2)
    ax.annotate("", xy=(xb, p.dd), xytext=(xb, c.gg),
                arrowprops=dict(arrowstyle="<->", color="#d62728", lw=2))
    ax.text(xb, (p.dd + c.gg) / 2,
            f"  后GG {c.gg:.2f} − 前DD {p.dd:.2f} = {gap:+.2f}\n"
            f"  要判下跌需 < 0", color="#d62728", fontsize=11,
            fontweight="bold", va="center")

    ax.set_title(
        f"{code} {level}  中枢{k} → 中枢{k+1} 为什么是 {relation(p, c)}"
        + (f"（起点 段{start}）" if start else "")
        + f"\n中枢区间 [{p.zd:.2f},{p.zg:.2f}] 与 [{c.zd:.2f},{c.zg:.2f}] 确实不重叠，"
          f"但中心定理二看的是包络：后GG {c.gg:.2f} ≥ 前DD {p.dd:.2f}，差 {gap:+.2f} 元",
        fontsize=11)
    ticks = range(lo, hi + 1, max(1, (hi - lo) // 8))
    ax.set_xticks([x0(t) for t in ticks])
    ax.set_xticklabels([bars[x0(t)].ts[:10] for t in ticks], fontsize=8, rotation=20)
    ax.grid(alpha=.25, lw=.5)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(f"{out}  relation={relation(p, c)}  后GG-前DD={gap:+.2f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4],
         int(sys.argv[5]) if len(sys.argv) > 5 else 0)
