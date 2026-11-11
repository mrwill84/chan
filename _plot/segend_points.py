"""十种「当下判断段结束」口径各自把点判在了哪里，分两组画。300308 日线。

用法：PYTHONPATH=. python _plot/segend_points.py <1|2|3|4>
  1/2 = 原五种的位置图/泳道图；3/4 = BOLL+RSI 新五种。

先跑出数据（一个进程一种口径，C-bitflip）：
    PYTHONPATH=. python .tmp/segend_eval.py base 300308
    PYTHONPATH=. python .tmp/segend_eval.py <method> 300308

两张图各回答一个问题：
  1 位置  每种口径把段结束判在了哪根K线上，判对了还是判错了
  2 泳道  每一次宣布「从什么时候开始说」到「说的是哪里」，提前量就是这段横距

**关键是要同时看见「判在哪」和「什么时候判的」。** 只画判在哪，会得到一张
看起来很准的图——那正是 C-lag 警告过的事后错觉。
"""
from __future__ import annotations

import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

from chan.pipeline import analyze
from feeds import Level, cache

CODE = "300308"
BASE = ".tmp/segend_base.json"

# 每排已经有方法名，不用十种颜色制造额外的识色负担；命中/误判靠形状。
OLD_METHODS = ["bi_beichi", "sub_third", "sub_depart", "sub_complete", "force"]
NEW_METHODS = ["boll_first", "boll_second", "boll_squeeze",
               "bi_beichi_rsi", "force_rsi"]
METHODS = OLD_METHODS + NEW_METHODS
COLOR = "#1f77b4"
CN = {"bi_beichi": "bi_beichi\n区间套背驰",
      "sub_third": "sub_third\n次级别3类点",
      "sub_depart": "sub_depart\n次级别离开",
      "sub_complete": "sub_complete\n走势必完美",
      "force": "force\n趋势平均力度",
      "boll_first": "boll_first\n高一级BOLL 1类",
      "boll_second": "boll_second\n高一级BOLL 2类",
      "boll_squeeze": "boll_squeeze\n高一级BOLL收口",
      "bi_beichi_rsi": "bi_beichi_rsi\nRSI背驰对照",
      "force_rsi": "force_rsi\nRSI力度对照"}


def load():
    bars = cache.load(CODE, Level.DAY)
    ref = json.load(open(BASE, encoding="utf-8"))
    truth = sorted(ref["truth"])
    first = {int(k): v for k, v in ref["first_seen"].items()}
    ann = {}
    for m in METHODS:
        try:
            ann[m] = json.load(open(f".tmp/segend_ann_{m}.json", encoding="utf-8"))["ann"]
        except FileNotFoundError:
            ann[m] = []
    return bars, truth, first, ann


def first_calls(rows):
    """每个被宣布过的段端，**第一次**被宣布是什么时候。{段端K线: 首次宣布t}"""
    out: dict[int, int] = {}
    for t, j, _s in rows:
        out.setdefault(j, t)
    return out


def datefmt(ax, bars, step=120):
    idx = range(0, len(bars), step)
    ax.set_xticks(list(idx))
    ax.set_xticklabels([bars[i].ts[:7] for i in idx], rotation=45,
                       ha="right", fontsize=8)


# --------------------------------------------------------------------------
# 图 1：判在哪根K线上。●=判对（真的是段端）　✕=判错
# --------------------------------------------------------------------------
def fig1(out: str, methods=OLD_METHODS):
    bars, truth, _first, ann = load()
    a = analyze(bars, check=False)
    fig, ax = plt.subplots(figsize=(16, 8.5))

    ax.plot(range(len(bars)), [b.close for b in bars], lw=.8, color="#bbb",
            zorder=1, label="收盘价（后复权，对数轴）")

    # 线段折线 + 真值段端：黑竖线贯穿全图，一眼看出哪些口径对齐了
    xs, ys = [], []
    for s in a.segs:
        if not xs:
            xs.append(s.start.peak_idx); ys.append(s.start_price)
        xs.append(s.end.peak_idx); ys.append(s.end_price)
    ax.plot(xs, ys, lw=1.5, color="#333", zorder=3,
            label=f"最终划出的线段（{len(a.segs)} 根）")
    for k, j in enumerate(truth):
        ax.axvline(j, color="k", lw=.7, alpha=.3, zorder=2,
                   label="真值段端（事后确认，23 个）" if k == 0 else None)

    close = [b.close for b in bars]
    # 一种口径一条横排，压在价格图上方的留白里
    hi = max(close)
    for n, m in enumerate(methods):
        fc = first_calls(ann[m])
        y = hi * (1.5 ** (n + 1))          # 对数轴上等距排开
        hit = [j for j in fc if j in set(truth)]
        miss = [j for j in fc if j not in set(truth)]
        ax.scatter(hit, [y] * len(hit), marker="o", s=52, color=COLOR,
                   edgecolor="k", lw=.6, zorder=5)
        ax.scatter(miss, [y] * len(miss), marker="x", s=46, color=COLOR,
                   alpha=.75, zorder=5)
        ax.text(-30, y, CN[m], fontsize=8, va="center", ha="right",
                color="#222", linespacing=.95)
        ax.text(len(bars) + 20, y,
                f"{len(hit)}/{len(fc)} 判对", fontsize=8.5, va="center",
                color="#222")
        # 判对的那些，往下牵一条线到价格上，看它落在哪
        for j in hit:
            ax.plot([j, j], [close[j], y], lw=.6, ls=":", color=COLOR,
                    alpha=.5, zorder=4)

    ax.set_yscale("log")
    ax.set_xlim(-230, len(bars) + 210)
    datefmt(ax, bars)
    ax.set_ylabel("价格（对数轴）")
    ax.set_title(
        f"{CODE} 日线：{len(methods)} 种「当下判断段结束」口径各自把段端判在了哪里\n"
        f"● = 判对（确实是段端，虚线牵到价格上）　× = 判错（事后看那里不是段端）　"
        f"黑竖线 = 真值段端 23 个\n"
        f"注意这张图只画「判在哪」，不画「什么时候判的」——那个在图 "
        f"{'2' if methods == OLD_METHODS else '4'}，只看这张会得到一张假准的图",
        fontsize=11)
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=.2, which="both", axis="x")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


# --------------------------------------------------------------------------
# 图 2：泳道。横距 = 提前量，颜色深浅 = 判对还是判错
# --------------------------------------------------------------------------
def fig2(out: str, methods=OLD_METHODS):
    bars, truth, first, ann = load()
    tset = set(truth)
    fig, axes = plt.subplots(len(methods) + 1, 1, figsize=(16, 10),
                             sharex=True,
                             gridspec_kw={"height_ratios": [1.6] + [1] * len(methods)})

    top = axes[0]
    top.plot(range(len(bars)), [b.close for b in bars], lw=.8, color="#888")
    top.set_yscale("log")
    for j in truth:
        top.axvline(j, color="k", lw=.7, alpha=.3)
    top.set_ylabel("收盘价", fontsize=9)
    top.set_title(
        f"{CODE} 日线：每一次「段已经结束了」，说的是哪里、什么时候才说出口\n"
        f"○ = 判定的段端　粗彩条 = 它自己迟了多久才说　"
        f"◆ = 基线（67课 特征序列）确认同一个段端的时刻\n"
        f"灰线那一截才是提前量——彩条越长说明这个口径自己也不快；"
        f"淡色 × 虚线 = 判错（事后看那里不是段端）",
        fontsize=11)
    top.grid(alpha=.2, axis="x")

    for n, m in enumerate(methods):
        ax = axes[n + 1]
        fc = first_calls(ann[m])
        leads = []
        for j, t in sorted(fc.items()):
            ok = j in tset
            ax.plot([j, t], [0, 0], lw=5 if ok else 2.5, color=COLOR,
                    alpha=.85 if ok else .3,
                    ls="-" if ok else ":", solid_capstyle="butt", zorder=3)
            if ok:
                ax.scatter([j], [0], marker="o", s=46, color=COLOR,
                           edgecolor="k", lw=.6, zorder=5)
            else:
                ax.scatter([j], [0], marker="x", s=32, color=COLOR,
                           lw=1.1, alpha=.6, zorder=5)
            # 基线什么时候才看见这个段端——两者之差就是提前量
            if ok and j in first:
                leads.append(first[j] - t)
                ax.scatter([first[j]], [0], marker="D", s=26, color="#555",
                           zorder=4)
                ax.plot([t, first[j]], [0, 0], lw=1.6, color="#444",
                        alpha=.7, zorder=2)
        hit = sum(1 for j in fc if j in tset)
        ax.set_ylim(-1, 1)
        ax.set_yticks([])
        ax.set_ylabel(CN[m], fontsize=8, rotation=0, ha="right",
                      va="center", color="#222", linespacing=.95)
        for j in truth:
            ax.axvline(j, color="k", lw=.6, alpha=.22, zorder=1)
        med = sorted(leads)[len(leads) // 2] if leads else None
        ax.text(0.998, .72,
                f"宣布 {len(fc)} 处，判对 {hit} 处"
                + (f"，提前量中位 {med} 根" if med is not None else ""),
                transform=ax.transAxes, ha="right", fontsize=8.5,
                color="#222")
        ax.grid(alpha=.18, axis="x")

    datefmt(axes[-1], bars)
    axes[-1].set_xlim(-40, len(bars) + 40)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "1"
    {"1": lambda: fig1("_plot/segend_1_where.png"),
     "2": lambda: fig2("_plot/segend_2_lanes.png"),
     "3": lambda: fig1("_plot/segend_new_1_where.png", NEW_METHODS),
     "4": lambda: fig2("_plot/segend_new_2_lanes.png", NEW_METHODS)}[which]()
