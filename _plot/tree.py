"""逐层画级别树：每一层一张子图，下层是段，上层是走势类型。

用法：PYTHONPATH=. python _plot/tree.py <code> <level> <out.png>

和 abc.py / split.py 的分工：那两张图回答「某一个中枢长什么样」，这张回答
「整只票分几级、每级看到什么」。平表最大的毛病是把 2 段的中枢和 23 段的中枢
并排列在一行上，这张图直接让它们待在不同的子图里。
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
from chan.tree import NO_ZHONGSHU, build_tree, leg_bars
from chan.zhongshu import TREND_DOWN, TREND_UP, classify, legs_from_segs, relation
from feeds import Level, cache

UP, DOWN = "#d62728", "#2ca02c"        # A 股习惯：红涨绿跌
PLAIN, SPLIT = "#9ecae1", "#fdae6b"    # 普通中枢 / 拆出来的子中枢
KIND = {TREND_UP: UP, TREND_DOWN: DOWN, "panzheng": "#7f7f7f",
        NO_ZHONGSHU: "#9b1fb0"}   # 本级别看不出中枢，要降级别看


def draw(ax, bars, bis, L, layers):
    """L.legs 的下标空间是本层；要落到 K 线下标得一路往下翻到 level 0。"""
    x0 = lambda i: leg_bars(layers, bis, L.level, i)[0]
    x1 = lambda i: leg_bars(layers, bis, L.level, i)[1]

    xs, ys = [], []
    for i, g in enumerate(L.legs):
        if not xs:
            xs.append(x0(i)); ys.append(g.start_price)
        xs.append(x1(i)); ys.append(g.end_price)
    # 真实极值也要进 y 轴范围：上面几层的一根段内部可以越过自己的两端
    lim = ys + [g.low for g in L.legs] + [g.high for g in L.legs]
    rng = max(lim) - min(lim)

    # 折线按方向上红下绿。**先画真实振幅再画折线**：71课 p190「没有人规定分段
    # 的结束位置一定是最高、最低的」，上面几层的一根「段」是一整个走势类型，
    # 它内部越过两端的那一截是结构本身，不是 bug。只画 start→end 一条直线，
    # 眼睛会以为价格没去过那里——数据（`Leg.high/low`）一直是对的，骗人的是画法。
    for i, g in enumerate(L.legs):
        lo, hi = min(g.start_price, g.end_price), max(g.start_price, g.end_price)
        if g.low < lo - 1e-9 or g.high > hi + 1e-9:
            ax.add_patch(Rectangle((x0(i), g.low), x1(i) - x0(i), g.high - g.low,
                                   fc="#999", ec="none", alpha=.13, zorder=1))
        ax.plot([x0(i), x1(i)], [g.start_price, g.end_price],
                color=UP if g.end_price >= g.start_price else DOWN,
                lw=1.6, zorder=4, solid_capstyle="round")
    ax.plot(xs, ys, "o", color="#333", ms=2.6, zorder=5)
    ys = lim

    # 走势类型：底部一条色带，颜色 = 盘整/上涨/下跌
    base = min(ys) - rng * .10
    n_sub = 0
    for t in L.trends:
        xa, xb = x0(t.start_leg), x1(t.end_leg)
        ax.add_patch(Rectangle((xa, base), xb - xa, rng * .035,
                               fc=KIND[t.kind], ec="none", alpha=.55, zorder=2))
        ax.text((xa + xb) / 2, base - rng * .012,
                f"走势{t.index}\n{t.kind}", ha="center", va="top",
                fontsize=6.5, color=KIND[t.kind])
        # 子结构（C-sub）：走势类型没被拆，但三等分的结果留着。一个 21 个月的
        # 「下跌趋势」标签方向是对的、操作上等于没说，这一行就是它缺的那一半。
        for k in t.substructure:
            n_sub += 1
            ka, kb = x0(k.start_leg), x1(k.end_leg)
            ax.add_patch(Rectangle((ka, base - rng * .105), kb - ka, rng * .028,
                                   fc=SPLIT, ec="white", lw=.7, alpha=.75, zorder=2))
            ax.add_patch(Rectangle((ka, k.zd), kb - ka, max(k.zg - k.zd, 1e-9),
                                   fc=SPLIT, ec="#a6621f", lw=.9, ls=":",
                                   alpha=.35, zorder=2))
            ax.text((ka + kb) / 2, base - rng * .091, f"{k.zd:.1f}~{k.zg:.1f}",
                    ha="center", va="center", fontsize=5.5, color="#5a3410")

    # 中枢
    for z in L.zhongshus:
        xa, xb = x0(z.start_leg), x1(z.core_end)
        kid = z.index in L.split_from
        ax.add_patch(Rectangle((xa, z.zd), xb - xa, max(z.zg - z.zd, 1e-9),
                               fc=SPLIT if kid else PLAIN, ec="#444",
                               lw=1.1 if kid else .8, alpha=.62, zorder=3))
        ax.text((xa + xb) / 2, z.zg + rng * .012,
                f"中枢{z.index}" + ("*" if kid else ""), ha="center",
                va="bottom", fontsize=8.5, fontweight="bold", color="#222")
        ax.text((xa + xb) / 2, z.zd - rng * .012,
                f"{z.zd:.2f}~{z.zg:.2f}\n{z.core_end - z.start_leg + 1}段",
                ha="center", va="top", fontsize=6, color="#555")

    rel = " | ".join(f"{j}→{j+1} {relation(L.zhongshus[j], L.zhongshus[j+1])}"
                     for j in range(len(L.zhongshus) - 1)) or "—"
    ax.set_title(
        f"L{L.level}   次级别走势类型 {len(L.legs)} 个   中枢 {len(L.zhongshus)} 个"
        f"（* = 过度延伸拆出来的子中枢，{len(L.split_from)} 个）   "
        f"走势类型 {len(L.trends)} 个   classify = {classify(L.zhongshus)}"
        + (f"   子结构 {n_sub} 个（橙：没拆，另记的次级别读法）" if n_sub else "")
        + f"\n相邻关系: {rel}", fontsize=10)
    ax.set_ylim(base - rng * (.14 if n_sub else .16), max(ys) + rng * .09)
    step = max(1, len(L.legs) // 9)
    ticks = list(range(0, len(L.legs), step))
    ax.set_xticks([x0(t) for t in ticks])
    ax.set_xticklabels([bars[x0(t)].ts[:10] for t in ticks], fontsize=7, rotation=20)
    ax.grid(alpha=.22, lw=.5)


def main(code, level, out, start=0):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    layers = build_tree(legs_from_segs(a.segs, a.bis), start=start)
    fig, axes = plt.subplots(len(layers), 1, figsize=(15, 4.8 * len(layers)))
    if len(layers) == 1:
        axes = [axes]
    for ax, L in zip(axes, layers):
        draw(ax, bars, a.bis, L, layers)
    fig.suptitle(f"{code} {level}  级别树（{len(layers)} 层）"
                 + (f"   起点 = 段{start}" if start else ""), fontsize=13, y=.999)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3],
         int(sys.argv[4]) if len(sys.argv) > 4 else 0)
