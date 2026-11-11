"""一个 20 个月的「下跌趋势」标签，操作上等于没说。子结构往哪挂。

用法：PYTHONPATH=. python _plot/substructure.py <code> <level> <out.png> [start]

上图  **现状（C-order）**。过度延伸的中枢只要并进了趋势就保持完整（20课 p43
      中心定理二是**等价**，33课 p79 的延伸上限自称是**消歧约定**，定理压约定）。
      好处：方向对，`a A b B c` 看得见。代价：600009 的走势3 横跨 2024-12 到
      2026-09，一个标签管 22 个月，中间没有任何可下手的地方。

下图  **提议**。走势类型一根不动，另外把够格拆的中枢的三等分子中枢作为**子结构**
      画在它下面一行。两种读法同时在场，这正是 33课 p79 说的走势多义性——不是
      二选一，是同一段走势在两个级别上各有一个合法答案。

灰影 = 这一段走势真实走过的振幅（71课 p190：端点未必是极值）。
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
from chan.tree import NO_ZHONGSHU, build_tree
from chan.zhongshu import PANZHENG, TREND_DOWN, TREND_UP, legs_from_segs
from feeds import Level, cache

UP, DOWN = "#d62728", "#2ca02c"
KIND = {TREND_UP: UP, TREND_DOWN: DOWN, PANZHENG: "#7f7f7f",
        NO_ZHONGSHU: "#9b1fb0"}


def draw_legs(ax, legs, bars, x0, x1):
    ys = []
    for i, g in enumerate(legs):
        lo, hi = min(g.start_price, g.end_price), max(g.start_price, g.end_price)
        if g.low < lo - 1e-9 or g.high > hi + 1e-9:
            ax.add_patch(Rectangle((x0(i), g.low), x1(i) - x0(i), g.high - g.low,
                                   fc="#999", ec="none", alpha=.13, zorder=1))
        ax.plot([x0(i), x1(i)], [g.start_price, g.end_price],
                color=UP if g.end_price >= g.start_price else DOWN,
                lw=1.5, zorder=4, solid_capstyle="round")
        ys += [g.start_price, g.end_price, g.low, g.high]
    return ys


def band(ax, xa, xb, y, h, color, text, rng, fs=7.5):
    ax.add_patch(Rectangle((xa, y), xb - xa, h, fc=color, ec="white",
                           lw=.8, alpha=.6, zorder=3))
    ax.text((xa + xb) / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color="#111", linespacing=1.15, zorder=5)


def main(code, level, out, start=0):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    L = build_tree(legs_from_segs(a.segs, a.bis), start=start)[0]
    legs = L.legs
    x0 = lambda i: a.bis[legs[i].src_start].start.peak_idx
    x1 = lambda i: a.bis[legs[i].src_end].end.peak_idx
    day = lambda i, e: bars[x1(i) if e else x0(i)].ts[:10]

    fig, axes = plt.subplots(2, 1, figsize=(16, 11), sharex=True, sharey=True)

    # ---------------- 上：现状 ----------------
    ax = axes[0]
    ys = draw_legs(ax, legs, bars, x0, x1)
    rng = max(ys) - min(ys)
    base = min(ys) - rng * .10
    worst = max(L.trends, key=lambda t: x1(t.end_leg) - x0(t.start_leg))
    for t in L.trends:
        band(ax, x0(t.start_leg), x1(t.end_leg), base, rng * .05,
             KIND[t.kind], f"走势{t.index} {t.kind}\n"
             f"{t.start_price:.2f}→{t.end_price:.2f}", rng)
        ax.text((x0(t.start_leg) + x1(t.end_leg)) / 2, base - rng * .018,
                f"{day(t.start_leg, False)}\n~ {day(t.end_leg, True)}",
                ha="center", va="top", fontsize=6.5, color="#666", linespacing=1.2)
    months = (x1(worst.end_leg) - x0(worst.start_leg)) / len(bars) * (
        (int(bars[-1].ts[:4]) * 12 + int(bars[-1].ts[5:7]))
        - (int(bars[0].ts[:4]) * 12 + int(bars[0].ts[5:7])))
    ax.set_title(f"现状（C-order）　{len(L.trends)} 个走势类型。"
                 f"最长的 走势{worst.index} {worst.kind} 占 段{worst.start_leg}.."
                 f"{worst.end_leg}，{day(worst.start_leg, False)} ~ "
                 f"{day(worst.end_leg, True)}（约 {months:.0f} 个月）"
                 f"——方向是对的，可是一个标签管这么久，中间无处下手",
                 fontsize=11.5)

    # ---------------- 下：提议 ----------------
    ax = axes[1]
    draw_legs(ax, legs, bars, x0, x1)
    n_sub = 0
    for t in L.trends:
        band(ax, x0(t.start_leg), x1(t.end_leg), base, rng * .05,
             KIND[t.kind], f"走势{t.index} {t.kind}", rng)
        # 子结构行（`Trend.substructure`，C-sub）：本走势类型里够格三等分的
        # 中枢，各自摊成三个本级别中枢。走势类型本身一根没动。
        for k in t.substructure:
            n_sub += 1
            xa, xb = x0(k.start_leg), x1(k.end_leg)
            band(ax, xa, xb, base - rng * .075, rng * .042, "#fdae6b",
                 f"子枢\n{k.zd:.1f}~{k.zg:.1f}", rng, fs=6.5)
            ax.add_patch(Rectangle((xa, k.zd), xb - xa, max(k.zg - k.zd, 1e-9),
                                   fc="#fdae6b", ec="#a6621f", lw=1.0,
                                   alpha=.45, zorder=2))
            ax.text((xa + xb) / 2, k.zg + rng * .01,
                    f"{k.end_leg - k.start_leg + 1}段", ha="center",
                    va="bottom", fontsize=6.5, color="#a6621f")
    ax.text(x0(0), base - rng * .075 + rng * .021, "子结构 ",
            ha="right", va="center", fontsize=8, color="#a6621f", fontweight="bold")
    ax.set_title(f"提议　走势类型一根不动（方向保住），另挂一行**子结构**："
                 f"够格三等分的中枢摊成 {n_sub} 个本级别中枢。"
                 f"33课 p79 的走势多义性不是二选一——两个级别各有一个合法答案，"
                 f"都摆出来", fontsize=11.5)

    ax.set_ylim(base - rng * .14, max(ys) + rng * .07)
    ticks = range(0, len(legs), max(1, len(legs) // 11))
    ax.set_xticks([x0(t) for t in ticks])
    ax.set_xticklabels([bars[x0(t)].ts[:10] for t in ticks], fontsize=8, rotation=18)
    for a_ in axes:
        a_.grid(alpha=.22, lw=.5)
    fig.suptitle(f"{code} {level}　20 个月的「下跌趋势」怎么变成能操作的东西"
                 + (f"（起点 段{start}）" if start else ""), fontsize=14, y=.985)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(f"{out}  走势 {len(L.trends)} 个，子结构 {n_sub} 个", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3],
         int(sys.argv[4]) if len(sys.argv) > 4 else 0)
