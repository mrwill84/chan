"""滚动推演：每隔 N 根K线站在当时的位置推一次后市，再回头判它走没走中。

这是 `prompt_forward` 的**引擎侧对照组**。那边把完备选项表交给模型排序，
这边记录同一批检查点上引擎自己说了什么、事后实际走了哪一支。两份对上，
模型的排序才有分母（`parse_ranking` 解析出来的名次拿这里的 `outcome` 判）。

和同目录另外两个 walk 的分工：

- `walk.py` 问**买卖点什么时候才看得见**（repaint lag、存活率）
- `score.py` 问**看见之后价格怎么走**（持有收益 vs 基准）
- 这里问**走势类型会演化成哪一类**（17课 p36 的三分类 + 24课「走势必完美」）

前两个的答案是收益数字，这个的答案是**类型**，所以不需要基准收益率，需要的
是 `chan.forward.resolve` 那套机械判定。

### 唯一的硬规矩：不能偷看

每个检查点都 `analyze_one(bars[:i+1])` 重新划一遍，**不能**拿全量划完的结果
去切片。笔和线段都会被后续K线回退重划（69课 p177 的笔回退、78课 p197 的线段
破坏），全量结果里第 i 根处的那条线用的是第 i 根**之后**的信息，拿它当「当时
的判断」去打分，分数全是假的。

重划成本实测：5384 根 30 分钟K线 60ms，1000 根 3ms。全历史每日推一遍不到半
分钟，没有理由为省这点时间去冒偷看的风险。

### 三种不计入分母的结果

- ``unresolved``：后面的K线还不够走出结论。最近那批检查点必然是这个。
- ``redrawn``：当时那个中枢在更长的K线上被重划掉了（C-redraw）。这是我方
  明确接受的代价，它说明**当时那个中枢本身不牢**，不是推演错了。
- ``no_zhongshu``：当时还没走出中枢，无可推演。

### 这里**不**排序

`project` 给的是完备选项表，哪支概率大是模型的事（全项目一贯分工：几何归
程序，研判归模型）。`in_progress` 看着像引擎的预测，**它不是**——它只说现价
在中枢的哪一侧，是个位置读数。拿它当预测去算命中率会得到一个漂亮但无意义
的数字，`summarize` 里特意把它单列并在文档里标注，见那里的注释。
"""

from __future__ import annotations

import json
import math
import os
import statistics
from collections.abc import Callable, Iterable
from pathlib import Path

from chan import analyze, macd
from chan.beichi import (
    NO_ENTER,
    NO_LEAVE,
    OPPOSITE,
    OUT_OF_RANGE,
    detect_at,
    reach_reason,
)
from chan.forward import resolve, zs_key
from chan.types import Bar, Direction, ZhongShu
from chan.verify import bars_digest, digest, stable
from chan.zhongshu import TREND_DOWN, TREND_UP, build_zhongshus, legs_from_bis, legs_from_segs
from feeds import Level
from scan.report import MIN_BARS, analyze_one, analyze_one_checked

#: 判定视野（K线根数）。30 分钟上 = 15 / 30 / 60 交易日。
#:
#: 选法不是拍脑袋，是**横跨 episode 首点的揭晓时间分布**（`resolution_time`
#: 印出来的第二行）。600009 30分钟，step=8，2026-10-20 重测：
#:
#:     全检查点     n=636  p10= 29  中位= 93  p90=317  max=725
#:     episode首点  n=  6  p10= 37  中位=133  p90=285  max=285
#:
#: 上一版这里记的是 `全检查点 p90=261 max=461 / episode首点 p10=93 中位=141
#: p90=461 max=461、10 个 episode`。差别来自走势层重写（中枢序列本身变了），
#: 不是这次的走势类型口径换树——那次换口径在这条序列的 641 个检查点上和
#: `trailing_trend` **100% 一致**，一个检查点都没移动。
#:
#: **必须看第二行。** 打分只用首点，而首点是中枢刚长出来那一刻、系统性地最难
#: 判，整条分布比检查点平均右移一倍。按第一行选会以为 48 根够用（它在检查点
#: 中位之下不远），旧序列上实测 H=48 下 10 个 episode 只判出 **1** 个，434 个
#: 检查点是 `unresolved`——那一列什么也量不了。（这组 H=48 的数和上面那组
#: `p90=461` 同属重写前的中枢序列，没有在新序列上复测；留着是因为它说明的是
#: 「按第一行选视野会选瞎」这个方法论的坑，不是 600009 今天的读数。）
#:
#: 于是 120（≈首点 p10~中位）/ 240（≈中位~p90）/ 480（刚过 max，全判得出）。
#: 重测之后这三个数仍然站得住：实测 可判定枢 4/6、5/6、6/6，正好把分布铺开。
#: 换级别或换票都要重测：日线的揭晓时间分布和这个完全不是一回事。
#:
#: **分母才是这套东西现在真正的瓶颈，不是视野。** 667 个检查点折叠下来只有
#: **6 个独立中枢**，而 `summarize_projections` 自己印的验收线要 9~10 胜才算
#: 证据。单票上这张表测不出任何东西，必须跨票汇总——缓存里 30 只日线合计
#: 293 个中枢，够用。跨票走 `walk_pooled`：`score_projections` 仍然绑死一只票
#: 的 `bars`（判定要拿**那只票**的未来K线重划），所以合并发生在它之后。
PROJ_HORIZONS = (120, 240, 480)

#: 无界判定在报表里的键。**它是参考列，不是主口径**——无界判定下早期检查点
#: 有几千根未来、最近的一根都没有，幸存下来的样本系统性偏向「老到能看出结果」
#: 的那批（C-nofuture 在 episode 层面的同一个洞）。
UNBOUNDED = "inf"


def walk_projections(
    bars: list[Bar], code: str, level: Level, *,
    step: int = 8, warmup: int = 0, zs_from: str = "seg",
    verify: int = 1,
) -> list[dict]:
    """每 ``step`` 根推一次，返回逐检查点的记录。

    ``warmup`` 是第一个检查点的K线根数下限，0 表示用 `scan.report.MIN_BARS`。
    中枢要两段、线段要三笔、笔要五根合并K线，一路乘下来几十根K线连一个中枢
    都长不出来，推演全是 `还没走出中枢`。

    ``verify`` 是每个检查点算几遍（`chan.verify.stable`，1 = 关掉）。**这一层
    不是防 bug，是防硬件**：这台机器会在长 Python 运行里损坏内存，而本函数正是
    最危险的那种负载——几百上千次 `analyze_one`，一次跑几分钟。崩掉的那些看得
    见，翻在一个价格浮点上的位不会抛异常，只会让某个检查点的中枢边界错几分钱，
    然后安静地进汇总表。默认 1 是因为库层不该替调用方把开销翻倍；CLI 那边默认
    开（`--verify`），和 `scan` 一致。

    账记在每条记录的 ``verify`` 键上，**只在不正常时挂**（和 `scan.scanner`
    同一条规矩：每条都印 `"ok"` 等于没印）。汇总用 `walk_health`。
    """
    warmup = max(warmup or MIN_BARS, step)
    out: list[dict] = []
    # 末根必须是检查点。`range` 按步长走，终点落在哪根看余数——5384 根步长 8
    # 停在第 5377 根，最后 7 根（当天下午）被整段丢掉。而「此刻该怎么看」正是
    # 这套东西唯一真正要用的那一条推演，不能因为除不尽就没有。
    stops = sorted({*range(warmup, len(bars) + 1, step), len(bars)})
    for i in stops:
        if i < warmup:
            continue
        # 切片**必须在计时之外先做好**：`analyze_one_checked` 要把同一个列表
        # 对象算两遍，每遍重新切一次等于两遍的输入不是同一份，`bars_digest`
        # 的前后对比就白做了。
        window = bars[:i]
        r = analyze_one_checked(window, code, level, tries=verify,
                                zs_from=zs_from, include_quasi=True,
                                recent=None)
        if "error" in r:
            continue
        f = r["forward"]
        v = r.get("verify") or {}
        # 只有「真的翻过位」才留痕：runs>2 说明重算过，ok=False 说明凑不齐
        # 多数，bars_stable=False 说明损坏落在输入上（那是最糟的一种）。
        dirty = (v.get("runs", 1) > 2 or not v.get("ok", True)
                 or not v.get("bars_stable", True))
        out.append({
            "i": i - 1,
            # 票号要跟着每条记录走，哪怕单票跑的时候它是常数。汇总层
            # （`episodes` / `recluster`）按「中枢身份」折叠，而中枢身份是
            # 一对浮点 ZG/ZD——**跨票是会撞的**，不同的票在同一个价位区间
            # 震荡既不稀奇也不说明它们是同一个结构。没有这个字段，跨票汇总
            # 只能靠调用方在外面自己拼，那就等于每个调用点各写一遍折叠逻辑。
            "code": code,
            **({"verify": {"runs": v.get("runs"), "ok": v.get("ok"),
                           "bars_stable": v.get("bars_stable"),
                           "digests": v.get("digests")}} if dirty else {}),
            "ts": bars[i - 1].ts,
            "close": bars[i - 1].close,
            # 当时那个中枢的 ZG/ZD，打分时靠它认同一性（`forward._same`）。
            # `zs_legs` 也必须存：`resolve` 判「延伸」比的就是段数有没有变多。
            "zs": f["zhongshu"],
            "zs_legs": (r["zhongshus"][-1]["legs"] if r.get("zhongshus")
                        else 0),
            "trend": f["trend"],
            "run": f["run"],
            # 最后两个中枢的关系（20课 p43）。只有一个中枢时是 None。
            "relation": f.get("relation"),
            "where": f.get("where", ""),
            "scenarios": f["scenarios"],
        })
    return out


def walk_health(recs: list[dict], tries: int) -> dict:
    """这一趟走查里机器翻了几个位。``tries<2`` 时 ``checked=False``。

    **`recomputed=0` 和「没查」必须分得开。** 两者在报表上都是一片安静，但
    前者是「查过了，干净」，后者是「这些数字没有任何人核对过」——后面那条
    汇总表的每一个百分比都建立在它上面，混成一个数等于把最关键的免责声明
    藏起来。所以这里永远返回 ``checked``，不靠 0 去暗示。
    """
    bad = [r for r in recs if r.get("verify")]
    unstable = [r for r in bad if not r["verify"].get("ok", True)]
    tainted = [r for r in bad if not r["verify"].get("bars_stable", True)]
    return {
        "checked": tries >= 2,
        "tries": tries,
        "n_checkpoints": len(recs),
        # 重算过但凑齐了多数：结果可信，机器不可信。
        "recomputed": len(bad) - len(unstable),
        # 凑不齐多数：**这些检查点的数字不能用**。
        "unstable": len(unstable),
        # 损坏落在输入K线上，`stable` 的盲区被 `bars_digest` 抓到了。
        "bars_tainted": len(tainted),
        "unstable_ts": [r["ts"] for r in unstable][:20],
    }


def _divide(bars: list[Bar], zs_from: str) -> list[ZhongShu]:
    """把一段K线划成中枢序列。和 `scan.report.analyze_one` 走同一条路。

    两边必须一致：检查点那侧的中枢来自 `analyze_one`，判定这侧来自这里，
    口径岔开的话「同一个中枢」就认不出来，`resolve` 会全判成 `redrawn`。
    """
    a = analyze(bars, check=False)
    legs = (legs_from_bis(a.bis) if zs_from == "bi"
            else legs_from_segs(a.segs, a.bis))
    return build_zhongshus(legs)


def _annotate_beichi(recs: list[dict], bars: list[Bar], zs_from: str) -> None:
    """给已判成 ``break_*`` 的结局补一句「那一下离开到底有没有背驰」。

    **这是旁证，永远不进主指标**（D3）。三个理由，一个比一个靠前：

    1. **循环论证。** `_reversal` 的 `evidence_now` 用的就是 `detect`。让它
       同时参与定标签，「引擎说背驰」和「判定说背驰」就成了同一次计算，命中
       率自动变高——C-blind 记过的标签锚定，和当年「提示词里写着 3买、模型
       说 up 只是在复述」是同一个形状。
    2. **把 ground truth 变成可调参数。** `hit = area_ok or dif_ok`，而
       `area_ok` 由 `AREA_RATIO` 决定。让它定标签，意味着以后任何一次背驰
       阈值调优都会**追溯性改写历史得分**。`resolve` 现在的判据全部来自划分
       结果（ZG/ZD、段数、`relation`），那些不是旋钮。
    3. **会静默重定义另外两类。** 一旦「伴随背驰的向上离开」另算一类，
       `break_up` 就悄悄变成「**不**伴随背驰的向上离开」——模型按一张表
       作答，却按另一张表被判分。

    取值 ``True`` / ``False`` / ``None``（判不了），``None`` 的原因记在同一个
    结局里的 ``beichi_why``，代号见 `chan.beichi` 和 `ZS_GONE`。

    **不要把构成写死在这里。** 这段原先列着一组固定的细分数字，报表也照着印
    了一句写死的「绝大多数是方向相反」——两边都在走势层重写之后变了却没人
    发现，而且那句话的归因框架本身是错的：它把 **aAb 层面**的进入/离开方向
    不符说成了「不是 a+A+b+B+c 的形态」。a+A+b+B+c 是 `_gate_trend` 的事，
    它只决定这次背驰叫趋势背驰还是盘整背驰，和够不够得着两段无关。
    现在 `_beichi_tally` 按**每次运行实测**出 ``why``，报表直接印它。

    **``None`` 占多数是结构事实不是 bug**，这一条仍然成立：实测（600009
    30分钟，笔级别，5 个截断点，217 个中枢）方向相反占 56%，取不到进入段
    占 2%，中枢认不出来占 0%。
    其中「方向相反」**不该读成盲区**：它是「这个中枢是一次折返的拐点」，
    本身就是个有用的读数——而那正是该升一级去看的信号。

    下面这段历史保留，它解释了另外三类为什么现在是 0：

    * ``c 段空区间`` 原先 180 个，来自 `resolve` 把中枢截断误判成「离开」；
    * ``b 段空区间（k>0）`` 原先 86 个，同一个病灶发生在前一个位置上——
      **前一个**中枢是段数上限截出来的，与本中枢之间没有连接段。

    `zhongshu_at` 改成「不切断、只置 `level_up`」之后两类同时归零（见
    `chan.invariants.check_zhongshus`）。剩下的 35 个 k=0 是真正无解的：
    第一个中枢前面本来就没有进入段。

    **另一半的变化更要紧：能判的那部分从「恒真」变成了真假各半。**
    更早以前是 58 真、0 假——那时这一列在 ``break_*`` 条件下区分度≈0，只能
    回答「这次离开测得出力度吗」。现在它接近五五开，是一个真有信息的旁证了。
    （它仍然不进主指标，理由见上面三条，和能不能区分无关。）

    **这一列在 ``--zs seg`` 下会整列为 0，别据此说「这只票没有背驰」。**
    600009 30分钟实测：seg 口径同时只活着五六个中枢，`detect_at` 够得着 4 个、
    一个都没判出背驰；换 ``--zs bi`` 之后同一条序列 112 真 / 175 假。
    口径决定了这一列有没有样本。

    122 个 ``b 段与 c 段方向相反`` 查过了，**不是 bug，是信息**。逐个画出
    aAbBc 核对（`_plot/abc.py`，600009 30分钟最终划分里 7 个中枢有 4 个是这
    一类）：b 确实是把价格带进 B 的那一段，c 确实是带出去的那一段，挑段没挑
    错；它们方向相反是因为 **B 是一次折返的拐点，不是一条链上的一环**。实例：

        中枢0 82.38~87.36  ──b↑──  中枢1 90.47~93.53  ──c↓──  中枢2 83.29~84.98

    上去又下来，这不是趋势。而背驰按定义就是**比较同向的两推**（24课 p53 的
    盘整背驰比 a 与 b，37课 p96 的趋势背驰比 b 与 c，两边都要求同向），所以
    `beichi.detect_at` 在 b/c 反向时返回 None 是对的。

    要紧的是**别把它读成「判不了」**：它不是「这次离开测不出力度」，而是
    「这里根本没有力度可比」。``why`` 现在就是干这个的——报表把 ``unknown``
    按原因逐行拆开印，方向相反那一类从「盲区」变成「当下处在震荡折返点」
    这个有用的读数，而那正是该升一级去看的信号。

    代价：按终点下标分组，每个终点**只划一次就丢**。不进 `score_projections`
    的 memo——那个 memo 只留 `zss`，这里要 legs/bis/macd 全套，几百份留在内存
    里是上百兆，而这台机器本来就有内存问题。换来的是多跑一遍 `analyze`。
    """
    need: dict[int, list[tuple[dict, str]]] = {}
    for r in recs:
        if r["zs"] is None:
            continue
        for key, o in r["outcomes"].items():
            if not o["code"].startswith("break_"):
                continue
            j = len(bars) if key == UNBOUNDED else r["i"] + 1 + int(key)
            need.setdefault(j, []).append((r, key))

    for j, items in need.items():
        a = analyze(bars[:j], check=False)
        legs = (legs_from_bis(a.bis) if zs_from == "bi"
                else legs_from_segs(a.segs, a.bis))
        zss = build_zhongshus(legs)
        m = macd([b.close for b in bars[:j]])
        idx = {zs_key(z): k for k, z in enumerate(zss)}
        for r, key in items:
            k = idx.get(zs_key(r["zs"]))
            o = r["outcomes"][key]
            if k is None:
                o["with_beichi"], o["beichi_why"] = None, ZS_GONE
                continue
            b = detect_at(legs, zss, a.bis, m, k)
            o["with_beichi"] = None if b is None else bool(b.hit and b.new_extreme)
            # 原因必须**本次运行真的算一遍**。上一版这里什么都不记，报表只好
            # 印一句写死的「绝大多数是方向相反」——那句话的数字是旧运行留下
            # 的，而且归因的框架也是错的（说成 a+A+b+B+c，实为 aAb 方向不符）。
            o["beichi_why"] = None if b is not None else reach_reason(
                legs, zss, a.bis, k)


def _shell(r: dict) -> ZhongShu:
    """把记录里存的 ZG/ZD 还原成能喂给 `resolve` 的轻量壳子。

    `resolve` 只用 ZG/ZD 认同一性、用 `leg_count` 看有没有延伸，不需要完整的
    `ZhongShu`。

    **`leg_count` 必须还原成当时的真值**，用 ``start_leg=0, end_leg=n-1`` 凑。
    偷懒写成 ``end_leg=0``（段数恒为 1）的后果是：末尾中枢后面没有新中枢时，
    `resolve` 走「比段数」那条分支，1 恒小于任何真实段数，于是**所有停在末尾
    中枢上的检查点全部白送一个 `extend`**。600009 的 30 分钟上这一口吃掉 74
    条记录（292 条里的 25%），`unresolved` 被抹成 0——而最近这批本来就该是
    `unresolved`，那正是「后面K线还不够下结论」的诚实回答。
    """
    z = r["zs"]
    n = max(r.get("zs_legs") or 1, 1)
    return ZhongShu(index=-1, direction=Direction.UP, start_leg=0,
                    end_leg=n - 1, zg=z["zg"], zd=z["zd"],
                    gg=z["gg"], dd=z["dd"], g=z["zg"], d=z["zd"])


def score_projections(recs: list[dict], bars: list[Bar], *,
                      horizons=PROJ_HORIZONS, zs_from: str = "seg",
                      unbounded: bool = True,
                      beichi: bool = True,
                      verify: int = 1,
                      health_out: dict | None = None) -> list[dict]:
    """判每条记录在**每个视野**下实际走了哪一支，就地写回 ``outcomes``。

    复数。原先是单数 `outcome`，对**全量最终划分**判一次——那问的是「这个
    中枢最终会变成什么」，实测中位 261 根、最大 872 根（109 个交易日）之后
    才揭晓，而选项表想回答的是「接下来」。两个不是同一个问题，混在一起量，
    换个视野就能把结论翻过来。

    每个视野 ``h`` 用 ``bars[:i+1+h]`` 重划一遍判定。**不能切全量划分的片**
    ——那就是偷看，理由见模块开头。重划结果按终点下标记忆化，几百个检查点
    乘几个视野去重后只剩几百次 `analyze`（实测 60ms/次）。

    ``i+1+h`` 越过末尾的记录记 ``skipped_no_future``，**不进分母**。不要图省
    事写成 ``min(i+1+h, len(bars))``：那会把末尾那批检查点的视野悄悄缩短，
    让它们看起来「判出来了」，而它们恰恰是最该说「还不知道」的那批。这是
    C-nofuture（`score.py:75,101-108`）在分类任务上的对应物。

    ``beichi`` 给每个 ``break_*`` 结局补一个 `with_beichi` 旁证（见
    `_annotate_beichi`）。**它不进任何指标**，只是让「这些离开里有几次是带
    背驰的」这个问题事后查得出来。关掉能省掉一遍 `analyze`。

    ``verify`` 同 `walk_projections`：判定侧的重划**和检查点侧一样会被硬件
    损坏**。这一侧更要紧——它产出的是 `outcomes`，也就是每一个百分比的分子
    和分母；检查点侧翻一个位只脏掉一行，这边翻一个位会把一整个视野的结局判
    错。给了 ``health_out`` 就把这一侧的账写进去（出参而不是改返回值：返回
    `recs` 是为了能链式写，而且已有调用点都靠它）。
    """
    memo: dict[int, list[ZhongShu]] = {}
    health = {"checked": verify >= 2, "tries": verify,
              "n_divides": 0, "recomputed": 0, "unstable": 0}

    def zss_at(j: int) -> list[ZhongShu]:
        if j not in memo:
            health["n_divides"] += 1
            if verify < 2:
                memo[j] = _divide(bars[:j], zs_from)
            else:
                window = bars[:j]
                out, chk = stable(lambda: _divide(window, zs_from),
                                  tries=verify, label=f"divide@{j}")
                if not chk.ok:
                    health["unstable"] += 1
                elif chk.retried:
                    health["recomputed"] += 1
                memo[j] = out
        return memo[j]

    keys = [(str(h), h) for h in horizons]
    if unbounded:
        keys.append((UNBOUNDED, None))
    for r in recs:
        i = r["i"]
        outs: dict[str, dict] = {}
        for key, h in keys:
            if r["zs"] is None:
                outs[key] = {"code": "no_zhongshu", "detail": "当时还没有中枢"}
                continue
            j = len(bars) if h is None else i + 1 + h
            if j > len(bars):
                outs[key] = {
                    "code": "skipped_no_future",
                    "detail": f"后面只剩 {len(bars) - i - 1} 根，不够 {h} 根"}
                continue
            outs[key] = resolve(_shell(r), zss_at(j))
        r["outcomes"] = outs
    if beichi:
        _annotate_beichi(recs, bars, zs_from)
    if health_out is not None:
        health_out.update(health)
    return recs


#: 单票检查点文件的格式版本。记录的形状变了就 +1——旧断点会被当成过期重算，
#: 而不是混进新一轮里。
POOL_FORMAT = 1


def _pool_params(level: Level, bars: list[Bar], *, step: int, warmup: int,
                 zs_from: str, horizons, unbounded: bool, beichi: bool,
                 verify: int) -> str:
    """一只票这一趟的参数指纹。**输入K线也在里面。**

    断点续跑最容易出的错不是算错，是**拿旧参数的结果冒充新参数的结果**：改了
    `--zs`、改了视野、或者只是 `pull --update` 补了今天那根K线，断点文件照样
    躺在那儿，读进来一声不响。所以指纹把「会改变 recs 的一切」都算进去，对不
    上就重算，不猜。

    ``verify`` 也算进去，哪怕复用「查过的」去填「没查的」在数值上只会更好：
    `walk_health` 的全部价值在于 ``checked`` 这一位说的是实话，混了就不是了。
    """
    return digest({
        "fmt": POOL_FORMAT, "level": level.value, "step": step,
        "warmup": warmup, "zs": zs_from, "h": list(horizons),
        "unbounded": unbounded, "beichi": beichi, "verify": verify,
        "bars": bars_digest(bars), "n_bars": len(bars),
    })


def _pool_read(p: Path, want: str) -> list[dict] | None:
    """读断点。**读不懂、对不上、半截的，一律当没有。**

    这里绝不能宽容：一个被写断的 JSON 如果用 `json.JSONDecodeError` 以外的
    方式悄悄退化成部分记录，那只票就少了一截检查点，而分母是这套东西唯一
    真正紧缺的东西。
    """
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or d.get("params") != want:
        return None
    recs = d.get("recs")
    return recs if isinstance(recs, list) else None


def _pool_write(p: Path, params: str, code: str, recs: list[dict]) -> None:
    """原子落盘：先写临时文件再 `os.replace`。

    直接 `open(p, "w")` 写几百条记录，进程在中途死掉（这台机器会）就留下一个
    **语法上可能仍然合法**的半截 JSON。下一趟读到它会当成「这只票跑完了」。
    `os.replace` 在同一个卷上是原子的，读到的要么是上一版、要么是完整的新版。
    """
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(
        {"params": params, "code": code, "n": len(recs), "recs": recs},
        ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def walk_pooled(
    codes: Iterable[str], level: Level,
    load: Callable[[str, Level], list[Bar]], *,
    step: int = 8, warmup: int = 0, zs_from: str = "seg",
    horizons=PROJ_HORIZONS, unbounded: bool = True, beichi: bool = True,
    verify: int = 1, checkpoint: str | Path | None = None,
    on_step: Callable[[int, int, str, dict], None] | None = None,
) -> dict:
    """跨票走查 + 打分，把所有票的检查点汇到一个列表里。

    ### 为什么要有这个函数

    `score_projections(recs, bars, ...)` 绑死一只票的 `bars`——判定侧要
    `bars[:i+1+h]` 重划，那串K线必须是**这条记录自己那只票的**。于是单票是
    这一层的物理上限，而单票的分母不够：600009 30分钟 667 个检查点折下来只有
    6 个独立中枢，`summarize_projections` 自己印的验收线要 9~10 胜。**这张表
    在单票上测不出任何东西**，不是因为视野选错或者算法不行，是因为 n=6。

    修法不是给 `score_projections` 加一个「多只票的 bars」参数——那会让它每条
    记录都要先查自己属于谁，而它现在是干净的单票函数、各处都这么用。这里改成
    **按票循环，一只票一套 bars，出来之后再合并**。合并是安全的，因为
    `outcomes` 在那时已经判完，下游（`episodes` / `summarize_projections`）
    只读记录本身。

    ### 断点

    给了 ``checkpoint``（一个目录）就一票一个文件，跑完一只写一只。全市场
    五千只票这一趟是几小时起步，而这台机器会在长 Python 运行里损坏内存、
    甚至直接崩掉（见 `chan/verify.py`）——没有断点，崩在第 4800 只等于整趟
    白跑，而且**第二趟同样大概率崩**。参数指纹见 `_pool_params`。

    ### 失败不中断

    一只票拉不到K线、K线太短、划线抛异常，都记进 ``failed`` 继续走，和
    `scan.scanner.scan_many` 同一条规矩。**但失败数要印出来**：五千只里悄悄
    掉了三百只，剩下那些的百分比看着一样正常。
    """
    codes = list(codes)
    cp = Path(checkpoint) if checkpoint else None
    if cp:
        cp.mkdir(parents=True, exist_ok=True)

    pooled: list[dict] = []
    failed: list[dict] = []
    per_code: dict[str, dict] = {}
    # 判定侧的健康账按票累加。`checked`/`tries` 全票一致（同一组参数），
    # 计数项相加——这几个数回答的是「这一趟机器翻了几次位」，是全局的。
    score_health = {"checked": verify >= 2, "tries": verify,
                    "n_divides": 0, "recomputed": 0, "unstable": 0}

    for n, code in enumerate(codes):
        try:
            bars = load(code, level)
        except Exception as e:                      # noqa: BLE001
            failed.append({"code": code, "where": "load", "error": str(e)})
            continue
        if not bars or len(bars) < max(warmup or MIN_BARS, step):
            failed.append({"code": code, "where": "load",
                           "error": f"K线只有 {len(bars) if bars else 0} 根，"
                                    f"不够起步"})
            continue

        params = _pool_params(level, bars, step=step, warmup=warmup,
                              zs_from=zs_from, horizons=horizons,
                              unbounded=unbounded, beichi=beichi,
                              verify=verify)
        f = cp / f"{code}.json" if cp else None
        recs = _pool_read(f, params) if f else None
        cached = recs is not None

        if not cached:
            dh: dict = {}
            try:
                recs = walk_projections(bars, code, level, step=step,
                                        warmup=warmup, zs_from=zs_from,
                                        verify=verify)
                score_projections(recs, bars, horizons=horizons,
                                  zs_from=zs_from, unbounded=unbounded,
                                  beichi=beichi, verify=verify,
                                  health_out=dh)
            except Exception as e:                  # noqa: BLE001
                failed.append({"code": code, "where": "walk", "error": str(e)})
                continue
            for k in ("n_divides", "recomputed", "unstable"):
                score_health[k] += dh.get(k, 0)
            if f:
                _pool_write(f, params, code, recs)

        assert recs is not None
        pooled += recs
        per_code[code] = {"n_checkpoints": len(recs), "cached": cached,
                          "n_bars": len(bars)}
        if on_step:
            on_step(n, len(codes), code, per_code[code])

    return {
        "recs": pooled,
        "failed": failed,
        "n_codes": len(codes),
        "n_ok": len(per_code),
        "n_failed": len(failed),
        "n_cached": sum(v["cached"] for v in per_code.values()),
        "per_code": per_code,
        # 走查侧的账要等全票合完再算——`walk_health` 数的是「有多少条记录带
        # 脏标记」，那是个全局比例。判定侧的账是循环里累加的（断点命中的票
        # 不会再算一遍，所以它只覆盖这一趟真跑过的那些，这正是它该说的）。
        "walk_health": walk_health(pooled, verify),
        "score_health": score_health,
    }


def resolution_time(recs: list[dict], bars: list[Bar], *,
                    zs_from: str = "seg", step: int = 24,
                    cap: int = 1440) -> dict:
    """揭晓时间：从检查点往后走多少根，`resolve` 才第一次不说 `unresolved`。

    **这是挑视野用的只读诊断，不是指标。** `PROJ_HORIZONS` 要横跨这里印出来
    的分布——全挤在中位数右边，报表上就只剩「都判出来了」；全挤在左边，就只
    剩 `unresolved`。两种都读不出模型有没有本事。

    按 ``step`` 的网格往后探，不是逐根——逐根要给每条记录各划几百次线。网格
    让所有记录共用同一批划分，代价是分辨率只到 ``step`` 根，对「选视野」这个
    用途足够。``cap`` 之后还没揭晓的记为删失（censored），**单独计数而不是
    当成 cap**，否则分位数会被一堆假的 cap 值顶上去。
    """
    memo: dict[int, list[ZhongShu]] = {}

    def zss_at(j: int) -> list[ZhongShu]:
        if j not in memo:
            memo[j] = _divide(bars[:j], zs_from)
        return memo[j]

    grid = list(range(step, len(bars) + 1, step))
    waits: list[int] = []
    censored = 0
    for r in recs:
        if r["zs"] is None:
            continue
        i, shell, hit = r["i"], _shell(r), None
        # 探测点用**绝对网格**，不是每条记录各自的 i+step*k。后者每条记录都要
        # 一套自己的划分，667 条记录乘 60 个探测点就是四万次 `analyze`；对齐到
        # 绝对网格之后全体共用同一批（len(bars)/step 个），差别是两个量级。
        # 代价是每条记录的等待时间有最多 step 根的量化误差，对「挑视野」够用。
        for g in grid:
            if g <= i + 1:
                continue
            if g - i > cap:
                break
            if resolve(shell, zss_at(g))["code"] != "unresolved":
                hit = g - i
                break
        if hit is None:
            censored += 1
        else:
            waits.append(hit)
    if not waits:
        return {"n": 0, "censored": censored}
    s = sorted(waits)
    pick = lambda q: s[min(len(s) - 1, int(q * len(s)))]  # noqa: E731
    return {"n": len(s), "censored": censored, "step": step,
            "p10": pick(0.10), "median": pick(0.50), "p90": pick(0.90),
            "max": s[-1], "mean": statistics.fmean(s)}


#: 判不了的几种结果。它们不进分母，但**必须分开计数**——三者的含义完全不同。
NOT_SCORED = ("unresolved", "redrawn", "no_zhongshu", "skipped_no_future")


def fold_reversal(probs: dict[str, float], trend: str) -> dict[str, float]:
    """把 ``p(reversal)`` 并进**反方向**的 `break_*`。

    `resolve` 的返回里根本没有 `reversal` 这一项，而提示词里有、模型会给它
    概率。那部分概率事后无处可去，必须有个明确的去处。

    折叠而不是丢掉、也不是重新归一，依据是 `_reversal` 自己的定性（
    `chan/forward.py:189-192`）：「这不是第四种走势类型，它是『向反方向离开』
    那一支的特例，只是触发机制不同」。一个背驰转折走出来的新中枢，`resolve`
    判出来本来就是反方向那个 code。

    **为什么不归一化**：归一会让 `0.5 reversal + 0.5 break_up` 和
    `1.0 break_up` 得同分，等于给模型一个可以无成本倾倒概率的避险桶。C-split
    的「`unclear` 永远不算错」在这里不适用——那条管的是对整个回答的离散弃权，
    而这里的 reversal 是连续质量、可以部分对冲，同一条规则换到概率空间就变成
    了可利用的漏洞。折叠保留零和性质，归一化不保留。

    **基准也必须走这个函数**（D1-2）。提示词明说「拿不准就把概率摊平」，下跌
    趋势里摊平是 0.25×4，折叠后变成 extend 0.25 / break_up **0.50** /
    break_down 0.25——一个完全不知所措的模型会在报表上呈现为系统性看多，方向
    还固定。基准不同步折叠，这个偏差就和真实技能彻底混在一起。
    """
    if "reversal" not in probs:
        return dict(probs)
    if trend not in (TREND_UP, TREND_DOWN):
        # 盘整里 `project` 根本不给 reversal 这一项。出现了说明上游串了行，
        # 而「往哪折」此时没有答案——静默猜一个方向正是这个函数要防的事。
        raise ValueError(f"盘整的选项表里不该有 reversal（trend={trend}）")
    out = dict(probs)
    p = out.pop("reversal")
    tgt = "break_up" if trend == TREND_DOWN else "break_down"
    out[tgt] = out.get(tgt, 0.0) + p
    return out


def in_progress_code(r: dict) -> str | None:
    """这个检查点上「正在走」的是哪一支。没有就 None。

    **这不是引擎的预测**，只是现价在中枢哪一侧的位置读数（模块开头说过）。
    留着是因为它是排序的**下界**：模型要是还不如照位置瞎猜，这一层没信息。
    """
    prog = [s["code"] for s in r["scenarios"] if s.get("in_progress")]
    if not prog:
        return None
    code = prog[0]
    return ("break_up" if r["trend"] == TREND_DOWN else "break_down") \
        if code == "reversal" else code


def episodes(recs: list[dict]) -> list[dict]:
    """把检查点按**中枢身份**折叠成 episode。

    同一个中枢上的几十个检查点共享同一个结局，它们不是几十个样本，是一个。
    600009 30分钟实测：635 个检查点只对应 10 个中枢，每枢 24~107 个，而按
    检查点算出来的 `in_progress` 命中率 60.8% 在折叠之后掉到 33.3%——那 60.8%
    几乎全部来自「同一个中枢里越靠后的检查点越好猜」。

    折叠键用 `forward.zs_key`（ZG/ZD），**不用下标**：下标会随重划整体平移。
    代表点取 ``first``，理由见 `summarize_projections`。

    **票号也进键。** 跨票汇总（`walk_pooled`）之后 ZG/ZD 不再唯一：两只票在
    同一个价位区间震荡是常事，后复权之后更常——它们会被折成一个 episode，
    代表点取了其中一只的首点，另一只的几十个检查点连同它们的结局一起消失在
    分母外。单票跑时 `code` 是常数，这个键退化成原来的样子，行为不变。
    """
    buckets: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    # 排序键带上 `code`：跨票的 `i` 是各自的下标，混在一起排会把两只票的
    # 检查点交错，`first`/`last` 就不再是「这只票在这个中枢上最早/最晚的
    # 那一次」。同票内仍按 `i` 升序，这是代表点取首点的前提。
    for r in sorted(recs, key=lambda x: (x.get("code") or "", x["i"])):
        if r["zs"] is None:
            continue
        k = (r.get("code"), *zs_key(r["zs"]))
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(r)
    return [{"key": k, "code": k[0], "zg": k[1], "zd": k[2],
             "n": len(buckets[k]), "recs": buckets[k],
             "first": buckets[k][0], "last": buckets[k][-1]} for k in order]


def recluster(recs: list[dict], tol_frac: float = 0.003) -> dict:
    """口径体检：1e-6 这一级有没有把**同一个**中枢切成两个 episode。

    `_same` 的 1e-6 在**单次划分内**是对的——ZG/ZD 由前两段定死后不再变
    （17课 p36）。但跨检查点不一定：前两段自己会被后续K线回退重划（69课
    p177 / 78课 p197）。于是「道义上同一个中枢」可能因为某次重划在 1e-6 口径
    下裂开，**把独立样本数虚报高**。

    这里用宽松容差（中枢宽度的 ``tol_frac``）再聚一次，报「严格口径几个、
    宽松口径几个」。两个数不等，说明 n 本身就是未经检验的——而 n 是报表上
    所有显著性结论的分母。**在看任何分数之前先跑这个。**
    """
    keys = [e["key"] for e in episodes(recs)]
    groups: list[list[tuple[str | None, float, float]]] = []
    for code, zg, zd in keys:
        tol = max(abs(zg - zd) * tol_frac, 1e-9)
        for g in groups:
            # `c == code` 先判：宽松容差的用途是认出「同一只票里被重划裂开的
            # 同一个中枢」，跨票合并是另一回事（那是两个结构碰巧价位接近），
            # 合了会把虚报的 n 从「略高」变成「离谱」。
            if any(c == code and abs(zg - a) <= tol and abs(zd - b) <= tol
                   for c, a, b in g):
                g.append((code, zg, zd))
                break
        else:
            groups.append([(code, zg, zd)])
    merged = [g for g in groups if len(g) > 1]
    return {"strict": len(keys), "loose": len(groups), "tol_frac": tol_frac,
            "merged": [[f"{c or ''} {zd:.4f}~{zg:.4f}".strip()
                        for c, zg, zd in g] for g in merged]}


def sign_test(wins: int, n: int) -> float:
    """双侧精确符号检验。``n`` 是**非平局**的对数。

    单票上这就是全部的统计火力，数值小得该提前知道：10 战 10 胜 p≈0.002、
    9 胜 p≈0.021、**8 胜 p≈0.11**。所以验收线划在 9 胜，8 胜不算有证据。
    """
    if n <= 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, j) for j in range(k, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def brier(probs: dict[str, float], truth: str, codes: list[str]) -> float:
    """多类 Brier（欧氏距离平方和），越小越好。均匀分布在 k 类上是 1−1/k。

    **codes 必须是折叠之后的那张表**（`fold_reversal`），模型和基准用同一张，
    否则两边的理论地板都不一样，差值没有意义。
    """
    return sum((probs.get(c, 0.0) - (1.0 if c == truth else 0.0)) ** 2
               for c in codes)


def _codes_of(r: dict) -> list[str]:
    """这个检查点上**折叠后**的选项集（打分空间）。趋势和盘整都是 3 类。"""
    return [s["code"] for s in r["scenarios"] if s["code"] != "reversal"]


def _raw_codes_of(r: dict) -> list[str]:
    """**未折叠**的选项集，趋势中是 4 个。只用来造均匀基准。

    均匀基准必须从这张表出发再走一遍 `fold_reversal`，不能直接在 3 类上摊平。
    提示词说的是「拿不准就把概率摊平」，模型摊的是 4 项；折叠后那个分布是
    extend .25 / 反向 .50 / 同向 .25，**不是** 1/3 各一份。基准要是取了 1/3，
    一个完全不知所措的模型就会凭空得到一截「看多」的分，而那截分来自折叠规则
    本身，不来自它说了什么。
    """
    return [s["code"] for s in r["scenarios"]]


def uniform_ref(row: dict) -> dict[str, float]:
    """地板参照：在**未折叠**的选项表上摊平，再走一遍 `fold_reversal`。

    它只回答「模型有没有做任何事」，不回答「值不值得挂」。趋势里 4 项摊平
    折叠后是 extend .25 / 反向 .50 / 同向 .25，盘整里 3 项摊平就是 1/3 各一
    份——两种形状不一样是**对的**，理由见 `_raw_codes_of`。
    """
    raw = row["codes_raw"]
    return fold_reversal({c: 1.0 / len(raw) for c in raw}, row["trend"])


def prequential_ref(prior: list[str], codes: list[str]) -> dict[str, float]:
    """序贯气候参照：拿**已经揭晓**的前序 episode 的类频率，Laplace 平滑。

    这是单票时的退路（首选是横截面 climatology——另一批票上跑同一套有界
    `resolve` 取类频率，那个才真正无条件；项目本来就要全市场扫描，等扩到
    多票时换过去）。

    ``prior`` 的排序依据必须是**结局揭晓时间**，不是检查点时间——按检查点
    时间排会把还没揭晓的标签喂给基准。固定 H 下揭晓时刻是 ``i+1+H``，对 i
    单调，所以按检查点下标排和按揭晓时间排是同一个序。**无界列不成立**
    （揭晓时刻不是 i 的函数），那一列的这个参照只能当参考看。

    首个 episode 的 ``prior`` 是空的，Laplace 让它退化成均匀分布，正好。
    """
    n = len(prior) + len(codes)
    return {c: (prior.count(c) + 1) / n for c in codes}


def _fold_answer(probs: dict[str, float], row: dict) -> dict[str, float]:
    """把模型的原始回答折进打分空间，并限制在该检查点的合法选项集内。

    模型可能排了选项表上没有的 code（比如盘整时给了 `reversal`）。这种项
    **丢弃而不是报错**：`fold_reversal` 对盘整里的 reversal 会抛，那是给上游
    串行用的保护，不该把一次解析噪声变成整轮崩溃。丢弃之后概率不再和为 1，
    Brier 照样有定义，而且少的那部分本来就该扣分。
    """
    p = dict(probs)
    if "reversal" in p and row["trend"] not in (TREND_UP, TREND_DOWN):
        p.pop("reversal")
    p = fold_reversal(p, row["trend"])
    return {c: v for c, v in p.items() if c in row["codes"]}


#: 中枢在判定点那次划分里按 ZG/ZD 认不出来了。这一种只可能发生在回测里
#: （`detect_at` 拿到的 k 是现找的），所以代号定在这边而不是 `chan.beichi`。
ZS_GONE = "zs_gone"

#: 「判不了」的各种原因怎么说人话。**`OPPOSITE` 不是盲区**：它是「这个中枢是
#: 一次折返的拐点」，本身就是个读数，混进盲区里会让人以为引擎瞎了。
WHY_CN = {
    OPPOSITE: "进入段与离开段方向相反：折返拐点，没有同向的两段可比",
    NO_ENTER: "取不到进入段：多半是序列里第一个中枢，它前面没有进入段",
    NO_LEAVE: "取不到离开段",
    OUT_OF_RANGE: "中枢下标越界",
    ZS_GONE: "中枢在判定点的划分里认不出来了",
    "unrecorded": "没记原因（`_annotate_beichi` 是旧版本跑的）",
}


def _beichi_tally(recs: list[dict], key: str) -> dict:
    """``break_*`` 结局里带背驰证据的有几个。`_annotate_beichi` 没跑就全 0。

    ``why`` 把 ``unknown`` 那一桶按原因拆开，**按本次运行实测**——不要再把
    构成写死在文档或输出里，那种数字过期了也没人发现。
    """
    t: dict = {"yes": 0, "no": 0, "unknown": 0, "why": {}}
    for r in recs:
        o = r.get("outcomes", {}).get(key, {})
        if not o.get("code", "").startswith("break_"):
            continue
        v = o.get("with_beichi")
        t["unknown" if v is None else "yes" if v else "no"] += 1
        if v is None:
            w = o.get("beichi_why") or "unrecorded"
            t["why"][w] = t["why"].get(w, 0) + 1
    return t


def summarize_projections(recs: list[dict], *, horizons=PROJ_HORIZONS,
                          unbounded: bool = True) -> dict:
    """两级汇总：检查点级**和** episode 级，每个视野各一份。

    **永远不只报一级。** 检查点级的 n 是几百，但那几百条里只有十来个独立
    结构，拿它做显著性是自欺；episode 级的 n 才是真分母，而它小到大部分数字
    都说明不了什么——这件事也必须让报表自己说出来，而不是读的人去猜。

    episode 的代表点取**首个**检查点，三个取法里只有它没有乐观偏差：

    - 平均概率：Brier 是凸的，由 Jensen，`Brier(mean p) ≤ mean Brier(p)`
      **恒成立**。先平均再打分是白送的减分，而且组内摇摆越厉害送得越多——
      等于奖励不一致。
    - 多数票：丢掉全部概率（那是这一层唯一的产出），乐观偏差同源。
    - 首个：信息最少、最难，而且**不随 episode 碰巧持续了多久而变化**。

    这和 C-lag 的 `first_seen`、`score.py:55-56` 的「入场价用第一次看见那天的
    收盘价」是同一条规则的第三次出现：**把判断记在它第一次真能被做出的那一刻
    的账上**。

    ``in_progress_*`` 不是准确率。它只表示现价在中枢哪一侧，在「两段就离开、
    再没回来」的中枢上接近 100%，在长期震荡的中枢上接近掷硬币。留着是因为它
    是排序的下界。``lookahead_premium`` 是末点减首点——C-peek 要求两种都算出来
    摆在一起，这个差额量化的正是「看起来会预测，其实只是站得离答案近」。
    """
    keys = [str(h) for h in horizons] + ([UNBOUNDED] if unbounded else [])
    eps = episodes(recs)
    out: dict = {"n_checkpoints": len(recs), "n_episodes": len(eps),
                 "recluster": recluster(recs),
                 # 组内稳定性**和视野无关**——`in_progress` 是当下的位置读数，
                 # 不看未来。所以它在顶层算一次，不进每个视野的行里。原先逐行
                 # 印同一个数，等于暗示它随视野变化。
                 "stability": _stability(eps), "h": {}}

    for key in keys:
        buckets: dict[str, int] = {}
        hit = miss = na = 0
        for r in recs:
            code = r["outcomes"][key]["code"]
            buckets[code] = buckets.get(code, 0) + 1
            prog = in_progress_code(r)
            if code in NOT_SCORED or prog is None:
                na += 1
            elif code == prog:
                hit += 1
            else:
                miss += 1

        rows = []
        for e in eps:
            seq = [in_progress_code(r) for r in e["recs"]]
            first, last = e["first"], e["last"]
            rows.append({
                "code": e["code"], "zd": e["zd"], "zg": e["zg"], "n": e["n"],
                "i": first["i"], "ts": first["ts"],
                "outcome": first["outcomes"][key]["code"],
                "outcome_last": last["outcomes"][key]["code"],
                "in_progress_first": seq[0], "in_progress_last": seq[-1],
                "flips": sum(a != b for a, b in zip(seq, seq[1:])),
                # `trend` 和 `codes_raw` 是给 `score_rankings` 造均匀基准用的：
                # 折叠要知道方向，摊平要知道摊在几项上。两个都必须取**首点**
                # 的值——代表点是首点，基准得和被评的那个回答站在同一处。
                "trend": first["trend"],
                "codes": _codes_of(first),
                "codes_raw": _raw_codes_of(first),
            })

        scored = [w for w in rows if w["outcome"] not in NOT_SCORED]
        truths: dict[str, int] = {}
        for w in scored:
            truths[w["outcome"]] = truths.get(w["outcome"], 0) + 1
        best = max(truths.items(), key=lambda kv: kv[1]) if truths else None
        ip_first = sum(w["in_progress_first"] == w["outcome"] for w in scored)
        # 末点用末点自己的结局判——同一个 episode 的首末两点视野窗口不同，
        # 拿首点的标签去判末点的预测，等于把两种偷看混在一起。
        last_ok = [w for w in rows if w["outcome_last"] not in NOT_SCORED]
        ip_last = sum(w["in_progress_last"] == w["outcome_last"]
                      for w in last_ok)

        out["h"][key] = {
            "checkpoint": {
                "n": len(recs), "outcomes": buckets,
                "in_progress_hit": hit, "in_progress_miss": miss,
                "not_scored": na,
                # 旁证，**不进任何率**（D3）。三个计数而不是一个比例：
                # 分母到底是 443 还是 58，决定这个数能不能读。
                "with_beichi": _beichi_tally(recs, key),
                "note": "相关样本，不可做显著性；真分母看 episode",
            },
            "episode": {
                "n_total": len(rows), "n_scored": len(scored),
                "outcomes": truths,
                "in_progress_hit": ip_first,
                "in_progress_rate": (ip_first / len(scored) if scored
                                     else None),
                # 常数预测器的**天花板**，不是基准：它偷看了标签。模型低于它
                # 什么都不说明；模型高于它是小样本上唯一能单独成立的强结论。
                "const_best": ({"code": best[0], "hits": best[1],
                                "rate": best[1] / len(scored)} if best
                               else None),
                "lookahead_premium_ip": (
                    (ip_last / len(last_ok) - ip_first / len(scored))
                    if scored and last_ok else None),
                "rows": rows,
            },
        }
    return out


def _stability(eps: list[dict]) -> dict:
    """组内稳定性。**它的 n 是几百，不是 10**——这一层是唯一有统计火力的地方。

    `in_progress` 在同一个中枢内来回翻多少次，量的是「位置读数」本身有多不
    安分。600009 实测逐 episode 0%~25.7%：长中枢上它翻十几次。模型那一层的
    `flip_rate` 要和这个数对照着读——模型比它还抖，说明模型连位置都没读稳。
    """
    flips = pairs = 0
    per: list[float | None] = []
    for e in eps:
        seq = [in_progress_code(r) for r in e["recs"]]
        f = sum(a != b for a, b in zip(seq, seq[1:]))
        p = max(len(seq) - 1, 0)
        flips += f
        pairs += p
        per.append(f / p if p else None)
    return {"n_pairs": pairs,
            "in_progress_flip_rate": flips / pairs if pairs else None,
            "per_episode": per}


def score_rankings(summary: dict, answers: dict[int, dict], *,
                   key: str = "240") -> dict:
    """给模型的排序打分。``answers`` 按检查点下标 ``i`` 索引。

    每个值是 ``{"probs": {code: p}, "missing": [...], "ok": bool}``——
    `agent.parse_ranking` 的形状。``ok=False`` 的记为弃答，不进分母也不算输。

    **主指标是逐 episode 的配对 Brier 差 + 精确符号检验**，不是 skill score。
    `1 − BS_model/BS_ref` 是两个噪声均值的比值，n=10 时分子分母都不稳。配对
    差向量把每一场的胜负原样印出来，一眼看得见是哪个 episode 翻了车。

    返回里的 ``wins/n_pairs/p`` 就是验收口径：9 胜或 10 胜（p ≤ 0.021）才算
    有证据，8 胜 p≈0.11 不算。这条在跑之前就该说死。

    **跨票的 summary 这里会直接拒绝。** `answers` 按 ``i`` 索引，而 ``i`` 是
    每只票各自的K线下标：600009 的第 320 个检查点和 600519 的第 320 个检查点
    共用一个键。接受了就是把甲票的答案拿去评乙票的结局，分数照样算得出来、
    看不出任何异样。模型侧按票分开跑、分开评，这里不替它猜。
    """
    h = summary["h"][key]
    rows = [w for w in h["episode"]["rows"] if w["outcome"] not in NOT_SCORED]
    codes = {w.get("code") for w in rows}
    if len(codes) > 1:
        raise ValueError(
            f"score_rankings 只能评单票，这份 summary 混了 {len(codes)} 只："
            f"{sorted(c or '?' for c in codes)}。answers 按 i 索引，跨票会串。")

    prior: list[str] = []      # 已揭晓的结局，序贯参照的料
    detail, missing_n, crushed = [], 0, 0
    for w in rows:
        a = answers.get(w["i"])
        ref_u = uniform_ref(w)
        ref_p = prequential_ref(prior, w["codes"])
        prior.append(w["outcome"])        # 用完**之后**才加，别喂自己的标签
        if not a or not a.get("ok"):
            detail.append({**_row_head(w), "answered": False})
            continue
        p = _fold_answer(a["probs"], w)
        pt = p.get(w["outcome"], 0.0)
        if pt == 0.0:
            missing_n += 1
        if pt < 0.05:
            crushed += 1
        bm = brier(p, w["outcome"], w["codes"])
        detail.append({
            **_row_head(w), "answered": True, "p_truth": pt,
            # 整张折叠后的概率表要存下来。`_concordant` 的 neg 堆需要「模型给
            # 多数类的概率」，只留 `p_truth` 的话那一堆只能按 0 算，等于系统性
            # 高估一致对率。多存三个浮点，换一个不带已知偏差的副指标。
            "probs": p,
            "top1": max(p, key=p.get) if p else None,
            "brier": bm,
            "brier_uniform": brier(ref_u, w["outcome"], w["codes"]),
            "brier_preq": brier(ref_p, w["outcome"], w["codes"]),
            "in_progress": w["in_progress_first"],
        })

    done = [d for d in detail if d["answered"]]
    out = {"key": key, "n_scored": len(rows), "n_answered": len(done),
           "rows": detail,
           # C3 的两个免调参计数，替掉 log-loss（漏排补 0 → log 是 −inf，
           # 加 epsilon 之后那个数主要由 epsilon 决定，不由模型决定）。
           "p_truth_lt_05": crushed, "p_truth_zero": missing_n,
           "top1_hits": sum(d["top1"] == d["outcome"] for d in done),
           "agree_in_progress": sum(d["top1"] == d["in_progress"]
                                    for d in done),
           "vs": {}}
    for name in ("uniform", "preq"):
        diffs = [d[f"brier_{name}"] - d["brier"] for d in done]
        nz = [x for x in diffs if x != 0]
        wins = sum(x > 0 for x in nz)
        out["vs"][name] = {
            "diffs": diffs, "wins": wins, "n_pairs": len(nz),
            "p": sign_test(wins, len(nz)),
            "mean_diff": statistics.fmean(diffs) if diffs else None,
            "mean_brier_ref": (statistics.fmean(d[f"brier_{name}"]
                                                for d in done)
                               if done else None),
        }
    out["mean_brier"] = (statistics.fmean(d["brier"] for d in done)
                         if done else None)
    out["concordant"] = _concordant(done)
    return out


def _row_head(w: dict) -> dict:
    return {"code": w.get("code"), "i": w["i"], "ts": w["ts"],
            "zs": f"{w['zd']:.2f}~{w['zg']:.2f}", "outcome": w["outcome"]}


def _concordant(done: list[dict]) -> dict | None:
    """C7 副指标：多数类的一致对计数。

    取出现次数最多的那一类 ``c``，把 episode 分成「真实结局是 c」和「不是」
    两堆，数「模型给 c 的概率在前一堆里更高」的配对数。

    比 top-1 功效高（用的是概率不是 argmax），而且**完全不受校准影响**——
    整体偏高偏低都无所谓，排序对了就得分。7 vs 3 给 21 个配对，粒度
    1/21≈0.048，够读。平局算半分，这是 AUC 的标准处理。
    """
    if not done:
        return None
    freq: dict[str, int] = {}
    for d in done:
        freq[d["outcome"]] = freq.get(d["outcome"], 0) + 1
    c = max(freq, key=freq.get)
    pos = [d for d in done if d["outcome"] == c]
    neg = [d for d in done if d["outcome"] != c]
    if not pos or not neg:
        return {"code": c, "n_pairs": 0, "rate": None}
    score = 0.0
    for a in pos:
        for b in neg:
            pa, pb = a["probs"].get(c, 0.0), b["probs"].get(c, 0.0)
            score += 1.0 if pa > pb else (0.5 if pa == pb else 0.0)
    n = len(pos) * len(neg)
    return {"code": c, "n_pairs": n, "rate": score / n}
