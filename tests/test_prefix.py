"""逐笔前缀：追加K线之后，划线会变成什么样。

### 这里**不**断言「已确认线段不许被改写」

一度想这么断言，也确实抓到了：60 只真实票逐笔扫过去，48 只在某一笔处把
已经标了 `confirmed=True` 的线段删掉重划。但这不算 bug——它是站在**当下**
做分析的必然结果：

  划线永远是「截止到今天这批K线，该怎么分」的答案。明天多走一根，证据变了，
  答案就该跟着变。后续行情是概率，不是我们能钉死的东西；真钉死了，等于拿
  昨天的信息冒充今天的结论。

所以前缀之间**允许**不一致。真正要守的是另一条：**每一个前缀各自都得是一份
合法的划线**。历史可以重写，但不能在任何一天写出一份违反原文的图。本模块测
的就是这个。

(`test_stress.py` 里那条随机游走版的「增量稳定性」断言已按同一理由撤掉，
见该处注释。)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import mk_bis

from chan.invariants import check_segs
from chan.seg import build_segs

# 600519 后复权日线最早 18 个转折点（2001-09-04 ~ 2002-05-28）。
# 挑这一段是因为它是「多一笔就改写历史」的最小真实复现，见下面那条测试。
S600519_HEAD = [38.08, 35.01, 37.05, 36.0, 37.5, 32.7, 35.72, 32.98, 36.2,
                34.8, 39.3, 34.7, 38.68, 37.12, 39.56, 36.51, 38.5, 34.5,
                35.55]


def test_one_more_bi_may_rewrite_an_already_confirmed_segment():
    """把「会改写」这件事本身钉成用例，免得哪天有人当 bug 顺手改掉。

    16 笔时 j=4 的情况二凑不齐第二特征序列的三个元素（`len(second) < 3`），
    判不出来，于是算法往后走，在 j=10 按情况一确认了 [0-10]。
    第 17 笔一到，第三个元素长出来了，j=4 立刻成立——而 `build_segs` 取的是
    **第一个**通过的 j，所以 [0-10] 被 [0-4] + [5-13] 顶掉。

    这不是算错，是证据变了。要守的不变量在下一条测试里。
    """
    short = [s for s in build_segs(mk_bis(S600519_HEAD[:17])) if s.confirmed]
    long_ = [s for s in build_segs(mk_bis(S600519_HEAD[:18])) if s.confirmed]

    assert [(s.start_bi, s.end_bi) for s in short] == [(0, 10)]
    assert [(s.start_bi, s.end_bi) for s in long_] == [(0, 4), (5, 13)]
    # 改写的是**同一个起点**上的终点，不是在后面追加——确认过的 [0-10] 消失了
    assert short[0].end_bi != long_[0].end_bi


def test_every_prefix_of_the_fixture_is_itself_a_legal_division():
    """允许改写，但不允许在任何一个前缀上划出违反原文的图。

    这是放弃「增量稳定」之后剩下的那条底线：既然每天都要重划，那每天那一份
    都必须自洽——笔数单数、方向交替、顶高于底、首尾相接（78课 p197）。
    """
    for n in range(4, len(S600519_HEAD) + 1):
        bis = mk_bis(S600519_HEAD[:n])
        check_segs(build_segs(bis), bis)


# 真实数据部分。手工 fixture 造不出趋势行情里那种「第二特征序列一路被合并掉」
# 的形态——`test_stress.py` 的随机游走 20 组一次都没撞上，这个缺陷才漏到图上
# 才被发现。所以这里必须吃真实K线。
#
# 缓存不在就跳过：别的机器上 clone 下来不该因为没有 ~/.chan_cache 而红。
_MAX_BI = 140  # 逐笔重划是 O(n⁴)，封在这里跑完只要几秒
_N_CODES = 6


def _cached_day_bis():
    """从本地缓存里取几只票的笔序列。没有缓存返回空。"""
    from feeds import Level, cache

    root = Path(cache.DEFAULT_ROOT)
    if not root.exists():
        return []
    from chan import analyze

    out = []
    for p in sorted(root.rglob("day/*.csv"))[:_N_CODES]:
        try:
            bars = cache.load(p.stem, Level("day"), source=p.parent.parent.name)
            out.append((p.stem, analyze(bars, check=False).bis[:_MAX_BI]))
        except Exception:  # noqa: BLE001 - 缓存里混进坏文件不该让测试红
            continue
    return out


def test_every_prefix_of_real_data_is_itself_a_legal_division():
    """真实数据逐笔重划，每一个前缀都要过不变量。

    这条才是前缀测试的主体。一天一根K线地走过去，任何一天划出来的图都得合法
    ——哪怕它和前一天的不一样。
    """
    cases = _cached_day_bis()
    if not cases:
        pytest.skip("本地没有 ~/.chan_cache 日线缓存")
    for code, bis in cases:
        for n in range(4, len(bis) + 1):
            head = bis[:n]
            try:
                check_segs(build_segs(head), head)
            except Exception as e:  # noqa: BLE001 - 要把出错的票和笔数带出来
                raise AssertionError(f"{code} 前 {n} 笔划出非法线段: {e}") from e
