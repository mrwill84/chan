"""「怎么验证的」走一遍：300308 中际旭创，日线，seg 中枢。

用法：PYTHONPATH=. python _plot/validate_300308.py <1|2|3|4>

挑这只票不是因为它好看，是因为它同时是三处结论的当事票：C-base 那张唯一
记过边际的表、C-stale 的极端案例、树/trailing 口径分歧的那只。而且它 2020 起
后复权涨了 17.9 倍，**量级跨度大到能把「触发价过期」这类毛病放大成肉眼可见**。

四张图各回答一个问题：
  1 全景   引擎到底画出了什么；8 个 1买 是不是 8 个独立事件
  2 延迟   「点发生」和「引擎第一次划出它」差多远，裸收益骗了多少
  3 过期   中枢不动而价格跑了 5 倍之后，推演表还在印什么
  4 退化   短视野上这题为什么恒猜就有 98%，以及同一个中枢内读数怎么翻

一张图一个进程（C-bitflip：进程活得越短越好）。
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
from chan.zhongshu import build_zhongshus, legs_from_segs
from feeds import Level, cache

CODE = "300308"
UP, DOWN = "#d62728", "#2ca02c"          # A 股习惯：红涨绿跌
ZSC, STALE = "#9ecae1", "#9b1fb0"
POINTS = ".tmp/v300308_points.json"
WALK = ".tmp/pool_v3.jsonl"


def load_structure():
    """K线 + 线段 legs + 中枢，外加 leg 下标 → K线下标的换算。"""
    bars = cache.load(CODE, Level.DAY)
    a = analyze(bars, check=False)
    legs = legs_from_segs(a.segs, a.bis)
    zss = build_zhongshus(legs)
    # level 0 的 leg，src_* 是**笔**下标，往下翻一层就到 K 线
    x0 = lambda g: a.bis[g.src_start].start.peak_idx
    x1 = lambda g: a.bis[g.src_end].end.peak_idx
    return bars, a, legs, zss, x0, x1


def load_points():
    with open(POINTS, encoding="utf-8") as f:
        return json.load(f)["points"]


def load_walk():
    with open(WALK, encoding="utf-8") as f:
        return [r for r in map(json.loads, f) if r.get("code") == CODE]


def datefmt(ax, bars, step=120):
    idx = range(0, len(bars), step)
    ax.set_xticks(list(idx))
    ax.set_xticklabels([bars[i].ts[:7] for i in idx], rotation=45,
                       ha="right", fontsize=8)


# --------------------------------------------------------------------------
# 图 1：全景。引擎画出了什么，以及那 8 个 1买 挤在哪里
# --------------------------------------------------------------------------
def fig1(out: str):
    bars, a, legs, zss, x0, x1 = load_structure()
    pts = load_points()
    fig, ax = plt.subplots(figsize=(15, 7.5))

    closes = [b.close for b in bars]
    ax.plot(range(len(bars)), closes, lw=0.8, color="#888", zorder=1,
            label="收盘价（后复权）")

    # 线段折线：这是「划线」的产物，中枢和买卖点全部建在它上面
    xs, ys = [], []
    for i, g in enumerate(legs):
        if not xs:
            xs.append(x0(g)); ys.append(g.start_price)
        xs.append(x1(g)); ys.append(g.end_price)
    ax.plot(xs, ys, lw=1.6, color="#1f77b4", zorder=3, label=f"线段（{len(legs)} 根）")

    # 中枢盒子
    for k, z in enumerate(zss):
        gx0, gx1 = x0(legs[z.start_leg]), x1(legs[z.end_leg])
        ax.add_patch(plt.Rectangle((gx0, z.zd), gx1 - gx0, z.zg - z.zd,
                                   facecolor=ZSC, alpha=.55, edgecolor="#3182bd",
                                   lw=1.2, zorder=2))
        ax.text(gx0, z.zg, f" 中枢{k} [{z.zd:.0f}, {z.zg:.0f}]",
                fontsize=8, va="bottom", color="#08519c")

    # 走查里出现过的全部点：画在**首次划出**那一天（真实可见的时刻）
    for p in pts:
        fb, pb = p["first_seen_bar"], p["bar"]
        col = UP if p["side"] == "buy" else DOWN
        mk = "^" if p["side"] == "buy" else "v"
        ax.plot([pb, fb], [p["price"], p["entry_price"]], lw=.9, ls=":",
                color=col, zorder=4)
        ax.scatter([fb], [p["entry_price"]], marker=mk, s=70, color=col,
                   edgecolor="k", lw=.5, zorder=5)
        ax.scatter([pb], [p["price"]], marker="x", s=28, color=col, zorder=5)

    ax.set_yscale("log")
    datefmt(ax, bars)
    ax.set_ylabel("价格（对数轴，后复权）")
    ax.set_title(
        f"{CODE} 日线 2020-01~2026-10：引擎画出来的东西\n"
        f"× = 买卖点「发生」的位置　▲▼ = 引擎**第一次划出**它的位置（虚线是延迟）　"
        f"共 {len(pts)} 个点，其中 8 个 1买 全挤在 2022 年那一段",
        fontsize=11)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=.25, which="both")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


# --------------------------------------------------------------------------
# 图 2：延迟 + 偷看红利。「事后看图很准」和「当时能赚到」差多少
# --------------------------------------------------------------------------
def fig2(out: str):
    pts = load_points()
    bars = cache.load(CODE, Level.DAY)
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(15, 6.5),
                                   gridspec_kw={"width_ratios": [1.45, 1]})

    # 左：每个点一行，画「点发生 → 引擎第一次划出」
    for r, p in enumerate(pts):
        col = UP if p["side"] == "buy" else DOWN
        y = len(pts) - 1 - r
        axL.plot([p["bar"], p["first_seen_bar"]], [y, y], lw=3, color=col,
                 alpha=.35, solid_capstyle="butt")
        axL.scatter([p["bar"]], [y], marker="x", s=42, color=col, zorder=3)
        axL.scatter([p["first_seen_bar"]], [y], marker="o", s=42, color=col,
                    edgecolor="k", lw=.5, zorder=3)
        chg = p["entry_price"] / p["price"] - 1
        axL.text(p["first_seen_bar"] + 14, y,
                 f"延迟 {p['lag']} 根，价格已走 {chg:+.0%}",
                 va="center", fontsize=8.5,
                 color="#b30000" if p["lag"] > 20 else "#555")
    axL.set_yticks(range(len(pts)))
    axL.set_yticklabels([f"{p['label']} {p['ts']}" for p in reversed(pts)],
                        fontsize=8.5)
    datefmt(axL, bars, 160)
    axL.set_xlim(300, len(bars) + 420)
    axL.set_title("× 点「发生」　● 引擎第一次划出它\n"
                  "1买 次日就能看见；3类点迟到 77~189 根（4~9 个月）", fontsize=10.5)
    axL.grid(alpha=.25, axis="x")

    # 右：裸收益（拿点自己那天的价）vs 真收益（拿第一次看见那天的价）
    lab = [f"{p['label']} {p['ts'][:7]}" for p in pts]
    naive = [p["naive_rets"]["20"] for p in pts]
    real = [p["rets"]["20"] for p in pts]
    y = range(len(pts))
    axR.barh([v + .2 for v in y], naive, height=.38, color="#bbbbbb",
             label="裸收益：拿点自己那天的价（事后看图）")
    axR.barh([v - .2 for v in y], real, height=.38, color="#4a7ebb",
             label="真收益：拿第一次看见那天的价")
    axR.axvline(0, color="k", lw=.8)
    axR.set_yticks(list(y))
    axR.set_yticklabels(lab, fontsize=8.5)
    axR.invert_yaxis()
    axR.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    axR.set_title("持有 20 根的收益（已按信号方向取正负）\n"
                  "三个迟到的点：裸收益全正，真收益全负——**符号被延迟翻了**",
                  fontsize=10.5)
    axR.legend(fontsize=8.5, loc="lower right")
    axR.grid(alpha=.25, axis="x")

    fig.suptitle(f"{CODE}：为什么「事后看图很准」不算数", fontsize=12.5)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


# --------------------------------------------------------------------------
# 图 3：C-stale。中枢不动、价格跑了 5 倍，推演表还在印同一个触发价
# --------------------------------------------------------------------------
def fig3(out: str):
    bars = cache.load(CODE, Level.DAY)
    recs = [r for r in load_walk() if r.get("zs")]
    fig, (ax, axb) = plt.subplots(2, 1, figsize=(15, 8.5), sharex=True,
                                  gridspec_kw={"height_ratios": [2.3, 1]})

    ax.plot(range(len(bars)), [b.close for b in bars], lw=.9, color="#888",
            label="收盘价（后复权）")

    # 每个检查点当时「引擎认的最后一个中枢」。同一个 (zd,zg) 连成一段色带，
    # 带长 = 这个中枢被当成当下中枢用了多久
    runs: list[list] = []
    for r in recs:
        key = (round(r["zs"]["zd"], 3), round(r["zs"]["zg"], 3))
        if runs and runs[-1][0] == key:
            runs[-1][2] = r["i"]
        else:
            runs.append([key, r["i"], r["i"]])
    for (zd, zg), i0, i1 in runs:
        ax.add_patch(plt.Rectangle((i0, zd), i1 - i0, zg - zd, facecolor=ZSC,
                                   alpha=.6, edgecolor="#3182bd", lw=1))

    # 主角：[150, 204] 这个中枢，以及它那条从头到尾没动过的 3买 触发价
    main = max(runs, key=lambda r: r[2] - r[1])
    (zd, zg), i0, i1 = main
    ax.hlines(zg, i0, i1, color=STALE, lw=2.2, ls="--",
              label=f"3买 触发价 = ZG = {zg:.2f}（{bars[i0].ts} 起一直没变过）")
    ax.annotate(f"中枢 [{zd:.0f}, {zg:.0f}]", (i0, zd), fontsize=10,
                color="#08519c", va="top")

    # 挑几个检查点，把引擎当时印的话摊开
    marks = [r for r in recs if i0 <= r["i"] <= i1][::16]
    for r in marks:
        h = zg - zd
        over = (r["close"] - zg) / h
        if over <= 0:
            continue
        ax.scatter([r["i"]], [r["close"]], marker="o", s=34, color=STALE,
                   zorder=5)
        ax.annotate(f"{r['ts'][:7]}  收 {r['close']:.0f}\n高出中枢 {over:.1f} 倍枢高",
                    (r["i"], r["close"]), textcoords="offset points",
                    xytext=(-6, 12), fontsize=8, color=STALE, ha="right")

    ax.set_yscale("log")
    ax.set_ylabel("价格（对数轴）")
    ax.set_title(
        f"{CODE}：触发价会过期，而过期的和新鲜的印得一模一样（C-stale）\n"
        f"中枢 [{zd:.0f}, {zg:.0f}] 被当成「当下中枢」用了 {i1 - i0} 根K线；"
        f"期间收盘价从 {bars[i0].close:.0f} 涨到 {bars[i1].close:.0f}，"
        f"推演表始终在印「向上离开进行中，触发 ≥ {zg:.2f} 形成 3买」",
        fontsize=11)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=.25, which="both")

    # 下图：量化「过期程度」= 现价超出中枢多少个枢高
    xs = [r["i"] for r in recs]
    ys = []
    for r in recs:
        z = r["zs"]
        h = max(z["zg"] - z["zd"], 1e-9)
        ys.append((r["close"] - z["zg"]) / h if r["close"] > z["zg"]
                  else (-(z["zd"] - r["close"]) / h if r["close"] < z["zd"] else 0.0))
    axb.plot(xs, ys, lw=1.3, color=STALE, marker=".", ms=3)
    axb.axhline(0, color="k", lw=.8)
    axb.axhspan(-1, 1, color="#2ca02c", alpha=.12)
    axb.axhline(1, color="#2ca02c", lw=.8, ls=":")
    axb.text(xs[0], 1.15, "1 倍枢高以内：触发价还算新鲜（30 只票中位 0.35 倍）",
             fontsize=8.5, color="#2b7a2b")
    axb.set_ylabel("现价离中枢多远\n（单位：枢高）")
    axb.set_yscale("symlog", linthresh=2)
    datefmt(axb, bars, 120)
    axb.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out, "最远", f"{max(ys):.1f}", "倍枢高")


# --------------------------------------------------------------------------
# 图 4：分母的幻觉 + 读数会翻 + 短视野退化
# --------------------------------------------------------------------------
WHERE_Y = {"中枢下方": 0, "中枢区间内": 1, "中枢上方": 2}
OC = {"extend": "#4a7ebb", "break_up": "#d62728", "break_down": "#2ca02c",
      "unresolved": "#cccccc", "skipped_no_future": "#777777"}
OC_CN = {"extend": "延伸", "break_up": "向上离开", "break_down": "向下离开",
         "unresolved": "视野内没结果", "skipped_no_future": "没有未来数据"}


def fig4(out: str):
    import collections
    bars = cache.load(CODE, Level.DAY)
    recs = [r for r in load_walk() if r.get("zs")]

    cl: dict = collections.defaultdict(list)
    for r in recs:
        cl[(round(r["zs"]["zd"], 3), round(r["zs"]["zg"], 3))].append(r)

    fig = plt.figure(figsize=(15, 8))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1], width_ratios=[1.6, 1])
    axa = fig.add_subplot(gs[0, :])
    axb = fig.add_subplot(gs[1, 0])
    axc = fig.add_subplot(gs[1, 1])

    # (a) 87 个检查点 = 3 个中枢。y 是「现价在中枢哪一侧」，簇内会翻
    cols = ["#08519c", "#e6550d", "#31a354"]
    flips = tot = 0
    for n, ((zd, zg), v) in enumerate(cl.items()):
        xs = [r["i"] for r in v]
        ys = [WHERE_Y[r["where"]] for r in v]
        axa.plot(xs, ys, lw=1.1, marker="o", ms=4.5, color=cols[n % 3],
                 label=f"中枢[{zd:.0f},{zg:.0f}]：{len(v)} 个检查点")
        f = sum(1 for a, b in zip(v, v[1:]) if a["where"] != b["where"])
        flips += f; tot += len(v) - 1
        for a, b in zip(v, v[1:]):
            if a["where"] != b["where"]:
                axa.scatter([b["i"]], [WHERE_Y[b["where"]]], s=150,
                            facecolor="none", edgecolor=STALE, lw=1.8, zorder=5)
    axa.set_yticks([0, 1, 2])
    axa.set_yticklabels(["中枢下方", "中枢区间内", "中枢上方"], fontsize=9)
    axa.set_ylim(-.5, 2.6)
    datefmt(axa, bars, 120)
    axa.legend(loc="upper left", fontsize=9, ncol=3)
    axa.grid(alpha=.25)
    axa.set_title(
        f"(a) 分母的幻觉：{len(recs)} 个检查点看着像 {len(recs)} 个样本，"
        f"其实只有 {len(cl)} 个独立中枢——6.75 年一只票就这么多\n"
        f"　　紫圈 = 同一个中枢内读数翻面：{flips}/{tot} = {flips / tot:.1%}"
        f"（30 只票汇总 11.7%）。会这样翻的读数不是判断",
        fontsize=10.5, loc="left")

    # (b) 各视野的结局分布：短视野几乎没结果，有结果的里面绝大多数是「延伸」
    hs = ["60", "120", "240", "inf"]
    counts = [collections.Counter(r["outcomes"][h]["code"] for r in recs) for h in hs]
    order = ["extend", "break_up", "break_down", "unresolved", "skipped_no_future"]
    bottom = [0.0] * len(hs)
    for k in order:
        vals = [c.get(k, 0) for c in counts]
        axb.bar(hs, vals, bottom=bottom, color=OC[k], label=OC_CN[k], width=.62)
        bottom = [b + v for b, v in zip(bottom, vals)]
    axb.set_xlabel("视野（往后看几根K线）")
    axb.set_ylabel("检查点数")
    axb.legend(fontsize=8, ncol=2)
    axb.set_title("(b) 短视野上这题是退化的：h=60 时一多半压根没结果，\n"
                  "决出来的里面绝大多数是「延伸」", fontsize=10, loc="left")

    # (c) 恒猜（偷看标签的天花板）在各视野的命中率，只算决出来的
    bestp = []
    for c in counts:
        d = {k: v for k, v in c.items()
             if k not in ("unresolved", "skipped_no_future")}
        bestp.append(max(d.values()) / sum(d.values()) if d else 0)
    bars_ = axc.bar(hs, bestp, color="#999", width=.62)
    for b, c, p in zip(bars_, counts, bestp):
        d = {k: v for k, v in c.items()
             if k not in ("unresolved", "skipped_no_future")}
        axc.text(b.get_x() + b.get_width() / 2, p + .02,
                 f"{p:.0%}\n{OC_CN[max(d, key=d.get)]}\nn={sum(d.values())}",
                 ha="center", fontsize=8.5)
    axc.axhline(.5, color="k", lw=.8, ls=":")
    axc.set_ylim(0, 1.15)
    axc.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    axc.set_title("(c) 恒猜（偷看了标签，是天花板不是基准）\n"
                  "模型要有用，得先赢过这条灰柱", fontsize=10, loc="left")
    axc.set_xlabel("视野")

    fig.suptitle(f"{CODE}：为什么「命中率 xx%」这句话单独说没有意义", fontsize=12.5)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("写出", out)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "1"
    {"1": lambda: fig1("_plot/v300308_1_overview.png"),
     "2": lambda: fig2("_plot/v300308_2_lag.png"),
     "3": lambda: fig3("_plot/v300308_3_stale.png"),
     "4": lambda: fig4("_plot/v300308_4_denominator.png")}[which]()
