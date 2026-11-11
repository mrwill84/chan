"""把一处薄边判据的**两种读法**并排画出来。

用法：PYTHONPATH=. python _plot/readings.py <code> <level> <k> <out.png> [start]

`relation` 现在只返回一个字符串，调用方看不出它是稳稳判出来的还是差两毛钱
判出来的。这张图就是把那两毛钱画成可见的宽度：

  上图 读法A —— 严格按 20课 p43 中心定理二。后GG >= 前DD 判 higher_level，
       而那个「重叠」本身是一条缝；右上角的放大窗专门给这条缝。
  下图 读法B —— 把这条缝当噪声抹掉。后GG < 前DD，两个中枢依次向下不重叠，
       17课 p36 的 aAbBc 下跌趋势成立。

两张图用的是**同一串段、同一个中枢划分**，差别只在那条缝算不算重叠。这就是
走势多义性在这个位置上的全部内容——不是起点选错了，是判据余量太薄。
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
BOX_P, BOX_C = "#9ecae1", "#fdae6b"


def polyline(ax, legs, bis, lo, hi, x0, x1, lw=1.8):
    ys = []
    for i in range(lo, hi + 1):
        g = legs[i]
        ax.plot([x0(i), x1(i)], [g.start_price, g.end_price],
                color=UP if g.end_price >= g.start_price else DOWN, lw=lw, zorder=4)
        ys += [g.start_price, g.end_price]
    return ys


def boxes(ax, p, c, x0, x1, rng, label=True):
    """每个中枢画两层：实心的中枢区间 [ZD,ZG]，外面套虚线的包络盒 [DD,GG]。

    中心定理二比的是**包络盒**（18课 p39：「包括任何围绕走势中枢产生的任何
    瞬间波动」），不是中枢区间，更不是某一根段。早先版本把前DD/后GG画成横贯
    全图的两条线，看上去像在比段——那是画错了，两条线各自只属于一个中枢盒。
    """
    for z, col, name in ((p, BOX_P, "A"), (c, BOX_C, "B")):
        a0, a1 = x0(z.start_leg), x1(z.core_end)
        ax.add_patch(Rectangle((a0, z.dd), a1 - a0, max(z.gg - z.dd, 1e-9),
                               fc=col, ec=col, lw=1.6, ls="--", alpha=.18, zorder=2))
        ax.add_patch(Rectangle((a0, z.zd), a1 - a0, max(z.zg - z.zd, 1e-9),
                               fc=col, ec="#444", lw=1.1, alpha=.7, zorder=3))
        if label:
            ax.text((a0 + a1) / 2, (z.zd + z.zg) / 2, name, ha="center", va="center",
                    fontsize=16, fontweight="bold", color="#222", zorder=6)
            ax.text((a0 + a1) / 2, z.gg + rng * .02,
                    f"中枢{z.index}  区间 {z.zd:.2f}~{z.zg:.2f}\n"
                    f"包络 DD {z.dd:.2f} ~ GG {z.gg:.2f}", ha="center", va="bottom",
                    fontsize=8.5, color="#333", linespacing=1.3)


def main(code, level, k, out, start=0):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    bis = a.bis
    L = build_tree(legs_from_segs(a.segs, bis), start=start)[0]
    legs, (p, c) = L.legs, (L.zhongshus[k], L.zhongshus[k + 1])

    x0 = lambda i: bis[legs[i].src_start].start.peak_idx
    x1 = lambda i: bis[legs[i].src_end].end.peak_idx
    # 多画一段进入段 a 和一段离开段 c，aAbBc 才看得全
    lo = max(0, p.start_leg - 1)
    hi = min(len(legs) - 1, c.core_end + 1)

    # 中心定理二判 higher_level 时所说的「重叠」= 两个**包络盒** [DD,GG] 的交
    band_lo, band_hi = max(p.dd, c.dd), min(p.gg, c.gg)
    width = band_hi - band_lo
    mid = (band_lo + band_hi) / 2

    # 两盒根本不相交时这张图没有意义：没有「那条缝」，中心定理二一步就判出趋势，
    # 不存在第二种读法。硬画会得到一个高度为负的矩形，看上去还挺像回事——所以
    # 这里直接拒绝出图。600009 中枢0→1 补上 C-enter 之后就落到了这一支。
    if width <= 0:
        raise SystemExit(
            f"{code} {level} 中枢{k}→{k+1}：两个包络盒不相交"
            f"（前 [{p.dd:.2f},{p.gg:.2f}] / 后 [{c.dd:.2f},{c.gg:.2f}]，"
            f"间隙 {-width:.2f} 元），relation={relation(p, c)} 是唯一读法，"
            f"没有并列读法可画。"
        )

    # DD/GG 各由哪一根 Zn 贡献——20课 p43 只数与中枢方向一致的段
    zn = lambda z: [i for i in range(z.start_leg, z.core_end + 1)
                    if legs[i].direction is z.direction]
    dd_leg = min(zn(p), key=lambda i: legs[i].low)
    gg_leg = max(zn(c), key=lambda i: legs[i].high)

    fig, axes = plt.subplots(2, 1, figsize=(15, 11))
    for ax in axes:
        ys = polyline(ax, legs, bis, lo, hi, x0, x1)
        rng = max(ys) - min(ys)
        boxes(ax, p, c, x0, x1, rng)
        ax.set_ylim(min(ys) - rng * .05, max(ys) + rng * .08)
        ticks = range(lo, hi + 1, max(1, (hi - lo) // 9))
        ax.set_xticks([x0(t) for t in ticks])
        ax.set_xticklabels([bars[x0(t)].ts[:10] for t in ticks], fontsize=8, rotation=18)
        ax.grid(alpha=.22, lw=.5)
    xa, xb = x0(lo), x1(hi)

    # ---- 上：严格读法。重叠是条缝，但它是重叠 ----
    ax = axes[0]
    # 重叠带只横跨这两个中枢盒，不横贯全图——它是盒与盒的交，不是一条行情价位
    ba, bb = x0(p.start_leg), x1(c.core_end)
    ax.add_patch(Rectangle((ba, band_lo), bb - ba, max(width, 1e-9),
                           fc="#d62728", ec="#d62728", lw=1.4, alpha=.40, zorder=5))
    ax.annotate(f"两个**包络盒**的交 [{band_lo:.2f}, {band_hi:.2f}]\n"
                f"宽 {width:.2f} 元（股价的 {width / mid * 100:.2f}%）\n"
                f"中心定理二据此判 {relation(p, c)}".replace("**", ""),
                xy=(ba + (bb - ba) * .22, mid), xytext=(xa + (xb - xa) * .16, mid - 11),
                fontsize=11, fontweight="bold", color="#d62728",
                arrowprops=dict(arrowstyle="->", color="#d62728", lw=1.8))
    ax.set_title(f"读法 A　严格按 20课 p43 中心定理二：后GG {c.gg:.2f} ≥ 前DD {p.dd:.2f}"
                 f" → 高级别走势中枢 → 走势类型 = 盘整", fontsize=12, color="#333")

    # 放大窗：那条缝本体
    ins = ax.inset_axes([.60, .52, .37, .40])
    polyline(ins, legs, bis, lo, hi, x0, x1, lw=1.2)
    ins.axhspan(band_lo, band_hi, color="#d62728", alpha=.30)
    # 两条边各自只画在自己那个中枢盒的跨度上
    ins.hlines(p.dd, x0(p.start_leg), x1(p.core_end), color="#1f77b4", lw=1.8)
    ins.hlines(c.gg, x0(c.start_leg), x1(c.core_end), color="#ff7f0e", lw=1.8)
    ins.text(x1(p.core_end), p.dd, f"前中枢DD {p.dd:.2f}（段{dd_leg}的低点） ",
             va="top", ha="right", fontsize=8, color="#1f77b4")
    ins.text(x1(c.core_end), c.gg, f"后中枢GG {c.gg:.2f}（段{gg_leg}的高点） ",
             va="bottom", ha="right", fontsize=8, color="#ff7f0e")
    ins.set_xlim(xa, xb)
    ins.set_ylim(mid - width * 6, mid + width * 6)
    ins.set_xticks([])
    ins.tick_params(labelsize=7)
    ins.set_title(f"放大 ×{(max(ys) - min(ys)) / (width * 12):.0f}：这条缝就是全部分歧",
                  fontsize=9, color="#d62728")

    # ---- 下：薄边翻面。缝抹掉就是 aAbBc ----
    ax = axes[1]
    for i, tag in ((p.start_leg - 1, "a"), (p.depart_leg, "b"), (c.core_end + 1, "c")):
        if i is None or not lo <= i <= hi:
            continue
        g = legs[i]
        ax.text((x0(i) + x1(i)) / 2, (g.start_price + g.end_price) / 2,
                tag, fontsize=17, fontweight="bold", color="#7b3294",
                ha="center", va="center", zorder=7,
                bbox=dict(boxstyle="circle,pad=.22", fc="white", ec="#7b3294", lw=1.4))
    # 说明文字放在右上角空白处，别压到 b 段和折线上
    ax.annotate("", xy=(.50, .60), xytext=(.50, .88), xycoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color=DOWN, lw=3.4, alpha=.85))
    ax.text(.53, .74, f"两个中枢依次向下、不重叠\n"
                      f"A 在 [{p.zd:.2f},{p.zg:.2f}]，B 在 [{c.zd:.2f},{c.zg:.2f}]\n"
                      f"中间隔着连接段 b —— 这就是下跌趋势",
            transform=ax.transAxes, fontsize=11.5, fontweight="bold",
            color=DOWN, va="center")
    ax.set_title(f"读法 B　把这 {width:.2f} 元当噪声抹掉：后GG < 前DD"
                 f" → 下跌趋势（17课 p36 的 a+A+b+B+c）", fontsize=12, color="#333")

    fig.suptitle(f"{code} {level}　中枢{k} → 中枢{k+1} 的两种读法"
                 + (f"（起点 段{start}）" if start else "")
                 + f"　— 同一串段、同一个划分，只差 {width:.2f} 元",
                 fontsize=14, y=.995)
    fig.tight_layout()
    fig.savefig(out, dpi=112)
    print(f"{out}  缝 [{band_lo:.2f},{band_hi:.2f}] 宽 {width:.2f} "
          f"({width / mid * 100:.2f}%)  relation={relation(p, c)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4],
         int(sys.argv[5]) if len(sys.argv) > 5 else 0)
