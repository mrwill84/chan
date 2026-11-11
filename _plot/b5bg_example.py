"""5 分钟单一级别区间套 1买 的例子：每个当下信号（低点）一张图，看结构对不对。
用法：PYTHONPATH=. python _plot/b5bg_example.py WALK_DIR [OUT_DIR]
  取每个低点第一次「背景成立 + 笔级别 1买 在低点」的那一步，用那一步的划线画：
  灰线 = 线段（实线已确认、点线未确认），橙框 = 线段中枢（最后一个是 B），紫框 = c 里的笔中枢（走势必完美），
  红绿细线 = 笔，蓝星 = c 的低点（1买），竖点线 = 信号那根；之后 120 根 K 线照画（不画之后的划线），
  标题写出后来怎样：等到那笔确认 / 1买没了 / 创新低 / 背景没了。
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
from fake3b import BUY, DOWN, INK, UP, candles  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
SPAN, WINDOW = 2000, 4000


def fate(st, t0, lts):
    for t in range(t0, min(t0 + 80, SPAN)):
        r = st[t]
        if r.get("bg") is None:
            return "背景没了"
        if r.get("low_ts") != lts:
            return "创了新低"
        if not r.get("pt"):
            return "笔级别 1买 没了（并进新笔中枢）"
        if r["pt"][1]:
            return f"等到那笔确认（第 {t - t0} 根后）"
    return "80 根内一直未确认"


def one(code, t0, r, st, out, n):
    from backtest.sublevel import seg_beichi_now
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs
    from feeds import Level, cache

    b5 = cache.load(code, Level.M5)
    base = len(b5) - SPAN
    i = base + t0
    off = max(0, i - WINDOW + 1)
    w = [replace(x, idx=k) for k, x in enumerate(b5[off: i + 1])]
    a = analyze(w, check=True)
    bg = seg_beichi_now(a, w, macd([x.close for x in w]))
    sl = legs_from_segs(a.segs, a.bis)
    szs = build_zhongshus(sl)
    bl = legs_from_bis(a.bis)
    full = [replace(x, idx=k) for k, x in enumerate(b5[off: min(len(b5), i + 121)])]
    lo_bar = a.bis[sl[szs[-2].start_leg].src_start].start.peak_idx if len(szs) >= 2 else len(w) - 400
    lo, hi = max(0, lo_bar - 20), len(full)
    f, ax = plt.subplots(figsize=(19, 8.6))
    candles(ax, full, lo, hi)
    for x in a.bis:
        if x.end.peak_idx >= lo:
            ax.plot([x.start.peak_idx, x.end.peak_idx], [x.start.price, x.end.price], lw=1.0, alpha=0.8,
                    color=UP if x.direction.name == "UP" else DOWN, ls="-" if x.confirmed else ":", zorder=3)
    for s in a.segs:
        if s.end.peak_idx >= lo:
            ax.plot([s.start.peak_idx, s.end.peak_idx], [s.start.price, s.end.price], lw=2.6, color="#555555",
                    alpha=0.7, ls="-" if s.confirmed else ":", zorder=4)
    # 最后一个框画规则实际用的 B：当下离开时 B 去掉了最后那段重算，不能直接画 szs[-1]
    boxes = [(z.start_leg, z.core_end, z.zd, z.zg, z.dd) for z in szs]
    if bg and boxes:
        boxes[-1] = bg["B"]
    for k, (sleg, ce, zd, zg, dd) in enumerate(boxes):
        s0 = a.bis[sl[sleg].src_start].start.peak_idx
        s1 = a.bis[sl[ce].src_end].end.peak_idx
        if s1 < lo:
            continue
        name = "B" if k == len(boxes) - 1 else f"Z{k}"
        ax.add_patch(Rectangle((max(s0, lo), zd), s1 - max(s0, lo), zg - zd, facecolor="#e0a050", alpha=0.22,
                               edgecolor="#a8681a", lw=1.4))
        ax.text(max(s0, lo), zg, f" 线段中枢 {name} [{zd:.4g},{zg:.4g}] DD={dd:.4g}", fontsize=9,
                color="#a8681a", va="bottom")
    if bg:
        jlow = bg["low_bi"]
        for z in build_zhongshus(bl):
            s0 = a.bis[bl[z.start_leg].src_start].start.peak_idx
            s1 = a.bis[bl[z.core_end].src_end].end.peak_idx
            if s0 >= lo and z.core_end < jlow and s1 > lo_bar:
                ax.add_patch(Rectangle((s0, z.zd), s1 - s0, z.zg - z.zd, facecolor="#8e6cc0", alpha=0.15,
                                       edgecolor="#8e6cc0", lw=1.0))
        ax.scatter([bg["low_bar"]], [bg["low"]], marker="*", s=320, color=BUY, zorder=8, edgecolor="white")
        ax.annotate(f"c 低点 {bg['low']:.4g}（{'趋势' if bg['kind'] == 'trend' else '盘整'}背驰）\n{w[bg['low_bar']].ts[5:16]}",
                    (bg["low_bar"], bg["low"]), xytext=(8, -40), textcoords="offset points", fontsize=9.5, color=BUY)
    ax.axvline(len(w) - 1, color=INK, lw=0.9, ls=":")
    ax.text(len(w) - 1, ax.get_ylim()[1], f" 信号那根 {w[-1].ts[5:16]}", va="top", fontsize=9.5)
    ticks = list(range(lo, hi, max(1, (hi - lo) // 14)))
    ax.set_xticks(ticks, [full[k].ts[5:16] for k in ticks], fontsize=8)
    ax.set_xlim(lo - 1, hi)
    ax.set_title(f"{code} 5 分钟区间套 1买（当下）：{r['low_ts'][5:16]} 低点；后来：{fate(st, t0, r['low_ts'])}；"
                 "灰粗线 = 线段，红绿细线 = 笔，橙框 = 线段中枢，紫框 = 笔中枢（只画 B 之后、低点之前的）；竖线右边 = 之后的 K 线",
                 fontsize=10.5, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"b5bg_{n:02d}_{code}_{r['low_ts'][5:10].replace('-', '')}_{r['low_ts'][11:16].replace(':', '')}.png"
    f.tight_layout()
    f.savefig(path, dpi=105)
    plt.close(f)
    print(n, code, r["low_ts"], fate(st, t0, r["low_ts"]), "→", path)


def main(walk, out):
    from backtest.sublevel import _read_steps

    n = 0
    for p in sorted(Path(walk).glob("*.steps.jsonl")):
        code = p.name.split(".")[0]
        st = _read_steps(p)
        seen = set()
        for t in sorted(st):
            r = st[t]
            if r.get("bg") and r.get("pt") and r["low_ts"] not in seen:
                seen.add(r["low_ts"])
                n += 1
                one(code, t, r, st, out, n)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "_plot/b5bg"))
