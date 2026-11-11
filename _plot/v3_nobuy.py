"""没下单的形态：回拉后 5 分钟上为什么没有买点。
用法：PYTHONPATH=. python _plot/v3_nobuy.py STEPS_DIR 例子序号 [OUT_DIR]
  取该次回拉最后一个候选步（30 分钟第 t 根收盘）的 5 分钟结构：从回拉的顶重划的笔中枢、
  引擎给出的 5 分钟 1 类点，以及每个中枢「比没比、为什么没背驰」的原因，写在图上。
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
from fake3b import DOWN, INK, UP, candles  # noqa: E402
from v3_example import events  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

NO_REASON = {
    "no_enter": "这个中枢前面没有进入段（回拉的顶之后没有同向的 a）",
    "no_leave": "价格还没离开这个中枢（还在里面震荡）",
    "opposite": "进入段和离开段方向相反（中枢是折返拐点，没有两次同向推进可比）",
    "out_of_range": "越界",
}


def main(steps_dir: str, which: int, out: Path) -> None:
    from backtest.sublevel import _read_steps, sub_window, top5_index
    from chan.beichi import detect_at, reach_reason
    from chan.buysell import find_bspoints
    from chan.macd import macd
    from chan.pipeline import analyze
    from chan.zhongshu import build_zhongshus, legs_from_bis
    from feeds import Level, cache

    code, t0, cand0, _ = list(events(steps_dir, need_confirm=False))[which - 1]
    steps = _read_steps(Path(steps_dir) / f"{code}.steps.jsonl")
    key = (cand0[0], cand0[1])
    ts_c = [t for t in sorted(steps) if steps[t]["cand"] and (steps[t]["cand"][0], steps[t]["cand"][1]) == key]
    t = ts_c[-1]
    c = steps[t]["cand"]
    b30 = cache.load(code, Level.M30)
    b30 = [replace(x, idx=i) for i, x in enumerate(b30[-4000:])]
    b5 = cache.load(code, Level.M5)
    ts5 = [x.ts for x in b5]
    w = sub_window(b5, ts5, b30[t].ts, 4000)
    w2 = sub_window(b5, ts5, b30[min(t + 30, len(b30) - 1)].ts, 5000)
    off = next(i for i, x in enumerate(w2) if x.ts == w[0].ts)
    a = analyze(w, check=True)
    legs = legs_from_bis(a.bis)
    top = top5_index(w, c[5])
    i0 = next((i for i, x in enumerate(legs) if a.bis[x.src_start].start.peak_idx >= top), None)
    m = macd([x.close for x in w])
    zss = build_zhongshus(legs, start=i0) if i0 is not None else []
    pts = find_bspoints(legs, zss, a.bis, m, include_quasi=True) if zss else []
    p1 = [p for p in pts if p.kind == 1 and p.bar > top]

    lines = [f"{code}  候选共 {len(ts_c)} 步（30分钟 {b30[ts_c[0]].ts[5:16]} → {b30[t].ts[5:16]}）",
             f"回拉的顶 {w[top].high:.2f}（{w[top].ts[5:16]}），当时回拉已走到 {min(x.low for x in w[top:]):.2f}"]
    if not zss:
        lines.append("从顶重划后，5 分钟上还没有形成任何中枢（需要至少 3 笔重叠）→ 没有 a-A-b 可比")
    for k, z in enumerate(zss):
        why = reach_reason(legs, zss, a.bis, k)
        row = f"中枢 Z{k} [{z.zd:.2f},{z.zg:.2f}]："
        if why is not None:
            row += NO_REASON.get(why, why)
        else:
            bc = detect_at(legs, zss, a.bis, m, k)
            if bc is None:
                row += "够不着"
            else:
                side = "下" if bc.direction.name == "DOWN" else "上"
                row += (f"离开段向{side}，面积 c={bc.area_leave:.1f} {'<' if bc.area_ok else '≥'} b={bc.area_enter:.1f}"
                        f"，黄白线{'背驰' if bc.dif_ok else '没背驰'}"
                        f"（{'满足' if bc.hit else '不满足'}背驰）")
        lines.append(row)
    buys = [p for p in p1 if p.is_buy]
    lines.append("5 分钟 1买/类 1买：" + ("；".join(
        f"{w[p.bar].ts[5:16]} {w[p.bar].low:.2f}"
        f"{'(已被之后新低破掉)' if min((x.low for x in w[p.bar + 1:]), default=1e18) < w[p.bar].low * (1 - 1e-9) else ''}"
        for p in buys) if buys else "回拉以来一个都没有"))

    left = max(0, top + off - 80)
    right = min(len(w2), len(w) + off + 120)
    f, ax = plt.subplots(figsize=(18, 8.8))
    candles(ax, w2, left, right)
    for x in a.bis:
        s0, s1 = x.start.peak_idx + off, x.end.peak_idx + off
        if s1 >= left:
            ax.plot([s0, s1], [x.start.price, x.end.price], lw=1.8, zorder=4,
                    color=UP if x.direction.name == "UP" else DOWN, ls="-" if x.confirmed else ":")
    for k, z in enumerate(zss):
        s0 = a.bis[legs[z.start_leg].src_start].start.peak_idx + off
        s1 = a.bis[legs[z.core_end].src_end].end.peak_idx + off
        ax.add_patch(Rectangle((s0, z.zd), s1 - s0, z.zg - z.zd, facecolor="#e0a050", alpha=0.25,
                               edgecolor="#a8681a", lw=1.2))
        ax.text(s0, z.zg, f" 从顶重划的 Z{k} [{z.zd:.2f},{z.zg:.2f}]", fontsize=9, color="#a8681a", va="bottom")
    ax.scatter([top + off], [w[top].high], marker="*", s=240, color=INK, zorder=8)
    ax.annotate("回拉的顶", (top + off, w[top].high), xytext=(6, 14), textcoords="offset points", fontsize=10)
    for p in p1:
        ax.scatter([p.bar + off], [w[p.bar].low if p.is_buy else w[p.bar].high],
                   marker="^" if p.is_buy else "v", s=130, color="#0b6e4f" if p.is_buy else "#b00020",
                   zorder=9, edgecolor="white")
    ax.axvline(len(w) - 1 + off, color=INK, lw=0.9, ls=":")
    ax.text(len(w) - 1 + off, ax.get_ylim()[1], f" 最后一个候选步（30分钟 {b30[t].ts[5:16]}）", va="top", fontsize=9.5)
    ax.text(0.01, 0.02, "\n".join(lines), transform=ax.transAxes, fontsize=10, va="bottom", ha="left",
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#888", alpha=0.92), zorder=20)
    ax.set_title(f"{code} 为什么没下单：回拉后 5 分钟的结构（实线 = 当时看到的 5 分钟笔，橙框 = 从顶重划的中枢，"
                 "绿三角/红倒三角 = 引擎的 5 分钟 1 类买/卖点）", fontsize=11, loc="left")
    ticks = list(range(left, right, max(1, (right - left) // 14)))
    ax.set_xticks(ticks, [w2[i].ts[5:16] for i in ticks], fontsize=8.5)
    ax.set_xlim(left - 1, right)
    ax.spines[["top", "right"]].set_visible(False)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v3_nobuy_{which:02d}_{code}.png"
    f.tight_layout()
    f.savefig(path, dpi=110)
    plt.close(f)
    print("\n".join(lines))
    print("写出", path)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/v3d"))
