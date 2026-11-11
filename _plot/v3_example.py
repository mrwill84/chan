"""第三版「当下3买」的例子：aAbBc → c 末趋势背驰 1买 → A'（下上下）→ 离开 → 回拉不进 A'。

用法：PYTHONPATH=. python _plot/v3_example.py STEPS_DIR [最多几张] [OUT_DIR]
  按股票顺序取判出的事件（不挑），一笔一张：30分钟K线 + 笔 / 段（红上绿下）/ 段中枢，
  标出 A、b、B、c、1买、a'、A'、离开段、回拉低点、买入，以及买入后持有40根的结果。
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, "_plot")
from fake3b import BI, BUY, DOWN, INK, SEGZS, UP, candles  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
NEWZS = "#8e6cc0"


def events(steps_dir, need_confirm=True):
    """``need_confirm=False`` 时也给没过5分钟确认的回试形态（取该次离开第一次出现的那步）。"""
    from backtest.sublevel import _read_steps

    for p in sorted(Path(steps_dir).glob("*.steps.jsonl")):
        st, first, conf = _read_steps(p), {}, {}
        for t in sorted(st):
            c = st[t]["cand"]
            if c:
                first.setdefault((c[0], c[1]), (t, c))
                if st[t]["confirm"]:
                    conf.setdefault((c[0], c[1]), (t, c, st[t]["confirm"]))
        for key, (t, c) in first.items():
            if key in conf:
                yield (p.name.split(".")[0], *conf[key])
            elif not need_confirm:
                yield p.name.split(".")[0], t, c, None


def fig(code, t, cand, confirm, n, path):
    from backtest.costs import Costs, simulate_long
    from types import SimpleNamespace

    from backtest.sublevel import c_beichi, c_inner_zhongshu
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import (build_zhongshus, legs_from_bis, relation, usable_count,
                               zhongshu_at)
    from feeds import Level, cache

    b = cache.load(code, Level.M30)
    b = [replace(x, idx=i) for i, x in enumerate(b[-4000:])]
    pre = b[: t + 1]
    a = analyze(pre, check=True)
    legs = legs_from_bis(a.bis)          # 第三版口径：整条链在笔上
    zss = build_zhongshus(legs)
    li = len(legs) - 2
    k = [i for i, z in enumerate(zss) if z.depart_leg is not None and z.depart_leg < li][-1]
    A, B, c = zss[k - 1], zss[k], zss[k].depart_leg
    j = c_beichi(legs, zss, k, a.bis, macd([x.close for x in pre]), li, need_c3=False)
    lo1 = a.bis[j].end.peak_idx
    p1 = SimpleNamespace(leg=j, bar=lo1, price=legs[j].low)
    A2 = zhongshu_at(legs, j + 2, usable_count(legs, True))

    def zb(z, end=None):
        return (a.bis[legs[z.start_leg].src_start].start.peak_idx,
                a.bis[legs[z.core_end if end is None else end].src_end].end.peak_idx)

    tr = simulate_long(b, t, 40, code, Costs(), intraday=True)
    lo = max(0, zb(A)[0] - 30)
    hi = min(len(b), (t if isinstance(tr, str) else tr.exit_bar) + 40)

    f, ax = plt.subplots(figsize=(18, 8.8))
    candles(ax, b, lo, hi)
    # 这一版的结构单位是笔：笔按方向红绿粗画；线段只作背景（灰细线）
    for s in a.segs:
        if s.start.peak_idx >= lo:
            ax.plot([s.start.peak_idx, s.end.peak_idx], [s.start.price, s.end.price],
                    color=BI, lw=1.0, alpha=0.7, zorder=2)
    for x in a.bis:
        if x.start.peak_idx >= lo:
            ax.plot([x.start.peak_idx, x.end.peak_idx], [x.start.price, x.end.price],
                    color=UP if x.direction.name == "UP" else DOWN, lw=1.9,
                    ls="-" if x.confirmed else ":", zorder=4)
    # 买入之后的价格走势（终版的笔，虚线）
    fin = analyze(b, check=False)
    for x in fin.bis:
        if t <= x.start.peak_idx < hi:
            ax.plot([x.start.peak_idx, x.end.peak_idx], [x.start.price, x.end.price],
                    color=UP if x.direction.name == "UP" else DOWN, lw=1.2, ls="--", alpha=0.5)

    for z, name, color in ((A, "A", SEGZS), (B, "B", SEGZS), (A2, "A'", NEWZS)):
        i0, i1 = zb(z, z.end_leg if z is A2 else None)
        ax.add_patch(Rectangle((max(i0, lo), z.zd), i1 - max(i0, lo), z.zg - z.zd,
                               facecolor=color, alpha=0.22, edgecolor=color, lw=1.4, zorder=1))
        shape = "".join("上" if legs[j].direction.name == "UP" else "下"
                        for j in range(z.start_leg, (z.end_leg if z is A2 else z.core_end) + 1))
        ax.text(max(i0, lo), z.zg, f"{name}  {shape}\n[{z.zd:.2f}, {z.zg:.2f}]",
                fontsize=10, color=color if color != SEGZS else "#a8681a", va="bottom",
                fontweight="bold")
    # c 内部（B 的离开段到 1买）自己的笔中枢 C：有 C 时背驰比 d 对 c，见 `c_inner_zhongshu`
    C = c_inner_zhongshu(legs, c, j)
    if C is not None:
        i0, i1 = zb(C)
        ax.add_patch(Rectangle((max(i0, lo), C.zd), i1 - max(i0, lo), C.zg - C.zd,
                               facecolor="#2a9d8f", alpha=0.22, edgecolor="#2a9d8f", lw=1.4, zorder=1))
        ax.text(max(i0, lo), C.zd, f"C  [{C.zd:.2f}, {C.zg:.2f}]", fontsize=10, color="#1f7a6f",
                va="top", fontweight="bold")
    for j, lab, col in ((A.depart_leg, "b", DOWN), (c, "c", DOWN), (p1.leg + 1, "a'", UP),
                        (li, "离开 A'", UP), (li + 1, "回拉", DOWN)):
        s = a.bis[j]
        ax.text((s.start.peak_idx + s.end.peak_idx) / 2, (s.start.price + s.end.price) / 2,
                f" {lab}", fontsize=13, color=col, fontweight="bold", zorder=7,
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.7))
    ax.scatter([p1.bar], [p1.price], marker="o", s=160, color=BUY, zorder=8, edgecolor="white")
    ax.annotate(f"1买（c 对 b 背驰，MACD；免「c 含 3卖」）\n{b[p1.bar].ts[5:16]}  {p1.price:.2f}",
                (p1.bar, p1.price), xytext=(10, -40), textcoords="offset points", fontsize=9.5,
                color=BUY, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BUY, lw=0.7))
    ax.axhline(A2.zg, color=NEWZS, lw=1.2, ls="-", alpha=0.8)
    ax.scatter([cand[4]], [cand[3]], marker="*", s=260, color=BUY, zorder=8, edgecolor="white")
    ax.scatter([t], [b[t].close], marker="^", s=170, color=BUY, zorder=8)
    how = f"5分钟确认 {confirm[5:16]}" if confirm else "5分钟没确认 → 不买（只画结构）"
    ax.annotate(f"{'3买 买入' if confirm else '回试形态'} {b[t].close:.2f}  {b[t].ts[5:16]}\n"
                f"回拉低点 {cand[3]:.2f} > A' 的 ZG {A2.zg:.2f}\n{how}", (t, b[t].close),
                xytext=(-20, 50),
                textcoords="offset points", ha="right", fontsize=9.5,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BUY, lw=0.7),
                arrowprops=dict(arrowstyle="-", color=BUY, lw=0.7))
    res = "成交不了" if confirm else "没买"
    if confirm and not isinstance(tr, str):
        col = UP if tr.net > 0 else DOWN
        ax.axvspan(tr.entry_bar, tr.exit_bar, color=col, alpha=0.05, lw=0)
        ax.scatter([tr.exit_bar], [b[tr.exit_bar].close], marker="v", s=150, color=col, zorder=8)
        res = f"持有40根 扣成本 {tr.net:+.1%}"
        ax.annotate(res, (tr.exit_bar, b[tr.exit_bar].close), xytext=(10, -20),
                    textcoords="offset points", fontsize=9.5, color=col)
    ax.axvline(t + 0.5, color=INK, lw=0.7, ls=":")
    ticks = list(range(lo, hi, max(1, (hi - lo) // 12)))
    ax.set_xticks(ticks, [b[i].ts[2:10] for i in ticks])
    ax.set_xlim(lo - 1, hi)
    rel = {"up": "上涨", "higher_level": "高级别中枢", "ambiguous": "不定"}.get(relation(B, A2), "?")
    ax.set_title(f"第三版例 {n}：{code}  A→B 下跌（B 的 GG {B.gg:.2f} < A 的 DD {A.dd:.2f}），"
                 f"c 末 1买，A' 与 B 关系：{rel}；{res}\n"
                 "30分钟笔中枢（A/B 橙、A' 紫）；红绿粗线 = 买入那一刻的笔，灰线 = 线段（背景）；"
                 "竖点线右边虚线 = 买入之后终版的笔",
                 fontsize=11, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    f.tight_layout()
    f.savefig(path, dpi=115)
    plt.close(f)
    print("写出", path)


if __name__ == "__main__":
    d = sys.argv[1]
    most = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    out = Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/v3")
    show_all = len(sys.argv) > 4 and sys.argv[4] == "all"
    out.mkdir(parents=True, exist_ok=True)
    for n, (code, t, c, conf) in enumerate(events(d, need_confirm=not show_all), 1):
        if n > most:
            break
        tail = "" if conf else "_noconf"
        fig(code, t, c, conf, n, str(out / f"v3_example_{n:02d}_{code}{tail}.png"))
