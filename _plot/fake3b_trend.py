"""假3买 的走势结构：买入那一刻，30分钟上前面是不是 aAbBc、3买 是不是落在新走势的第一个中枢上。

用法：PYTHONPATH=. python _plot/fake3b_trend.py CODE T [OUT]
  T 是第二版判出那一步（30分钟最近4000根里的下标）。左：30分钟K线 + 笔/段/段中枢，
  按中心定理二标出前后中枢关系和 a/A/b/B/c；右：示意图——「aAbBc 走完，再 aAb，
  b 之后回拉不进 A」才是第一个中枢的 3买。
"""
from __future__ import annotations

import sys
from dataclasses import replace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, "_plot")
from fake3b import BI, BUY, DOWN, INK, SEGZS, UP, candles  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

REL_CN = {"up": "上涨", "down": "下跌", "higher_level": "高级别中枢", "ambiguous": "不定"}


def seg_mid(b, s):
    return (s.start.peak_idx + s.end.peak_idx) / 2, (s.start.price + s.end.price) / 2


def main(code: str, t: int, out: str):
    from chan.num import eq
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_segs, relation
    from feeds import Level, cache

    b = cache.load(code, Level.M30)
    b = [replace(x, idx=i) for i, x in enumerate(b[-4000:])]
    st = None
    from backtest.sublevel import _read_steps
    st = _read_steps(f".tmp/now3b2_new/{code}.steps.jsonl")[t]["cand"]
    zg0 = st[2]

    a = analyze(b[: t + 1], check=True)
    legs = legs_from_segs(a.segs, a.bis)
    zss = build_zhongshus(legs)
    k = next(i for i, z in enumerate(zss) if eq(z.zg, zg0))
    cur, prev = zss[k], zss[k - 1] if k > 0 else None
    pp = zss[k - 2] if k > 1 else None

    def zbars(z):
        return (a.bis[legs[z.start_leg].src_start].start.peak_idx,
                a.bis[legs[z.core_end].src_end].end.peak_idx)

    first = pp if pp is not None else prev
    lo = max(0, zbars(first)[0] - 40)
    fin = analyze(b, check=False)
    hi = min(len(b), t + 120)

    fig = plt.figure(figsize=(21, 9.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[3.1, 1], wspace=0.06)
    ax, sx = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])

    candles(ax, b, lo, hi)
    for x in a.bis:
        if x.start.peak_idx >= lo:
            ax.plot([x.start.peak_idx, x.end.peak_idx], [x.start.price, x.end.price],
                    color=BI, lw=0.8, zorder=2)
    for i, s in enumerate(a.segs):
        if s.start.peak_idx < lo:
            continue
        ax.plot([s.start.peak_idx, s.end.peak_idx], [s.start.price, s.end.price],
                color=UP if s.direction.name == "UP" else DOWN, lw=2.6,
                ls="-" if s.confirmed else ":", zorder=4)
        mx, my = seg_mid(b, s)
        ax.text(mx, my, f"S{i}", fontsize=9, color=INK, ha="center", va="center", zorder=6,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.8))
    # 买入之后：终版的段（虚线）
    for s in fin.segs:
        if s.end.peak_idx > t and s.start.peak_idx < hi:
            x0 = max(s.start.peak_idx, t)
            ax.plot([x0, s.end.peak_idx], [s.start.price if x0 == s.start.peak_idx else
                                           b[t].close, s.end.price],
                    color=UP if s.direction.name == "UP" else DOWN, lw=1.6, ls="--", alpha=0.55)

    for z in zss[max(0, k - 2): k + 1]:
        i0, i1 = zbars(z)
        if i1 < lo:
            continue
        ax.add_patch(Rectangle((max(i0, lo), z.zd), i1 - max(i0, lo), z.zg - z.zd,
                               facecolor=SEGZS, alpha=0.2, edgecolor=SEGZS, lw=1.4, zorder=1))
        ax.hlines([z.gg, z.dd], max(i0, lo), i1, colors=SEGZS, lw=0.8, linestyles=":",
                  zorder=1)
        segs = "".join("上" if legs[j].direction.name == "UP" else "下"
                       for j in range(z.start_leg, z.end_leg + 1))
        ax.text(max(i0, lo) + 1, z.gg, f"Z{z.index}  S{z.start_leg}~S{z.end_leg}（{segs}）\n"
                f"ZD {z.zd:.2f} ZG {z.zg:.2f}\nDD {z.dd:.2f} GG {z.gg:.2f}", fontsize=8.5,
                color="#a8681a", va="bottom")

    # 前后中枢关系（中心定理二）
    notes = []
    for p, q in ((pp, prev), (prev, cur)):
        if p is not None and q is not None:
            r = relation(p, q)
            how = (f"Z{q.index} GG {q.gg:.2f} < Z{p.index} DD {p.dd:.2f}" if r == "down" else
                   f"Z{q.index} DD {q.dd:.2f} > Z{p.index} GG {p.gg:.2f}" if r == "up" else
                   "区间有重叠")
            notes.append(f"Z{p.index} → Z{q.index}：{REL_CN[r]}（{how}）")

    # a/A/b/B/c：末中枢与前一个是下跌关系时，按下跌趋势读
    r_last = relation(prev, cur) if prev is not None else None
    leave = a.segs[cur.end_leg]
    third = cur.end_leg == cur.start_leg + 2
    if r_last == "down":
        i0, _ = zbars(prev)
        ax.text(i0, prev.zg + (prev.gg - prev.zg) * 0.3, "A", fontsize=22, color="#a8681a",
                fontweight="bold")
        i0, _ = zbars(cur)
        ax.text(i0, cur.zd - (cur.zg - cur.zd) * 0.35, "B", fontsize=22, color="#a8681a",
                fontweight="bold", va="top")
        for j in range(prev.end_leg + 1, cur.start_leg):
            mx, my = seg_mid(b, a.segs[j])
            ax.text(mx + 8, my, "b", fontsize=20, color=DOWN, fontweight="bold")
    mx, my = seg_mid(b, leave)
    ax.annotate(f"S{cur.end_leg}：B 的第 3 段，向上穿出 ZG {cur.zg:.2f}\n"
                + ("引擎把它同时当成 B 的构成段和「离开段」" if third else "离开段"),
                (mx, my), xytext=(-330, 150), textcoords="offset points", fontsize=9.5,
                color=UP, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=UP, lw=0.7),
                arrowprops=dict(arrowstyle="->", color=UP, lw=0.8))
    tail = a.segs[-1]
    ax.annotate(f"S{len(a.segs) - 1}（未确认）向下回拉，低点 {st[3]:.2f}\n只比 ZG 高 "
                f"{st[3] - cur.zg:.2f} → 被当成 3买\n"
                "按「下跌中枢是上下上」读，这根向下段\n也可能就是 B 之后的 c 段",
                (tail.end.peak_idx, tail.end.price), xytext=(30, -150),
                textcoords="offset points", fontsize=9.5, color=INK,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BUY, lw=0.7),
                arrowprops=dict(arrowstyle="->", color=BUY, lw=0.8))
    ax.scatter([st[4]], [st[3]], marker="*", s=260, color=BUY, zorder=8, edgecolor="white")
    ax.scatter([t], [b[t].close], marker="^", s=170, color=BUY, zorder=8)
    ax.axvline(t + 0.5, color=INK, lw=0.7, ls=":")
    ax.text(t + 2, ax.get_ylim()[0], " 买入之后（虚线 = 终版的段）", fontsize=9, va="bottom")
    ax.axhline(cur.zg, color=SEGZS, lw=1.4)

    c_note = ("c 段（B 之后向下离开 B 的段）：买入时**不存在**。\n"
              f"S{cur.start_leg + 1} 虽然跌破了 ZD，但它是 B 的构成段。")
    ax.text(0.01, 0.02, "\n".join(notes) + "\n" + c_note.replace("**", ""),
            transform=ax.transAxes, fontsize=10, va="bottom",
            bbox=dict(boxstyle="round,pad=0.4", fc="#fffdf5", ec=SEGZS, lw=0.8))
    ticks = list(range(lo, hi, max(1, (hi - lo) // 12)))
    ax.set_xticks(ticks, [b[i].ts[2:10] for i in ticks])
    ax.set_xlim(lo - 1, hi)
    ax.set_ylabel("30分钟（后复权）")
    ax.set_title(f"{code} 买入 {b[t].ts[:16]} 时的 30 分钟走势结构："
                 f"{'A →(下跌)→ B，B 后面没有 c，3买 落在 B（下跌趋势的最后一个中枢）上' if r_last == 'down' else ''}",
                 fontsize=11.5, loc="left")
    ax.spines[["top", "right"]].set_visible(False)

    # 右：示意图
    xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    ys = [10, 7, 8.2, 6.8, 7.8, 4, 5.2, 3.8, 4.8, 1.0, 3.0, 2.0, 3.2, 2.3, 5.6, 4.0]
    sx.plot(xs, ys, color=INK, lw=1.4)
    sx.add_patch(Rectangle((1, 7.0), 3, 1.0, fc=SEGZS, alpha=0.3, ec=SEGZS))
    sx.add_patch(Rectangle((5, 4.0), 3, 1.0, fc=SEGZS, alpha=0.3, ec=SEGZS))
    sx.add_patch(Rectangle((10, 2.3), 3, 0.7, fc="#8e6cc0", alpha=0.3, ec="#8e6cc0"))
    for x, y, s, c in ((0.3, 8.8, "a", DOWN), (2.3, 8.25, "A", "#a8681a"), (4.6, 6.0, "b", DOWN),
                       (6.3, 5.25, "B", "#a8681a"), (8.6, 2.9, "c", DOWN),
                       (9.2, 0.55, "1买", BUY), (11.2, 3.3, "A'", "#8e6cc0"),
                       (13.3, 4.4, "b'", UP)):
        sx.text(x, y, s, fontsize=15 if len(s) < 3 else 11, color=c, fontweight="bold")
    sx.scatter([15], [4.0], marker="*", s=260, color=BUY, zorder=5)
    sx.annotate("回拉不进 A'\n= 第一个中枢的 3买", (15, 4.0), xytext=(12, -110),
                textcoords="offset points", fontsize=10, color=BUY, ha="right",
                arrowprops=dict(arrowstyle="->", color=BUY))
    sx.text(0, 10.6, "示意：aAbBc 走完（c 末端 1买），\n新的向上走势 a' A' b'，"
            "b' 之后回拉不进 A'\nA' 是向上中枢（下上下），是新走势的第一个中枢",
            fontsize=10, va="bottom")
    sx.set_ylim(0, 12.6)
    sx.axis("off")

    fig.savefig(out, dpi=115, bbox_inches="tight")
    print("写出", out)


if __name__ == "__main__":
    code, t = sys.argv[1], int(sys.argv[2])
    main(code, t, sys.argv[3] if len(sys.argv) > 3 else f"_plot/fake3b/trend_{code}.png")
