"""把划线结果画出来，用眼睛核对。

这是验证策略的第四层。前三层（不变量断言、手工 fixture、随机压力测试）都只能
证明「没有违反我写下来的规则」，证明不了「我写下来的规则就是缠论」。缠论这东西
最终还是要看一眼图才放心——本项目至今最严重的两个 bug（C-win、C-engulf）就是
拿 600519 真数据跑出来、从「11 条线段吞掉 616 笔」这个一眼就不对的数字查出来的。

用法::

    python -m tools.plot_chan 600519 --start 2003-01-01 --end 2005-06-30
    python -m tools.plot_chan 600519 --level 30min --zs bi --out /tmp/a.png

**重要：划线永远在完整历史上做，`--start/--end` 只裁剪视野。** 裁数据会改变
划线结果（线段的确认依赖后续笔），那样看到的就不是真实的图了。
"""

from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from chan import analyze, macd
from chan.buysell import find_bspoints
from chan.types import Direction
from chan.zhongshu import build_zhongshus, classify, legs_from_bis, legs_from_segs
from feeds import Level, cache
from feeds.akshare_feed import AkshareFeed

# A股习惯：涨红跌绿。Windows 上中文字体用雅黑，缺了就退回默认（图还是能看）。
UP_C, DOWN_C = "#d62728", "#2ca02c"
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _candles(ax, bars) -> None:
    for b in bars:
        c = UP_C if b.close >= b.open else DOWN_C
        ax.vlines(b.idx, b.low, b.high, color=c, linewidth=0.5, zorder=1)
        lo, hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(
            Rectangle(
                (b.idx - 0.3, lo),
                0.6,
                max(hi - lo, 1e-9),
                facecolor=c,
                edgecolor=c,
                linewidth=0.3,
                zorder=2,
            )
        )


def _polyline(ax, pts, *, color, lw, label, zorder, dashed_tail=False):
    """pts 是 [(x, y), ...]。``dashed_tail`` 把最后一节画成虚线表示未确认。"""
    if len(pts) < 2:
        return
    solid = pts[:-1] if dashed_tail else pts
    ax.plot(
        [p[0] for p in solid],
        [p[1] for p in solid],
        color=color,
        linewidth=lw,
        label=label,
        zorder=zorder,
        solid_capstyle="round",
    )
    if dashed_tail:
        ax.plot(
            [pts[-2][0], pts[-1][0]],
            [pts[-2][1], pts[-1][1]],
            color=color,
            linewidth=lw,
            linestyle="--",
            zorder=zorder,
        )


def _draw_zhongshus(ax, zss, legs, bis) -> None:
    """中枢画两个框：实线 [ZD, ZG] 是中枢本体，虚线 [DD, GG] 是震荡包络。

    `Leg.src_start/src_end` 不论来自笔还是线段，存的都是**笔下标**。
    """
    for z in zss:
        a, b = legs[z.start_leg], legs[z.end_leg]
        x0 = bis[a.src_start].start.peak_idx
        x1 = bis[b.src_end].end.peak_idx
        w = max(x1 - x0, 1)
        ax.add_patch(
            Rectangle(
                (x0, z.zd),
                w,
                z.zg - z.zd,
                facecolor="#1f77b4",
                alpha=0.16,
                edgecolor="#1f77b4",
                linewidth=1.2,
                linestyle="-" if z.confirmed else ":",
                zorder=3,
            )
        )
        ax.add_patch(
            Rectangle(
                (x0, z.dd),
                w,
                z.gg - z.dd,
                facecolor="none",
                edgecolor="#1f77b4",
                linewidth=0.6,
                linestyle="--",
                alpha=0.5,
                zorder=3,
            )
        )
        tag = f"ZS{z.index}" + ("↑" if z.level_up else "")
        ax.text(x0, z.zg, tag, fontsize=7, color="#1f77b4", va="bottom", zorder=6)


def _draw_bspoints(ax, pts, span: float) -> None:
    """买卖点。买点画在K线下方、卖点在上方，标签就是「1买」「3卖」。

    四种状态要一眼能分开，否则图上核对不了：

    - **类**买卖点（盘整背驰，C-quasi）→ 空心，标签前加 `~`
    - **歧义**三买（C15）→ 标签后加 `?`
    - **未确认**（落在未完成笔上，C-prov）→ 半透明
    - 买红卖绿，和K线同色系
    """
    off = span * 0.025
    for p in pts:
        buy = p.is_buy
        y = (p.price - off) if buy else (p.price + off)
        color = UP_C if buy else DOWN_C
        ax.scatter(
            p.bar,
            y,
            marker="^" if buy else "v",
            s=46,
            facecolor="none" if p.quasi else color,
            edgecolor=color,
            linewidth=1.0,
            alpha=1.0 if p.confirmed else 0.45,
            zorder=7,
        )
        ax.text(
            p.bar,
            y - off * 0.6 if buy else y + off * 0.6,
            ("~" if p.quasi else "") + p.label + ("?" if p.ambiguous else ""),
            fontsize=7,
            color=color,
            ha="center",
            va="top" if buy else "bottom",
            alpha=1.0 if p.confirmed else 0.5,
            zorder=7,
        )


def plot(code: str, level: Level, *, zs_from: str, start, end, out: str, bs: bool = True) -> str:
    bars = cache.get_or_fetch(AkshareFeed(), code, level)
    if not bars:
        raise SystemExit(f"{code} {level.value} 没有数据")
    a = analyze(bars, check=True)
    bis, segs = a.bis, a.segs

    legs = legs_from_segs(segs, bis) if zs_from == "seg" else legs_from_bis(bis)
    zss = build_zhongshus(legs)

    fig, ax = plt.subplots(figsize=(22, 10))
    _candles(ax, bars)

    _polyline(
        ax,
        [(bis[0].start.peak_idx, bis[0].start_price)]
        + [(b.end.peak_idx, b.end_price) for b in bis],
        color="#444444",
        lw=0.9,
        label=f"笔 {len(bis)}",
        zorder=4,
        dashed_tail=not bis[-1].confirmed,
    )
    _polyline(
        ax,
        [(bis[segs[0].start_bi].start.peak_idx, bis[segs[0].start_bi].start_price)]
        + [(bis[s.end_bi].end.peak_idx, bis[s.end_bi].end_price) for s in segs],
        color="#ff7f0e",
        lw=2.2,
        label=f"线段 {len(segs)}（已确认 {sum(s.confirmed for s in segs)}）",
        zorder=5,
        dashed_tail=not segs[-1].confirmed,
    )
    _draw_zhongshus(ax, zss, legs, bis)

    # 买卖点画在中枢之后，免得被框子盖住。MACD 在**原始K线**上算（C-hist）
    pts = find_bspoints(legs, zss, bis, macd([b.close for b in bars])) if bs else []

    # x 轴用原始K线下标（不用日期，免得停牌/周末留出假空白），刻度贴日期
    n = len(bars)
    lo = next((b.idx for b in bars if b.ts >= start), 0) if start else 0
    hi = next((b.idx for b in reversed(bars) if b.ts <= end), n - 1) if end else n - 1
    pad = max((hi - lo) * 0.01, 1)
    ax.set_xlim(lo - pad, hi + pad)
    view = bars[lo : hi + 1]
    ys = [b.low for b in view] + [b.high for b in view]
    span = max(ys) - min(ys)
    ax.set_ylim(min(ys) - span * 0.06, max(ys) + span * 0.06)
    _draw_bspoints(ax, [p for p in pts if lo <= p.bar <= hi], span)
    ticks = [view[i].idx for i in range(0, len(view), max(len(view) // 14, 1))]
    ax.set_xticks(ticks)
    ax.set_xticklabels([bars[t].ts[:10] for t in ticks], rotation=30, ha="right", fontsize=8)

    name = {"seg": "线段级", "bi": "笔级"}[zs_from]
    ax.set_title(
        f"{code} {level.value}  {view[0].ts[:10]} ~ {view[-1].ts[:10]}  "
        f"（划线基于全部 {n} 根K线）\n"
        f"{name}中枢 {len(zss)} 个，走势 = {classify(zss)}"
        + (f"，买卖点 {len(pts)} 个（视野内 {sum(lo <= p.bar <= hi for p in pts)}）" if bs else ""),
        fontsize=12,
    )
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.2, linewidth=0.4)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="缠论划线可视化")
    p.add_argument("code")
    p.add_argument("--level", default="day", choices=[x.value for x in Level])
    p.add_argument("--zs", default="seg", choices=["seg", "bi"], help="中枢建在哪一层")
    p.add_argument("--start", help="视野起点 YYYY-MM-DD（不影响划线）")
    p.add_argument("--end", help="视野终点 YYYY-MM-DD（不影响划线）")
    p.add_argument("--out")
    p.add_argument("--no-bs", action="store_true", help="不画买卖点，只看划线")
    args = p.parse_args()
    out = args.out or f"{args.code}_{args.level}_{args.zs}.png"
    print(plot(args.code, Level(args.level), zs_from=args.zs, start=args.start,
               end=args.end, out=out, bs=not args.no_bs))


if __name__ == "__main__":
    main()
