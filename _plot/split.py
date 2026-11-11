"""把挂了 level_up 的中枢按「每 3 段一个次级别中枢」拆开画出来。

用法：PYTHONPATH=. python _plot/split.py <code> <level> <k1,k2> <out.png>

和 abc.py 的区别：abc.py 画的是**本级别**那一张平铺的中枢表，这里画的是
同一段走势在**上一级别**该怎么读——33课 p79 说 9 段以上「就可以解释成这是
一个上级别的中枢」，那就得真的把那几个子中枢摆出来，而不是只挂个标志。
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
from chan.zhongshu import build_zhongshus, legs_from_segs
from feeds import Level, cache

SUB = ["#9ecae1", "#fdae6b", "#c7e9c0", "#dadaeb", "#fdd0a2", "#d9d9d9", "#bcbddc"]


def x0(bis, g): return bis[g.src_start].start.peak_idx
def x1(bis, g): return bis[g.src_end].end.peak_idx


def draw(ax, bars, bis, legs, z, k, pad=2):
    lo = max(0, z.start_leg - pad)
    hi = min(len(legs) - 1, z.end_leg + pad)
    xs, ys = [], []
    for i in range(lo, hi + 1):
        g = legs[i]
        if not xs: xs.append(x0(bis, g)); ys.append(g.start_price)
        xs.append(x1(bis, g)); ys.append(g.end_price)
    ax.plot(xs, ys, color="#444", lw=1.0, zorder=4)
    ax.plot(xs, ys, "o", color="#444", ms=2.4, zorder=5)
    rng = max(ys) - min(ys)

    core = legs[z.start_leg:z.core_end + 1]
    subs = [core[i:i + 3] for i in range(0, len(core) - 2, 3)]
    bands = []
    for n, s in enumerate(subs):
        d, g_ = max(x.low for x in s), min(x.high for x in s)
        bands.append((d, g_))
        xa, xb = x0(bis, s[0]), x1(bis, s[-1])
        ax.add_patch(Rectangle((xa, d), xb - xa, max(g_ - d, 1e-9),
                               fc=SUB[n % len(SUB)], ec="#333", lw=.9,
                               alpha=.75, zorder=3))
        ax.text((xa + xb) / 2, g_ + rng * .015, f"子{n}", ha="center",
                va="bottom", fontsize=9, fontweight="bold", color="#222")
        ax.text((xa + xb) / 2, d - rng * .015, f"{d:.2f}\n{g_:.2f}",
                ha="center", va="top", fontsize=6, color="#444")

    # 上级别中枢：前三个子中枢的重叠（17课 p36「以前三个连续次级别的重叠为准」）
    note = "子中枢不足 3 个，够不上升级"
    if len(bands) >= 3:
        D = max(b[0] for b in bands[:3]); G = min(b[1] for b in bands[:3])
        if D <= G:
            xa = x0(bis, core[0]); xb = x1(bis, core[-1])
            ax.add_patch(Rectangle((xa, D), xb - xa, G - D, fc="none",
                                   ec="#d62728", lw=2.4, ls="--", zorder=6))
            ax.text(xb, G, f" 上级别中枢 [{D:.2f},{G:.2f}]", color="#d62728",
                    fontsize=10, fontweight="bold", va="bottom", ha="right")
            allD = max(b[0] for b in bands); allG = min(b[1] for b in bands)
            note = (f"{len(bands)} 个子中枢，前三个重叠 [{D:.2f},{G:.2f}]；"
                    + (f"全部 {len(bands)} 个仍有公共交集 [{allD:.2f},{allG:.2f}]"
                       if allD <= allG else
                       f"但全部 {len(bands)} 个无公共交集 → 上级别是**趋势**"))
        else:
            note = "前三个子中枢无重叠 → 上级别是**趋势**，不是上级别中枢"

    ax.set_title(f"{bars[0].ts[:4]}  中枢{k} 拆开看："
                 f"本级别 [{z.zd:.2f},{z.zg:.2f}] 宽 {z.zg - z.zd:.2f}，"
                 f"包络 [{z.dd:.2f},{z.gg:.2f}] 宽 {z.gg - z.dd:.2f}，"
                 f"core {len(core)} 段\n{note}", fontsize=10)
    ticks = range(lo, hi + 1, max(1, (hi - lo) // 8))
    ax.set_xticks([x0(bis, legs[t]) for t in ticks])
    ax.set_xticklabels([bars[x0(bis, legs[t])].ts[:10] for t in ticks],
                       fontsize=7, rotation=20)
    ax.grid(alpha=.25, lw=.5)


def main(code, level, ks, out):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    legs = legs_from_segs(a.segs, a.bis)
    zss = build_zhongshus(legs)
    fig, axes = plt.subplots(len(ks), 1, figsize=(15, 4.6 * len(ks)))
    if len(ks) == 1: axes = [axes]
    for ax, k in zip(axes, ks):
        draw(ax, bars, a.bis, legs, zss[k], k)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3].split(",")],
         sys.argv[4])
