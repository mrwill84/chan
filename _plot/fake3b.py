"""结构化卖法里「收盘跌回 ZG」的那几笔假 3买：一笔一张图。

用法：PYTHONPATH=. python _plot/fake3b.py [CODES_FILE STEPS_DIR EXIT_DIR OUT_DIR]
  默认是正式样本（.tmp/oos200b.txt / .tmp/now3b2_new / .tmp/exit3b_new → _plot/fake3b/）。

每张图：30分钟K线；买入那一刻看到的 笔 / 线段（红上绿下）/ 笔中枢 / 段中枢（实线），
买入之后叠加终版划线（虚线，看它后来变成了什么）；ZG（判 3买 的线）、回试高点、
离开段、回试低点、买入、跌回 ZG 卖出。
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

UP, DOWN = "#d62728", "#2ca02c"      # A股习惯：红涨绿跌
BI = "#7f8c9d"
SEGZS = "#e8a33d"                    # 段中枢
BIZS = "#8e6cc0"                     # 笔中枢
BUY, INK = "#1f77b4", "#222222"


def structure(bars):
    """笔、线段端点（带方向）、笔中枢、段中枢（起K线, 止K线, zd, zg）。"""
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs

    a = analyze(bars, check=False)

    def spans(legs):
        return [(a.bis[legs[z.start_leg].src_start].start.peak_idx,
                 a.bis[legs[z.core_end].src_end].end.peak_idx, z.zd, z.zg)
                for z in build_zhongshus(legs)]

    bis = [(b.start.peak_idx, b.start.price, b.end.peak_idx, b.end.price) for b in a.bis]
    segs = [(s.start.peak_idx, s.start.price, s.end.peak_idx, s.end.price,
             s.direction.name == "UP", s.confirmed) for s in a.segs]
    return bis, segs, spans(legs_from_bis(a.bis)), spans(legs_from_segs(a.segs, a.bis))


def draw(ax, st, lo, hi, *, solid, after=None):
    bis, segs, bizs, segzs = st
    ls = "-" if solid else "--"
    ok = lambda i: lo <= i < hi and (after is None or i >= after)  # noqa: E731
    for i0, z0, i1, z1 in bis:
        if ok(i0) and ok(i1):
            ax.plot([i0, i1], [z0, z1], color=BI, lw=0.9, ls=ls, alpha=0.9 if solid else 0.5,
                    zorder=3)
    for i0, z0, i1, z1, up, conf in segs:
        if ok(i0) and ok(i1):
            ax.plot([i0, i1], [z0, z1], color=UP if up else DOWN, lw=2.6 if solid else 1.8,
                    ls=ls if conf else ":", alpha=0.95 if solid else 0.6, zorder=4)
    for zs, color, a_f in ((segzs, SEGZS, 0.20), (bizs, BIZS, 0.12)):
        for i0, i1, zd, zg in zs:
            if i1 < lo or i0 >= hi or (after is not None and i1 < after):
                continue
            x0 = max(i0, lo) if after is None else max(i0, lo, after)
            ax.add_patch(Rectangle((x0, zd), min(i1, hi - 1) - x0, zg - zd,
                                   facecolor=color, alpha=a_f if solid else a_f / 2.5,
                                   edgecolor=color, lw=1.4 if color == SEGZS else 0.8,
                                   ls=ls, zorder=1 if color == SEGZS else 2))


def candles(ax, bars, lo, hi):
    for i in range(lo, hi):
        b = bars[i]
        c = UP if b.close >= b.open else DOWN
        ax.vlines(i, b.low, b.high, color=c, lw=0.5, alpha=0.55, zorder=0)
        ax.add_patch(Rectangle((i - 0.35, min(b.open, b.close)), 0.7,
                               max(abs(b.close - b.open), 1e-9), facecolor=c, edgecolor=c,
                               lw=0, alpha=0.55, zorder=0))


def events(codes_file, steps_dir, exit_dir):
    """按 exit3b_summary 的口径重放，挑出卖出原因是 stop 的那几笔。"""
    from backtest.costs import Costs, enter_long, exit_long, prev_day_close
    from backtest.sublevel import _read_steps, structural_exit
    from feeds import Level, cache

    out = []
    for code in [c.strip() for c in open(codes_file) if c.strip()]:
        p = Path(f"{steps_dir}/{code}.steps.jsonl")
        if not p.exists():
            continue
        st = _read_steps(p)
        seen = {}
        for t in sorted(st):
            c = st[t]["cand"]
            if c and st[t]["confirm"]:
                seen.setdefault((c[0], c[1]), (t, c, st[t]["confirm"]))
        if not seen:
            continue
        b = cache.load(code, Level.M30)
        b = [replace(x, idx=i) for i, x in enumerate(b[-4000:])]
        ref = prev_day_close(b)
        ts = {x.ts: i for i, x in enumerate(b)}
        for t, c, conf in seen.values():
            e = enter_long(b, t, code, ref)
            if isinstance(e, str):
                continue
            h_ref = b[ts[c[5]]].high
            ex = _read_steps(Path(f"{exit_dir}/{code}.{t}.jsonl"))
            x, why = structural_exit(ex, b, e, c[2], h_ref, 160)
            tr = exit_long(b, t, e, x, code, Costs(), ref=ref, intraday=True)
            if why == "stop" and not isinstance(tr, str):
                out.append(dict(code=code, bars=b, t=t, e=e, x_sig=x, tr=tr, zg=c[2],
                                low=c[3], lo_i=c[4], rs=ts[c[5]], h_ref=h_ref,
                                leave=c[1], confirm=conf))
    return out


def fig(ev, n, total, path):
    from chan.num import eq

    b, t, zg = ev["bars"], ev["t"], ev["zg"]
    now = structure(b[: t + 1])
    fin = structure(b)
    # 判 3买 用的那个段中枢
    orig = next((z for z in now[3] if eq(z[3], zg)), None)
    lo = max(0, (orig[0] if orig else ev["leave"]) - 60)
    hi = min(len(b), ev["tr"].exit_bar + 60)

    fig_, ax = plt.subplots(figsize=(17, 8.5))
    candles(ax, b, lo, hi)
    draw(ax, now, lo, hi, solid=True)
    draw(ax, fin, lo, hi, solid=False, after=t + 1)
    ax.axvline(t + 0.5, color=INK, lw=0.7, ls=":", alpha=0.6)
    ax.text(t + 1, ax.get_ylim()[1], " 买入之后：终版划线（虚线）", fontsize=9, va="top", color=INK)
    ax.text(t - 1, ax.get_ylim()[1], "买入时看到的划线（实线） ", fontsize=9, va="top",
            ha="right", color=INK)

    # ZG：判假 3买 的那条线
    ax.axhline(zg, color=SEGZS, lw=1.6, ls="-", alpha=0.9, zorder=5)
    ax.text(lo + 1, zg, f" ZG {zg:.2f} ← 判 3买 用的线：回试低点必须在它之上；"
            "买入后收盘跌回它下面 = 假3买", fontsize=9.5, color="#a8681a", va="bottom",
            fontweight="bold")
    ax.axhline(ev["h_ref"], color=INK, lw=0.8, ls="-.", alpha=0.6)
    ax.text(lo + 1, ev["h_ref"], f" 回试高点（离开段的顶）{ev['h_ref']:.2f}", fontsize=8.5,
            color=INK, va="bottom")
    if orig:
        ax.text(orig[0], orig[2], f"判3买的段中枢\n[{orig[2]:.2f}, {orig[3]:.2f}]",
                fontsize=8.5, color="#a8681a", va="top")

    ax.annotate("离开段", (ev["leave"], b[ev["leave"]].low), xytext=(0, -34),
                textcoords="offset points", ha="center", fontsize=9,
                arrowprops=dict(arrowstyle="->", color=INK, lw=0.6))
    ax.scatter([ev["lo_i"]], [ev["low"]], marker="*", s=260, color=BUY, zorder=7,
               edgecolor="white", lw=1)
    tr = ev["tr"]
    ax.scatter([tr.entry_bar], [b[tr.entry_bar].close], marker="^", s=170, color=BUY, zorder=8)
    ax.annotate(f"▲ 买入 {b[tr.entry_bar].close:.2f}\n{b[tr.entry_bar].ts[5:16]}\n"
                f"（5分钟确认 {ev['confirm'][5:16]}）",
                (tr.entry_bar, b[tr.entry_bar].close), xytext=(-16, 44),
                textcoords="offset points", ha="right", fontsize=9,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BUY, lw=0.7),
                arrowprops=dict(arrowstyle="-", color=BUY, lw=0.7))
    xs = ev["x_sig"]
    ax.scatter([xs], [b[xs].close], marker="x", s=120, color=DOWN, zorder=8, lw=2.4)
    sold = "" if tr.exit_bar == xs else f"\nT+1/跌停顺延到 {b[tr.exit_bar].ts[5:16]} 成交"
    ax.annotate(f"× 收盘 {b[xs].close:.2f} < ZG → 假3买，卖出\n{b[xs].ts[5:16]}"
                f"（持有 {xs - tr.entry_bar} 根）{sold}\n扣成本 {tr.net:+.1%}",
                (xs, b[xs].close), xytext=(16, -46), textcoords="offset points", fontsize=9,
                color=DOWN, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=DOWN, lw=0.7),
                arrowprops=dict(arrowstyle="-", color=DOWN, lw=0.7))
    ax.axvspan(tr.entry_bar, tr.exit_bar, color=DOWN, alpha=0.05, lw=0)

    ax.legend(handles=[
        Line2D([], [], color=BI, lw=0.9, label="笔"),
        Line2D([], [], color=UP, lw=2.6, label="向上线段"),
        Line2D([], [], color=DOWN, lw=2.6, label="向下线段"),
        Line2D([], [], color=INK, lw=2.0, ls=":", label="未确认的线段（点线）"),
        Patch(facecolor=BIZS, alpha=0.3, edgecolor=BIZS, label="笔中枢"),
        Patch(facecolor=SEGZS, alpha=0.4, edgecolor=SEGZS, label="段中枢 [ZD, ZG]"),
        Line2D([], [], marker="*", color=BUY, lw=0, markersize=12, label="回试低点（买入时）"),
    ], loc="upper left", bbox_to_anchor=(0, 0.93), fontsize=8.5, frameon=False, ncol=2)

    ticks = list(range(lo, hi, max(1, (hi - lo) // 12)))
    ax.set_xticks(ticks, [b[i].ts[2:10] for i in ticks])
    ax.set_xlim(lo - 1, hi)
    ax.set_ylabel("30分钟（后复权）")
    ax.set_title(f"假3买 {n}/{total}：{ev['code']}  买入 {b[tr.entry_bar].ts[:16]}  "
                 f"→ {xs - tr.entry_bar} 根后收盘跌回 ZG {zg:.2f}，扣成本 {tr.net:+.1%}\n"
                 "正式样本（新200只）第二版入场 + 结构化卖法；27 笔里 12 笔是这样卖掉的",
                 fontsize=11, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    fig_.tight_layout()
    fig_.savefig(path, dpi=120)
    plt.close(fig_)
    print("写出", path)


if __name__ == "__main__":
    a = sys.argv[1:] or [".tmp/oos200b.txt", ".tmp/now3b2_new", ".tmp/exit3b_new",
                         "_plot/fake3b"]
    Path(a[3]).mkdir(parents=True, exist_ok=True)
    evs = events(*a[:3])
    for n, ev in enumerate(evs, 1):
        fig(ev, n, len(evs), f"{a[3]}/fake3b_{n:02d}_{ev['code']}.png")
