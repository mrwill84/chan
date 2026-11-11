"""``python -m backtest 300308`` —— 把一只票的历史重走一遍。

默认只读缓存，不联网（和 `scan` 同一套理由：拉数据是另一件事）。
"""

from __future__ import annotations

import argparse
import json
import sys

from chan.verify import MAX_TRIES
from feeds import Level, cache

from .score import HORIZONS, evaluate, summarize
from chan.segend import METHODS as SEGEND_METHODS

from .walk import walk_forward


def _pct(x: float | None) -> str:
    return "  --  " if x is None else f"{100 * x:+6.2f}%"


def render(code: str, res: dict, sightings: list, bars: list) -> str:
    L = [
        f"{code}  {bars[0].ts} ~ {bars[-1].ts}  共 {len(bars)} 根",
        f"走出买卖点 {len(sightings)} 个"
        f"（其中 {res['skipped_no_future']} 个太新、还没有未来数据，不计入统计）",
        "",
        "持有期基准（同区间任取一天进场）：",
    ]
    for h in res["horizons"]:
        b = res["base"]["buy"][h]
        L.append(f"  {h:>3} 根：做多胜率 {100 * b['win_rate']:.1f}%，"
                 f"平均 {_pct(b['mean_ret'])}")
    L += ["", "分组（edge = 本组 - 同方向基准，正数才叫有用）：", ""]
    L.append("类型   样本  平均延迟  存活  稳定   持有   胜率   edge    平均收益  edge     偷看红利")
    for name, g in res["groups"].items():
        for i, (h, row) in enumerate(sorted(g["h"].items())):
            head = (f"{name:<5} {g['n']:>4}  {g['mean_lag']:>6.1f}根  "
                    f"{g['survived']:>3}  {g['stable']:>3}  ") if i == 0 else " " * 32
            L.append(
                f"{head}{h:>5}  {100 * row['win_rate']:>5.1f}% "
                f"{100 * row['edge_win']:>+6.1f}%  {_pct(row['mean_ret'])} "
                f"{_pct(row['edge_ret'])}  {_pct(row['lookahead_premium'])}")
        L.append("")
    L += [
        "列的意思：",
        "  平均延迟  点发生 → 引擎第一次划出它，中间隔了几根K线。入场价用的是**后者**那天的收盘价。",
        "  存活      最终那一版划线里还在的个数。差额是被抹掉的点（C-prov 说的就是它们）。",
        "  稳定      从出现到最后一步没断过的个数。断过的当时根本不敢下手。",
        "  偷看红利  拿点自己那天的价进场 - 拿真实可见那天的价进场。**这个数越大，事后看图骗得越狠。**",
    ]
    return "\n".join(L)


def render_projection(r: dict) -> str:
    """把一个检查点的推演印成人能读的样子。

    格式是定死的：**先说当前是什么走势类型，再把后续选项连同触发价全列出来**。
    不印「看涨/看跌 + 置信度」——那种输出事后没法机械判定，也不是缠论能回答
    的问题。17课 p36 把走势类型穷尽为三类，24课「走势必完美」保证当前这段一定
    会落到其中一类，所以列表是完备的，事后恰好命中一项。
    """
    z, L = r["zs"], []
    L.append(f"== {r['ts']}  收 {r['close']:.2f} ==")
    L.append(f"走势类型：{r['trend']}（由 {r['run']} 个中枢构成），现价在{r['where']}")
    if r.get("relation"):
        L.append(f"最后两个中枢的关系：{r['relation']}（20课 p43 中心定理二）")
    if z:
        L.append(f"当前中枢 ZD {z['zd']:.3f} ~ ZG {z['zg']:.3f}"
                 f"（GG {z['gg']:.3f} DD {z['dd']:.3f}）"
                 f"  {r['zs_legs']}段  已离开={z['departed']}")
    for s in r["scenarios"]:
        L.append("")
        # 两个标记不互斥也不同义：★ 是位置读数，⚡ 是力度条件（只有 reversal
        # 会亮，而且它的 ★ 恒灭——见 Scenario.evidence_now）。
        mark = ("★正在走" if s["in_progress"]
                else "⚡力度到" if s.get("evidence_now") else "      ")
        L.append(f"[{mark}] {s['name']} → {s['becomes']}")
        t = s["trigger"]
        L.append(f"          触发：{t['what']}（{t['op']} {t['price']:.3f}）")
        if s.get("kills"):
            k = s["kills"]
            L.append(f"          证伪：{k['what']}（{k['op']} {k['price']:.3f}）")
        if s.get("forms"):
            at = f" @ {s['at']:.3f}" if s.get("at") else ""
            L.append(f"          形成：{s['forms']}{at}")
        # 「力度：」那行是**实测读数**不是待办事项，别套上「需要：」前缀。
        # 提示词那边（`agent.prompt_forward`）用同一个规则，两处要一致。
        L += [f"          {n}" if n.startswith("力度：")
              else f"          需要：{n}" for n in s["needs"]]
    for key, o in (r.get("outcomes") or {}).items():
        if key == "inf":
            L.append(f"事后实际（无界）：{o['code']} —— {o['detail']}")
        else:
            L.append(f"事后实际（{key} 根内）：{o['code']} —— {o['detail']}")
    return "\n".join(L)


def render_forward(s: dict) -> str:
    """推演汇总表。**独立枢那一列不能省**，它是读这张表第一个该看到的数。"""
    from .forward_walk import WHY_CN, sign_test

    rc = s["recluster"]
    L = [f"检查点 {s['n_checkpoints']} 个 → 独立中枢 {s['n_episodes']} 个"
         f"（宽松容差 {rc['tol_frac']:.1%} 下并成 {rc['loose']} 个"
         f"{'，口径没把同一个中枢切开' if rc['loose'] == rc['strict'] else '，n 虚报了'}）",
         ""]
    for g in rc["merged"]:
        L.append(f"  宽松口径下合并：{' / '.join(g)}")
    if rc["merged"]:
        L.append("")
    st = s["stability"]
    # 组内翻转**不随视野变化**（`in_progress` 是当下的位置读数，不看未来），
    # 所以它印在表外。逐行重复同一个数会让人以为它依赖视野。
    L += [f"组内翻转率 {_rate(st['in_progress_flip_rate'])}"
          f"（{st['n_pairs']} 个相邻对，和视野无关）；逐枢 "
          + " ".join("--" if x is None else f"{100 * x:.0f}%"
                     for x in st["per_episode"]),
          ""]
    L.append("视野   可判定枢  总枢  位置读数  恒猜(oracle)  偷看红利  "
             "unresolved  redrawn  skipped")
    for key, row in s["h"].items():
        e, ck = row["episode"], row["checkpoint"]
        cb = e["const_best"]
        L.append(
            f"{key:>5}  {e['n_scored']:>8}  {e['n_total']:>4}  "
            f"{_rate(e['in_progress_rate']):>8}  "
            f"{(cb['code'] + ' ' + _rate(cb['rate'])) if cb else '  --  ':>18}  "
            f"{_rate(e['lookahead_premium_ip']):>8}  "
            f"{ck['outcomes'].get('unresolved', 0):>10}  "
            f"{ck['outcomes'].get('redrawn', 0):>7}  "
            f"{ck['outcomes'].get('skipped_no_future', 0):>7}")
    # 背驰旁证印在表**外**。它不是一列指标——让它进表就迟早有人拿它算率，
    # 而它的分母（能判的那部分）和表里每一列的分母都不是一回事（D3）。
    L.append("")
    for key, row in s["h"].items():
        b = row["checkpoint"]["with_beichi"]
        n = b["yes"] + b["no"] + b["unknown"]
        if n:
            L.append(f"  旁证（视野 {key}，不进任何率）：{n} 次离开里，带背驰"
                     f"证据 {b['yes']}，明确没有 {b['no']}，"
                     f"判不了 {b['unknown']}")
            # 「判不了」按**本次实测**拆开。以前这里是一句写死的话，数字是旧
            # 运行留下的，归因还把 aAb 的方向问题说成了 a+A+b+B+c 的形态问题。
            for w, c in sorted(b.get("why", {}).items(), key=lambda kv: -kv[1]):
                L.append(f"      {c:>6}  {WHY_CN.get(w, w)}")
    L += [
        "",
        "怎么读这张表：",
        "  可判定枢  进分母的独立中枢数。**所有百分比的真分母是它**，不是检查点数。",
        "  位置读数  episode 首点的 `in_progress` 命中率。它不是预测，是「现价在中枢哪一侧」。",
        "  恒猜      本样本上最优的常数预测器。它偷看了标签，是**天花板**不是基准。",
        "  偷看红利  末点命中率 − 首点命中率。越大说明「越靠后越好猜」的成分越重（C-peek）。",
        "  组内翻转  同一个中枢内相邻检查点位置读数改变的比例。**它的 n 是几百**，"
        "是这张表上唯一有统计火力的数。",
        "",
        f"验收线：单票这个规模下，只有配对 Brier 差 9 胜或 10 胜（p ≤ "
        f"{sign_test(9, 10):.3f}）才算有证据；8 胜 p≈{sign_test(8, 10):.2f}，不算。",
    ]
    return "\n".join(L)


def render_rankings(sr: dict) -> str:
    """模型排序的打分表。**逐 episode 全印**，不只印均值。

    均值会被一个灾难 episode 整个吃掉，而 n=10 的时候「是哪一个翻的车」恰恰
    是最该看见的信息。
    """
    L = [f"== 模型排序打分（视野 {sr['key']}）==",
         f"可判定枢 {sr['n_scored']}，模型答了 {sr['n_answered']} 个", "",
         "   枢区间        结局          p(真值)  top1          Brier   均匀   序贯   位置读数"]
    for d in sr["rows"]:
        if not d["answered"]:
            L.append(f"  {d['zs']:>13}  {d['outcome']:<12}  —— 没有有效回答")
            continue
        L.append(
            f"  {d['zs']:>13}  {d['outcome']:<12}  {d['p_truth']:>6.3f}  "
            f"{str(d['top1']):<12}  {d['brier']:>5.3f}  "
            f"{d['brier_uniform']:>5.3f}  {d['brier_preq']:>5.3f}  "
            f"{str(d['in_progress'])}")
    L.append("")
    for name, label in (("uniform", "均匀地板"), ("preq", "序贯气候")):
        v = sr["vs"][name]
        L.append(f"对 {label}：{v['wins']}/{v['n_pairs']} 胜，符号检验 "
                 f"p={v['p']:.4f}，平均配对差 "
                 f"{(v['mean_diff'] or 0):+.4f}（正数=模型更好）")
    c = sr["concordant"]
    L += [
        "",
        f"平均 Brier {sr['mean_brier']:.4f}" if sr["mean_brier"] is not None
        else "平均 Brier ——",
        f"一致对（多数类 {c['code']}）：{c['rate']:.3f}（{c['n_pairs']} 对）"
        if c and c["rate"] is not None else "一致对：样本里只有一类，算不了",
        f"真值被压到 p<0.05 的有 {sr['p_truth_lt_05']} 个，"
        f"其中模型**根本没排**的 {sr['p_truth_zero']} 个",
        f"top-1 命中 {sr['top1_hits']}/{sr['n_answered']}"
        f"，其中与位置读数一致的 {sr['agree_in_progress']} 个",
        "",
        "注意：",
        "  top-1 **不是**准确率等级的证据。它丢掉全部概率（{.34,.33,.33} 和 "
        "{.99,.005,.005} 同分），",
        "       且 7/10 的 95% 区间约 [0.35,0.93]，和 5/10 几乎完全重叠。",
        "  与位置读数一致的比例若接近满分，这一层模型就只是在渲染一个布尔量。",
        "  序贯气候用的是**已揭晓**的前序 episode 类频率；无界那一列不成立"
        "（揭晓时刻不是检查点下标的函数），只能参考。",
    ]
    return "\n".join(L)


def render_health(wh: dict, dh: dict) -> str:
    """机器健康。**没查过要说没查过**，不能靠「0 个错误」暗示干净。

    这台机器会损坏长 Python 运行（见 chan/verify.py）。一趟 `--forward` 要跑
    几百上千次划线，是最容易安静出错的负载，而出错的表现可以只是某个中枢的
    边界差几分钱——不崩、不报错、直接进汇总表。所以这一段和汇总表一起印。
    """
    if not wh["checked"]:
        return ("机器自检：**没开**（--verify 1）。上面每一个数字都没有经过"
                "任何核对，这台机器会损坏长时间运行，见 chan/verify.py。")
    L = [f"机器自检：开（每次算到两遍一致，最多 {wh['tries']} 遍）",
         f"  检查点侧 {wh['n_checkpoints']} 个：重算过 {wh['recomputed']}，"
         f"凑不齐多数 {wh['unstable']}，输入K线被改 {wh['bars_tainted']}",
         f"  判定侧   {dh.get('n_divides', 0)} 次重划：重算过 "
         f"{dh.get('recomputed', 0)}，凑不齐多数 {dh.get('unstable', 0)}"]
    bad = (wh["unstable"] + wh["bars_tainted"]
           + dh.get("unstable", 0))
    rec = wh["recomputed"] + dh.get("recomputed", 0)
    if bad:
        L.append(f"  **有 {bad} 处凑不齐多数——上面的数字含不可信成分。**")
        if wh["unstable_ts"]:
            L.append(f"  出事的检查点：{', '.join(wh['unstable_ts'][:5])}"
                     + ("…" if len(wh["unstable_ts"]) > 5 else ""))
    elif rec:
        L.append(f"  这一趟真的翻过 {rec} 次位，但都被多数票挡住了。"
                 f"结果可信，机器不可信。")
    else:
        L.append("  这一趟没翻过位。")
    return "\n".join(L)


def _rate(x: float | None) -> str:
    return "  --  " if x is None else f"{100 * x:+5.1f}%" if x < 0 else f"{100 * x:5.1f}%"


def _pool_forward(a, level: Level) -> int:
    """`--pool`：跨票走查推演，印一份合并 summary。

    和单票那条路**只差分母**，别的一概相同——同一个 `summarize_projections`、
    同一张表、同一条 9~10 胜的验收线。所以这里不另造渲染，只在表前面加一行
    说清这些中枢来自几只票（否则 `n_episodes: 293` 看着像一只票走了十年）。

    `--rank` 在这条路上**不开**：`score_rankings` 按检查点下标 `i` 索引模型的
    回答，而跨票的 `i` 会撞（那边有断言）。模型侧要按票分开跑。
    """
    from .forward_walk import summarize_projections, walk_pooled

    with open(a.pool, encoding="utf-8") as f:
        codes = [ln.strip() for ln in f
                 if ln.strip() and not ln.startswith("#")]
    if not codes:
        print(f"{a.pool} 里一个代码都没有。", file=sys.stderr)
        return 1
    if a.rank:
        print("--rank 不能和 --pool 一起用：排序按检查点下标索引，跨票会串。",
              file=sys.stderr)
        return 1

    hs = tuple(int(x) for x in a.proj_horizons.split(","))

    def load(code, lv):
        return cache.load(code, lv, source=a.source)

    def on_step(n, total, code, info):
        if a.progress:
            tag = "（断点）" if info["cached"] else ""
            print(f"  [{n + 1}/{total}] {code}  "
                  f"{info['n_checkpoints']} 个检查点{tag}", file=sys.stderr)

    p = walk_pooled(codes, level, load, step=a.step, zs_from=a.zs,
                    horizons=hs, verify=a.verify,
                    checkpoint=a.pool_state, on_step=on_step)
    if not p["recs"]:
        print(f"{p['n_codes']} 只票一条检查点都没走出来"
              f"（失败 {p['n_failed']} 只）。", file=sys.stderr)
        return 1

    summary = summarize_projections(p["recs"], horizons=hs)
    print(f"跨票汇总：{p['n_ok']}/{p['n_codes']} 只票"
          f"（断点复用 {p['n_cached']} 只，失败 {p['n_failed']} 只），"
          f"{level.value}，{a.zs} 中枢")
    print()
    print(render_forward(summary))
    print()
    print(render_health(p["walk_health"], p["score_health"]))

    # 失败清单**要印**，而且印在表后面同一屏上。五千只里悄悄掉三百只，
    # 剩下那些的百分比看着和跑全了一模一样。
    if p["failed"]:
        print()
        print(f"失败 {p['n_failed']} 只：")
        for x in p["failed"][:20]:
            print(f"  {x['code']}  [{x['where']}]  {x['error'][:70]}")
        if p["n_failed"] > 20:
            print(f"  …还有 {p['n_failed'] - 20} 只")

    if a.forward != "-":
        with open(a.forward, "w", encoding="utf-8") as f:
            for r in p["recs"]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n逐点明细写到了 {a.forward}（{len(p['recs'])} 个检查点）",
              file=sys.stderr)
    return 0


def main(argv=None) -> int:
    # stderr 也要改。只改 stdout 的话进度和提示会在 Windows 控制台上变乱码，
    # 而那些行恰恰是出错时唯一能看的东西。
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m backtest", description="单只股票的缠论走查回测")
    # `--pool` 那条路没有「这只票」，所以代码位是可选的。两者必须给且只给
    # 一个：都不给无从下手，都给则「印哪一只的推演」这个问题没有答案。
    ap.add_argument("code", nargs="?")
    ap.add_argument("--level", default="day", choices=[x.value for x in Level])
    # 默认 seg，和 scan / multilevel / plot 一致（C-zslevel）。原先是 bi，
    # 后果是回测用的中枢和盘后扫描、人工验收那张图不是同一批。
    ap.add_argument("--zs", default="seg", choices=["seg", "bi"])
    ap.add_argument("--forward", metavar="JSONL", nargs="?", const="-",
                    help="改跑**走势类型滚动推演**：每 --step 根推一次后续"
                         "选项表，再用最终划分判实际走了哪支。给路径就写 JSONL。"
                         "这和买卖点回测是两个问题，不会同时跑。")
    ap.add_argument("--proj-horizons", default="120,240,480",
                    help="推演的判定视野（K线根数）。**不是越大越好也不是越小"
                         "越好**：视野越短恒猜基准越高、越难证明 skill，视野越"
                         "长类别越均衡但 skipped 越多。先用 --resolution-time "
                         "看揭晓时间分布，让这几个数横跨**第二行（episode首点）"
                         "**——首点比检查点平均难判得多，照第一行选会选短。")
    ap.add_argument("--resolution-time", action="store_true",
                    help="只读诊断：印出「往后多少根才判得出来」的分位数")
    ap.add_argument("--pool", metavar="FILE",
                    help="**跨票汇总推演**（只对 --forward 有效）：从这个文件"
                         "读代码表（一行一只，# 开头是注释），逐票走查打分再"
                         "合并成一份 summary。单票的独立中枢只有个位数，验收线"
                         "要 9~10 胜，跨票是唯一能让这张表说话的办法。"
                         "给了它就不要再给代码位。")
    ap.add_argument("--pool-state", metavar="DIR",
                    help="--pool 的断点目录，一票一个 JSON，跑完一只写一只。"
                         "参数或K线变了会自动失效重算。几十只以上**务必给**"
                         "——这台机器会在长运行里崩（见 chan/verify.py）。")
    ap.add_argument("--rank", metavar="JSONL",
                    help="让本地模型给**每个 episode 首点**的选项表排序，"
                         "逐条追加到这个文件、可中断续跑。只问首点，所以是"
                         "十来次调用而不是几百次（见 C-episode）。")
    ap.add_argument("--rank-horizon", default="480",
                    help="用哪个视野的结局给排序打分。要在 --proj-horizons "
                         "里。**取可判定枢最多、恒猜最低的那一档**，别取最短"
                         "的——600009 30分钟实测 H=120 恒猜 100%%、H=480 恒猜"
                         " 60%%，短视野下那个基准几乎不可能被超过，用它只会"
                         "得到「模型没用」的假阴性。")
    ap.add_argument("--source", default=cache.DEFAULT_SOURCE)
    ap.add_argument("--no-quasi", action="store_true", help="不计类买卖点")
    ap.add_argument("--horizons", default=",".join(map(str, HORIZONS)))
    ap.add_argument("--step", type=int, default=1, help=">1 是抽样走查，会把延迟量化")
    ap.add_argument("--seg-end", default="none",
                    # sublevel_1bs 要逐步喂次级别数据，只能走 backtest.sublevel
                    choices=["none", *(m for m in SEGEND_METHODS
                                      if m != "sublevel_1bs")],
                    help="「当下判断末段已结束」的口径（chan.segend）。"
                         "none=只认 67课 特征序列，是历史基线")
    ap.add_argument("--seg-end-ma-filter", action="store_true",
                    help="叠加106课最低延伸负向过滤（18根/MA21），不单独造信号")
    # 和 `scan --verify` 同一个默认值、同一个含义。这台机器会在长 Python 运行
    # 里损坏内存，而 --forward 恰恰是跑最久的那条路；关掉等于那趟的每个百分比
    # 都没人核对过。代价是一倍 CPU。
    ap.add_argument("--verify", type=int, default=MAX_TRIES, metavar="N",
                    help="每次划线算几遍取多数，防硬件位翻（1 = 关掉）。"
                         "默认开，见 chan/verify.py。")
    ap.add_argument("--json", help="把逐个点的明细写到这个文件")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--agent", metavar="JSONL",
                    help="让本地模型把每个点重判一遍，逐条追加到这个文件。"
                         "**很慢**（一个点一分钟上下），但可以中断续跑。")
    ap.add_argument("--model", help="交给 pi 的 --model")
    ap.add_argument("--blind", action="store_true",
                    help="对照组：把买卖点整段蒙住，只给中枢和位置。"
                         "用来分辨模型是在判断还是在复述标签。"
                         "**结果要写到另一个 --agent 文件**，别和带标签的混在一起。")
    a = ap.parse_args(argv)

    level = Level(a.level)

    if a.pool:
        if a.code:
            print("--pool 和代码位只能给一个。", file=sys.stderr)
            return 1
        if not a.forward:
            print("--pool 只对 --forward 有效（别的回测还是单票的）。",
                  file=sys.stderr)
            return 1
        return _pool_forward(a, level)

    if not a.code:
        print("要给一个股票代码，或者用 --pool FILE 跨票跑。", file=sys.stderr)
        return 1
    bars = cache.load(a.code, level, source=a.source)
    if not bars:
        print(f"{a.source} 的缓存里没有 {a.code} 的{a.level}K线。"
              f"先 `python -m scan --source {a.source} pull {a.code}`。",
              file=sys.stderr)
        return 1

    if a.forward:
        from .forward_walk import (
            resolution_time,
            score_projections,
            summarize_projections,
            walk_health,
            walk_projections,
        )

        hs = tuple(int(x) for x in a.proj_horizons.split(","))
        recs = walk_projections(bars, a.code, level, step=a.step,
                                zs_from=a.zs, verify=a.verify)
        if a.resolution_time:
            # 挑视野用的只读诊断：`--proj-horizons` 要横跨这几个分位数。
            # **两个分布都要印。** 打分只用 episode 首点，而首点是中枢刚长出来
            # 那一刻、系统性地最难判，它的揭晓时间比检查点平均长得多。600009
            # 30分钟上检查点中位 93 根，按这个选视野会以为 48 够用，实际 H=48
            # 下 10 个 episode 里只有 1 个判得出来。选视野要看下面那一行。
            from .forward_walk import episodes
            for tag, rs in (("全检查点", recs),
                            ("episode首点", [e["first"] for e in episodes(recs)])):
                print(f"揭晓时间（{tag}）：" + json.dumps(
                    resolution_time(rs, bars, zs_from=a.zs),
                    ensure_ascii=False), file=sys.stderr)
        dh: dict = {}
        score_projections(recs, bars, horizons=hs, zs_from=a.zs,
                          verify=a.verify, health_out=dh)
        summary = summarize_projections(recs, horizons=hs)
        print(render_forward(summary))
        # 机器健康印在表**后面**、每次都印。它不是可选的脚注：没开校验时这张
        # 表的每个数都没人核对过，那句话必须和数字出现在同一屏上。
        print()
        print(render_health(walk_health(recs, a.verify), dh))

        if a.rank:
            from pathlib import Path

            from .forward_replay import replay_rankings
            from .forward_walk import score_rankings

            if a.rank_horizon not in summary["h"]:
                print(f"--rank-horizon {a.rank_horizon} 不在 "
                      f"--proj-horizons {a.proj_horizons} 里", file=sys.stderr)
                return 1

            def step(n, total, r, cached):
                tag = "（已有）" if cached else f"{r.seconds:.0f}s"
                d = (",".join(f"{k}={v:.2f}" for k, v in r.probs.items())
                     if r.ok else f"失败：{r.error[:40]}")
                print(f"  [{n + 1}/{total}] {r.ts} {r.zs} → {d}  {tag}",
                      file=sys.stderr)

            ans = replay_rankings(recs, bars, a.code, level,
                                  out=Path(a.rank), zs_from=a.zs,
                                  model=a.model, on_step=step, blind=a.blind)
            print()
            print(("【对照组：背驰数值已蒙住】\n" if a.blind else "")
                  + render_rankings(score_rankings(summary, ans,
                                                   key=a.rank_horizon)))

        print()
        print(render_projection(recs[-1]) if recs else "没有可推演的检查点")
        if a.forward != "-":
            with open(a.forward, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"\n逐点明细写到了 {a.forward}（{len(recs)} 个检查点）",
                  file=sys.stderr)
        return 0

    def prog(t, n, k):
        if t % 100 == 0:
            print(f"  走到 {t}/{n}，已见 {k} 个点", file=sys.stderr)

    sightings = walk_forward(bars, a.code, level, zs_from=a.zs,
                             include_quasi=not a.no_quasi, step=a.step,
                             seg_end=None if a.seg_end == "none" else a.seg_end,
                             seg_end_ma_filter=a.seg_end_ma_filter,
                             verify=a.verify,
                             on_progress=prog if a.progress else None)
    horizons = tuple(int(x) for x in a.horizons.split(","))
    outcomes = evaluate(sightings, bars, horizons=horizons)
    res = summarize(outcomes, bars, horizons=horizons)
    print(render(a.code, res, sightings, bars))

    if a.agent:
        from pathlib import Path

        from .replay import render_replay, replay, summarize_replay

        def step(i, n, r, cached):
            tag = "（已有）" if cached else f"{r.seconds:.0f}s"
            d = r.direction if r.ok else f"解析失败：{r.error[:40]}"
            print(f"  [{i + 1}/{n}] {r.label} {r.ts} → {d}  {tag}",
                  file=sys.stderr)

        rs = replay(outcomes, bars, a.code, level, out=Path(a.agent),
                    zs_from=a.zs, model=a.model, horizons=horizons,
                    on_step=step, blind=a.blind)
        print(("\n【对照组：买卖点已蒙住】" if a.blind else "") + "\n"
              + render_replay(
                  summarize_replay(rs, outcomes, bars, horizons=horizons)))

    if a.json:
        detail = [{
            "label": o.sighting.label, "kind": o.sighting.kind,
            "side": o.sighting.side, "ts": o.sighting.ts,
            "bar": o.sighting.bar, "price": o.sighting.price,
            "first_seen_bar": o.entry_bar, "first_seen_ts": bars[o.entry_bar].ts,
            "lag": o.sighting.lag, "entry_price": o.entry_price,
            "quasi_at_first": o.sighting.quasi_at_first,
            "confirmed_at_first": o.sighting.confirmed_at_first,
            "trend_at_first": o.sighting.trend_at_first,
            "survived": o.sighting.survived, "stable": o.sighting.stable,
            "gone_times": o.sighting.gone_times,
            "rets": o.rets, "naive_rets": o.naive_rets,
            "mfe": o.mfe, "mae": o.mae, "enough_future": o.enough_future,
            "reasons": o.sighting.reasons_at_first,
        } for o in outcomes]
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"code": a.code, "level": a.level, "zs_from": a.zs,
                       "source": a.source, "summary": res, "points": detail},
                      f, ensure_ascii=False, indent=2)
        print(f"\n明细写到了 {a.json}（{len(detail)} 个点）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
