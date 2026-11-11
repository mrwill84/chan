"""C-enter 改前改后对照：进入段的终点算不算进 GG/DD。

用法：PYTHONPATH=. python _plot/c_enter.py <code> <level> <k> <out.png> [start]

上下两张图用的是**同一串段、同一个中枢划分**（ZD/ZG 不受这条规则影响，中枢
边界一根段都没动），差别只在包络盒 [DD, GG] 怎么取：

  上 旧口径 —— 首构成段两端都进 GG/DD。前中枢的 DD 于是钉在进入段 a 砸出来
     的那个低点上，和后中枢的包络擦出一条缝，中心定理二判 higher_level。
  下 新口径 —— 首构成段只贡献远端极值（18课 p39「围绕走势中枢产生的」）。
     DD 抬回中枢自己震荡出来的低点，两盒分开，趋势成立。

虚线框 = 包络 [DD, GG]，实心框 = 中枢区间 [ZD, ZG]。被剔掉的那个端点用 ✕ 标。
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
from chan.types import Direction
from chan.zhongshu import _envelope, legs_from_segs, relation
from feeds import Level, cache

UP, DOWN = "#d62728", "#2ca02c"
BOX_P, BOX_C = "#9ecae1", "#fdae6b"


def old_env(legs, z):
    """旧口径的 (gg, dd)：首构成段两端都算。中枢边界不变，所以能直接重算。"""
    gg, dd, _, _ = _envelope(legs[z.start_leg : z.core_end + 1], z.direction,
                             has_entry=False)
    return gg, dd


def draw(ax, legs, bis, lo, hi, x0, x1, pairs, title):
    ys = []
    for i in range(lo, hi + 1):
        g = legs[i]
        ax.plot([x0(i), x1(i)], [g.start_price, g.end_price],
                color=UP if g.end_price >= g.start_price else DOWN, lw=1.8, zorder=4)
        ys += [g.start_price, g.end_price]
    rng = max(ys) - min(ys)
    for (z, (gg, dd)), col in zip(pairs, (BOX_P, BOX_C)):
        a0, a1 = x0(z.start_leg), x1(z.core_end)
        ax.add_patch(Rectangle((a0, dd), a1 - a0, gg - dd,
                               fc=col, ec=col, lw=1.6, ls="--", alpha=.18, zorder=2))
        ax.add_patch(Rectangle((a0, z.zd), a1 - a0, max(z.zg - z.zd, 1e-9),
                               fc=col, ec="#444", lw=1.1, alpha=.7, zorder=3))
        ax.text((a0 + a1) / 2, (z.zd + z.zg) / 2, f"中枢{z.index}",
                ha="center", va="center", fontsize=12, fontweight="bold", zorder=6)
        ax.text((a0 + a1) / 2, gg + rng * .02,
                f"区间 {z.zd:.2f}~{z.zg:.2f}\n包络 DD {dd:.2f} ~ GG {gg:.2f}",
                ha="center", va="bottom", fontsize=8.5, color="#333", linespacing=1.3)
    ax.set_ylim(min(ys) - rng * .06, max(ys) + rng * .12)
    ax.set_title(title, fontsize=12, color="#333")
    return ys, rng


def main(code, level, k, out, start=0):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    bis = a.bis
    L = build_tree(legs_from_segs(a.segs, bis), start=start)[0]
    legs, (p, c) = L.legs, (L.zhongshus[k], L.zhongshus[k + 1])

    x0 = lambda i: bis[legs[i].src_start].start.peak_idx
    x1 = lambda i: bis[legs[i].src_end].end.peak_idx
    lo, hi = max(0, p.start_leg - 1), min(len(legs) - 1, c.core_end + 1)

    old = [old_env(legs, z) for z in (p, c)]
    new = [(z.gg, z.dd) for z in (p, c)]
    # 旧口径下的 relation：中心定理二只看 GG/DD 和 ZG/ZD，后者没变
    from chan.zhongshu import AMBIGUOUS, HIGHER_LEVEL, TREND_DOWN, TREND_UP
    from chan.num import ge, gt, le, lt
    (pgg, pdd), (cgg, cdd) = old
    if lt(cgg, pdd):
        rel_old = TREND_DOWN
    elif gt(cdd, pgg):
        rel_old = TREND_UP
    elif (lt(c.zg, p.zd) and ge(cgg, pdd)) or (gt(c.zd, p.zg) and le(cdd, pgg)):
        rel_old = HIGHER_LEVEL
    else:
        rel_old = AMBIGUOUS
    rel_new = relation(p, c)

    gap_old = min(pgg, cgg) - max(pdd, cdd)
    gap_new = min(p.gg, c.gg) - max(p.dd, c.dd)

    fig, axes = plt.subplots(2, 1, figsize=(15, 11), sharex=True)
    draw(axes[0], legs, bis, lo, hi, x0, x1, list(zip((p, c), old)),
         f"旧口径　首构成段两端都进 GG/DD　→ 两盒"
         + (f"相交 {gap_old:.2f} 元" if gap_old > 0 else f"相距 {-gap_old:.2f} 元")
         + f"　relation = {rel_old}")
    ys, rng = draw(axes[1], legs, bis, lo, hi, x0, x1, list(zip((p, c), new)),
                   f"新口径（C-enter）　首构成段只贡献远端极值　→ 两盒"
                   + (f"相交 {gap_new:.2f} 元" if gap_new > 0 else f"相距 {-gap_new:.2f} 元")
                   + f"　relation = {rel_new}")

    # 标出被剔掉的那个端点：前中枢首构成段的近端
    for ax in axes:
        for z, (ogg, odd) in zip((p, c), old):
            if z.start_leg == 0:
                continue
            f = legs[z.start_leg]
            near = f.low if z.direction is Direction.UP else f.high
            if abs(near - (odd if z.direction is Direction.UP else ogg)) > 1e-9:
                continue  # 这个端点本来就不是极值，剔不剔都一样
            ax.plot([(x0(z.start_leg) + x1(z.start_leg)) / 2], [near],
                    marker="x", ms=15, mew=3.5, color="#7b3294", zorder=8)
            ax.text((x0(z.start_leg) + x1(z.start_leg)) / 2, near - rng * .035,
                    f"段{z.start_leg} 的近端 {near:.2f}\n= 进入段 段{z.start_leg - 1} 的终点",
                    ha="center", va="top", fontsize=9, color="#7b3294",
                    fontweight="bold", linespacing=1.3)

    ticks = range(lo, hi + 1, max(1, (hi - lo) // 9))
    axes[1].set_xticks([x0(t) for t in ticks])
    axes[1].set_xticklabels([bars[x0(t)].ts[:10] for t in ticks], fontsize=8, rotation=18)
    for ax in axes:
        ax.grid(alpha=.22, lw=.5)

    fig.suptitle(f"{code} {level}　中枢{k} → 中枢{k+1}"
                 + (f"（起点 段{start}）" if start else "")
                 + "　C-enter 改前 / 改后　— 同一串段、同一个划分，ZD/ZG 一分不动",
                 fontsize=14, y=.995)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(f"{out}  旧 {rel_old}（盒距 {gap_old:+.2f}）→ 新 {rel_new}（盒距 {gap_new:+.2f}）")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4],
         int(sys.argv[5]) if len(sys.argv) > 5 else 0)
