"""把某个中枢周围的 a A b B c 结构标出来，连同前后各 `span` 个中枢。

用法：PYTHONPATH=. python _plot/abc.py <code> <level> <k1,k2> <out.png> [span]

`span=0` 时就是最早那版（只画 A/B/B'），`span=2` 画 k-2..k+2 五个中枢——
看 b/c 方向为什么对不上，必须把前后都摊开，单看三个会以为是挑段挑错了。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

from chan.beichi import _slice_enter, _slice_leave
from chan.pipeline import analyze
from chan.zhongshu import build_zhongshus, legs_from_segs, relation
from feeds import Level, cache

FOCUS = "#fdae6b"
OTHER = "#9ecae1"


def x0(bis, leg): return bis[leg.src_start].start.peak_idx
def x1(bis, leg): return bis[leg.src_end].end.peak_idx


def draw(ax, bars, bis, legs, zss, k, span=2, pad=2):
    lo_k, hi_k = max(0, k - span), min(len(zss) - 1, k + span)
    lo_leg = max(0, zss[lo_k].start_leg - pad)
    hi_leg = min(len(legs) - 1, zss[hi_k].end_leg + pad)

    xs, ys = [], []
    for i in range(lo_leg, hi_leg + 1):
        g = legs[i]
        if not xs: xs.append(x0(bis, g)); ys.append(g.start_price)
        xs.append(x1(bis, g)); ys.append(g.end_price)
    ax.plot(xs, ys, color="#444", lw=1.0, zorder=3)
    ax.plot(xs, ys, "o", color="#444", ms=2.4, zorder=4)
    rng = max(ys) - min(ys)

    for j in range(lo_k, hi_k + 1):
        zz = zss[j]
        focus = j == k
        xa, xb = x0(bis, legs[zz.start_leg]), x1(bis, legs[zz.core_end])
        ax.add_patch(Rectangle((xa, zz.zd), xb - xa, zz.zg - zz.zd,
                               fc=FOCUS if focus else OTHER, ec="#555",
                               lw=1.2 if focus else .7,
                               alpha=.6 if focus else .40, zorder=2))
        tag = f"B(中枢{j})" if focus else f"中枢{j}"
        if j == k - 1: tag = f"A(中枢{j})"
        ax.text((xa + xb) / 2, zz.zg + rng * .012, tag, ha="center",
                va="bottom", fontsize=11 if focus else 9,
                fontweight="bold" if focus else "normal", color="#333")
        ax.text((xa + xb) / 2, zz.zd - rng * .012,
                f"{zz.zd:.2f}~{zz.zg:.2f}\n{zz.core_end - zz.start_leg + 1}段"
                + ("  升级" if zz.level_up else ""),
                ha="center", va="top", fontsize=6.5, color="#555")

    # a / b / c：a 取 A 的进入段，b 是进 B 的那段，c 是出 B 的那段
    alo, ahi = _slice_enter(zss, k - 1) if k >= 1 else (-1, -1)
    elo, ehi = _slice_enter(zss, k)
    llo, lhi = _slice_leave(zss, k, len(legs))
    for name, (slo, shi), col in (("a", (alo, ahi), "#7f7f7f"),
                                  ("b", (elo, ehi), "#1f77b4"),
                                  ("c", (llo, lhi), "#d62728")):
        if slo < 0 or shi < slo or shi >= len(legs): continue
        sx, sy = [], []
        for i in range(slo, shi + 1):
            g = legs[i]
            if not sx: sx.append(x0(bis, g)); sy.append(g.start_price)
            sx.append(x1(bis, g)); sy.append(g.end_price)
        ax.plot(sx, sy, color=col, lw=3.4, alpha=.85, zorder=5,
                solid_capstyle="round")
        d = "↑" if legs[slo].direction.name == "UP" else "↓"
        ax.text(sum(sx) / len(sx), max(sy) + rng * .04, f"{name}{d}",
                color=col, fontsize=15, fontweight="bold", ha="center",
                zorder=6)

    ax.set_xlim(min(xs) - 12, max(xs) + 12)
    ticks = [t for t in range(lo_leg, hi_leg + 1, max(1, (hi_leg - lo_leg) // 7))]
    ax.set_xticks([x0(bis, legs[t]) for t in ticks])
    ax.set_xticklabels([bars[x0(bis, legs[t])].ts[:10] for t in ticks],
                       fontsize=7, rotation=20)
    return lo_k, hi_k


def main(code, level, ks, out, span=2):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    legs = legs_from_segs(a.segs, a.bis)
    zss = build_zhongshus(legs)
    fig, axes = plt.subplots(len(ks), 1, figsize=(15, 5.0 * len(ks)))
    if len(ks) == 1: axes = [axes]
    for ax, k in zip(axes, ks):
        lo_k, hi_k = draw(ax, bars, a.bis, legs, zss, k, span)
        elo, ehi = _slice_enter(zss, k)
        llo, lhi = _slice_leave(zss, k, len(legs))
        bd = legs[elo].direction.name if 0 <= elo <= ehi else "-"
        cd = legs[llo].direction.name if llo <= lhi < len(legs) else "-"
        rel = " | ".join(f"{j}→{j+1} {relation(zss[j], zss[j+1])}"
                         for j in range(lo_k, hi_k))
        ax.set_title(
            f"{code} {level}  B=中枢{k} [{zss[k].zd:.2f},{zss[k].zg:.2f}]   "
            f"b段={elo}..{ehi}({bd})  c段={llo}..{lhi}({cd})   "
            f"{'b/c 同向 → 可比力度' if bd == cd else 'b/c 反向 → 没有可比的两推'}"
            f"\n相邻关系: {rel}", fontsize=10)
        ax.grid(alpha=.25, lw=.5)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3].split(",")],
         sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 2)
