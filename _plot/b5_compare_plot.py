"""5 分钟 1买 几种用法的对比图（探索，不判），数字和 .tmp/b5_compare.py 同一套。
用法：PYTHONPATH=. python _plot/b5_compare_plot.py OLD_WALK_DIR NEW_WALK_DIR [OUT_DIR]
  01_summary：信号数、滞后中位、48 根内跌破低点的比例、240 根后超额（每个信号一个点）。
  02_timeline：6 只票各 2000 根收盘价，下面每种用法一行，竖线 = 信号出现的那根。
  03_zoom_*：放大到新规则有信号的几天，K 线 + 每种用法一行：空心圈 = 它指的低点，实心 = 信号那根，
            连线长度 = 滞后；实心 = 48 根内低点守住，叉 = 48 根内被跌破。
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean, median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, "_plot")
sys.path.insert(0, ".tmp")
from b5_compare import SPAN, collect, stats  # noqa: E402
from fake3b import candles  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 固定顺序、固定颜色（dataviz 默认分类色 1~6，已过 validate_palette）
METHODS = [
    ("A 旧·一出现就用", "#2a78d6"),
    ("B 旧·等那笔确认", "#eb6834"),
    ("N0 新·不等确认·趋势", "#1baf7a"),
    ("N0 新·不等确认·盘整", "#eda100"),
    ("N 新·等那笔确认·趋势", "#e87ba4"),
    ("N 新·等那笔确认·盘整", "#008300"),
]
INK, MUTED, GRID = "#222222", "#6b6b6b", "#e6e6e3"


def tidy(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def summary(sig, bars, uncond, out):
    names = [m for m, _ in METHODS]
    cols = [c for _, c in METHODS]
    st = {m: stats(sig.get(m, []), bars, uncond) for m in names}
    y = list(range(len(names)))[::-1]
    f, axs = plt.subplots(1, 4, figsize=(19, 5.6), sharey=True, gridspec_kw={"width_ratios": [1, 1, 1, 1.5]})

    def bars_(ax, vals, labels, title):
        ax.barh(y, vals, color=cols, height=0.6)
        top = max(vals) if max(vals) > 0 else 1
        for yy, v, lab in zip(y, vals, labels):
            ax.text(v + top * 0.02, yy, lab, va="center", fontsize=9.5, color=INK)
        ax.set_xlim(0, top * 1.3)
        ax.set_title(title, fontsize=11, loc="left", color=INK)
        ax.xaxis.grid(True, color=GRID)
        ax.set_axisbelow(True)
        tidy(ax)

    n = [len(sig.get(m, [])) for m in names]
    bars_(axs[0], n, [str(v) for v in n], "信号数（6 只票 × 2000 根）")
    lag = [median(st[m]["lag"]) if st[m]["lag"] else 0 for m in names]
    bars_(axs[1], lag, [f"{v:.0f} 根" if st[m]["lag"] else "—" for m, v in zip(names, lag)],
          "滞后中位（信号那根 − 低点那根）")
    brk = [mean(st[m]["b48"]) * 100 if st[m]["b48"] else 0 for m in names]
    bars_(axs[2], brk, [f"{v:.0f}%（{sum(st[m]['b48'])}/{len(st[m]['b48'])}）" if st[m]["b48"] else "—"
                        for m, v in zip(names, brk)], "48 根（1 天）内低点被跌破")
    axs[2].set_xlim(0, 135)

    ax = axs[3]
    for yy, m, c in zip(y, names, cols):
        xs = [v * 100 for v in st[m]["x240"]]
        if not xs:
            ax.text(0.2, yy, "没有信号", va="center", fontsize=9.5, color=MUTED)
            continue
        jit = [(((k * 37) % 17) / 17 - 0.5) * 0.45 for k in range(len(xs))]
        ax.scatter(xs, [yy + j for j in jit], s=26 if len(xs) > 30 else 60, color=c,
                   alpha=0.35 if len(xs) > 30 else 0.9, edgecolor="white", lw=0.6, zorder=3)
        mu = mean(xs)
        ax.plot([mu, mu], [yy - 0.32, yy + 0.32], color=INK, lw=2, zorder=4)
        ax.annotate(f"均值 {mu:+.2f}%  n={len(xs)}", (0.01, yy + 0.36), xycoords=("axes fraction", "data"),
                    fontsize=9, color=INK, va="bottom")
    ax.axvline(0, color=MUTED, lw=0.9)
    ax.set_title("240 根（5 天）后超额，每点一个信号，黑竖线 = 均值", fontsize=11, loc="left", color=INK)
    ax.xaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")
    tidy(ax)

    axs[0].set_yticks(y, names, fontsize=10.5, color=INK)
    f.suptitle("5 分钟 1买 几种用法对比（探索，不判；毛收益、不扣成本、不算 T+1）", fontsize=13, x=0.01, ha="left")
    f.tight_layout()
    p = out / "b5cmp_01_summary.png"
    f.savefig(p, dpi=110)
    plt.close(f)
    print(p)


def timeline(sig, bars, out):
    codes = sorted(bars)
    f = plt.figure(figsize=(19, 4.6 * len(codes)))
    outer = f.add_gridspec(len(codes), 1, hspace=0.32)
    for r, code in enumerate(codes):
        b5, base = bars[code]
        w = b5[base:base + SPAN]
        inner = outer[r].subgridspec(2, 1, height_ratios=[1.6, 1.4], hspace=0.06)
        ax = f.add_subplot(inner[0])
        ax.plot(range(len(w)), [x.close for x in w], color=INK, lw=0.9)
        ax.set_title(f"{code}  {w[0].ts[:10]} ~ {w[-1].ts[:10]}", fontsize=10.5, loc="left", color=INK)
        ax.set_xlim(0, SPAN)
        ax.set_xticks([])
        tidy(ax)
        lane = f.add_subplot(inner[1])
        lane.set_xlim(0, SPAN)
        for k, (m, c) in enumerate(METHODS):
            ts = [t for cd, t, _, _ in sig.get(m, []) if cd == code]
            yy = len(METHODS) - 1 - k
            lane.vlines(ts, yy - 0.38, yy + 0.38, color=c, lw=1.6)
            lane.text(SPAN + 8, yy, f"{len(ts)}", va="center", fontsize=8.5, color=INK)
        lane.set_yticks(range(len(METHODS))[::-1], [m for m, _ in METHODS], fontsize=8.5)
        lane.set_ylim(-0.6, len(METHODS) - 0.4)
        ticks = list(range(0, SPAN, 250))
        lane.set_xticks(ticks, [w[t].ts[5:10] for t in ticks], fontsize=8)
        lane.yaxis.grid(True, color=GRID)
        tidy(lane)
    f.suptitle("每种用法的信号落在哪里（竖线 = 信号出现那根；右边数字 = 该票信号数）", fontsize=13, x=0.01, ha="left", y=0.9)
    p = out / "b5cmp_02_timeline.png"
    f.savefig(p, dpi=95, bbox_inches="tight")
    plt.close(f)
    print(p)


def structure(ax, b5, base, lo, hi, window=4000):
    """窗口最后一根那一刻的笔、线段、笔中枢、线段中枢，画到 t 坐标（t = 全局下标 − base）。"""
    from dataclasses import replace
    from matplotlib.patches import Rectangle

    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs

    end = base + hi
    off = max(0, end - window)
    a = analyze([replace(x, idx=k) for k, x in enumerate(b5[off:end])], check=False)
    sh = off - base
    vis = lambda i0, i1: i1 + sh >= lo and i0 + sh < hi  # noqa: E731
    for b in a.bis:
        if vis(b.start.peak_idx, b.end.peak_idx):
            ax.plot([b.start.peak_idx + sh, b.end.peak_idx + sh], [b.start.price, b.end.price], color="#7f8c9d",
                    lw=1.5, ls="-" if b.confirmed else ":", zorder=3)
    for s in a.segs:
        if vis(s.start.peak_idx, s.end.peak_idx):
            ax.plot([s.start.peak_idx + sh, s.end.peak_idx + sh], [s.start.price, s.end.price], color="#4a4a4a",
                    lw=3.5, alpha=0.8, ls="-" if s.confirmed else ":", zorder=4)
    for legs, face, edge, alpha, tag in ((legs_from_segs(a.segs, a.bis), "#e0a050", "#a8681a", 0.20, "段"),
                                          (legs_from_bis(a.bis), "#8e6cc0", "#8e6cc0", 0.13, None)):
        for z in build_zhongshus(legs):
            i0 = a.bis[legs[z.start_leg].src_start].start.peak_idx + sh
            i1 = a.bis[legs[z.core_end].src_end].end.peak_idx + sh
            if i1 < lo or i0 >= hi:
                continue
            x0, x1 = max(i0, lo), min(i1, hi - 1)
            ax.add_patch(Rectangle((x0, z.zd), x1 - x0, z.zg - z.zd, facecolor=face, alpha=alpha, edgecolor=edge,
                                   lw=2.0 if tag else 1.2, zorder=1 if tag else 2))
            if tag:
                ax.text(x1, z.zd, f"线段中枢 [{z.zd:.4g}, {z.zg:.4g}] ", fontsize=12, color=edge, va="bottom",
                        ha="right")


def zoom(sig, bars, code, lo, hi, out):
    b5, base = bars[code]
    w = b5[base:base + SPAN]
    f, (ax, lane) = plt.subplots(2, 1, figsize=(32, 16), sharex=True, gridspec_kw={"height_ratios": [2.3, 1.5]})
    candles(ax, w, lo, hi)
    structure(ax, b5, base, lo, hi)
    ax.set_title(f"{code} 放大 {w[lo].ts[5:16]} ~ {w[hi - 1].ts[5:16]}（K 线红涨绿跌）；划线取窗口最后一根那一刻（事后看）："
                 "灰细线 = 笔，深灰粗线 = 线段（点线 = 未确认），紫框 = 笔中枢，棕框 = 线段中枢",
                 fontsize=14, loc="left", color=INK)
    tidy(ax)
    for k, (m, c) in enumerate(METHODS):
        yy = len(METHODS) - 1 - k
        for cd, t, lowi, low in sig.get(m, []):
            if cd != code or not lo <= t < hi:
                continue
            i = base + t
            broke = i + 48 < len(b5) and min(x.low for x in b5[i + 1:i + 49]) < low
            lane.plot([max(lowi, lo), t], [yy, yy], color=c, lw=2)
            if lowi >= lo:
                lane.scatter([lowi], [yy], s=80, facecolor="white", edgecolor=c, lw=2, zorder=3)
                # 几种用法常认同一个低点：先画的圈大、后画的小，套成同心圈都看得见
                ax.scatter([lowi], [low], s=60 + 50 * (len(METHODS) - 1 - k), facecolor="none", edgecolor=c,
                           lw=2, zorder=4 + k)
            lane.scatter([t], [yy], s=140 if broke else 110, marker="x" if broke else "o",
                         color=c, lw=2.5, zorder=4)
    lane.set_yticks(range(len(METHODS))[::-1], [m for m, _ in METHODS], fontsize=13)
    lane.set_ylim(-0.6, len(METHODS) - 0.4)
    lane.yaxis.grid(True, color=GRID)
    lane.set_title("空心圈 = 它认的低点，实心圆 = 信号那根且 1 天内低点守住，叉 = 1 天内被跌破；连线长度 = 滞后",
                   fontsize=13, loc="left", color=INK)
    ticks = list(range(lo, hi, max(1, (hi - lo) // 18)))
    lane.set_xticks(ticks, [w[t].ts[5:16] for t in ticks], fontsize=11)
    lane.set_xlim(lo - 1, hi)
    tidy(lane)
    f.tight_layout()
    p = out / f"b5cmp_03_zoom_{code}_{w[lo].ts[5:10].replace('-', '')}.png"
    f.savefig(p, dpi=130)
    plt.close(f)
    print(p)


def main(old, new, out):
    out.mkdir(parents=True, exist_ok=True)
    sig, bars, uncond = collect(old, new)
    summary(sig, bars, uncond, out)
    timeline(sig, bars, out)
    # 新规则有信号的地方各放大一段：按票把新信号的 t 聚在一起，前留 250 根（看得到 B），后留 90 根
    new_t = {}
    for m, _ in METHODS[2:]:
        for cd, t, lowi, _ in sig.get(m, []):
            new_t.setdefault(cd, []).append((lowi, t))
    for cd, ts in new_t.items():
        ts.sort()
        grp = [[ts[0]]]
        for p in ts[1:]:
            (grp[-1].append(p) if p[0] - grp[-1][-1][1] < 150 else grp.append([p]))
        for g in grp:
            zoom(sig, bars, cd, max(0, min(x[0] for x in g) - 250), min(SPAN, max(x[1] for x in g) + 90), out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/b5cmp"))
