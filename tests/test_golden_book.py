"""书中图解示范课当 golden case（验证策略第三层）。

前面几层测的都是**自洽**：不变量断言、随机行情、多空镜像、新老笔偏序——
它们能抓住「实现和自己打架」，抓不住「实现和原文系统性地不是一回事」。
全部自洽、整体偏移一格，上面那些一条都不会响。

这一层不一样：用的是作者自己在 54~61 课里，对着真实行情、带着具体点位
标注出来的结论。数字是他写下的，不是我们推出来的。

**为什么是 56/57/61 这三课。** 图解示范一共六个（54~61 课），但大部分只有
文字描述加一张图——图在 PDF 里是位图，提取不出点位。只有这三处把关键价位
直接写进了正文：

- 56 课 p133-134：1 分钟中枢「区间在 4087 到 4122 点」
- 57 课 p136：「56 段只到了 4077，所以这就是第三类卖点」
- 61 课 p154：三类买卖点「至少需要有 5 段次级别的」

所以这里只收这三条，不去猜图上的位置。**宁可少，不可编**——拿一个猜出来
的点位当 golden case，比没有 golden case 更糟。

行情是按原文点位手工复原的，不是真的 2007 年上证分时数据（那个拿不到了）。
复原的是**几何关系**：哪两段重叠、重叠在哪、回抽到了哪里。买卖点的定义本来
就只看这些。
"""

from __future__ import annotations

import random

import pytest
from conftest import mk_bis

from chan import analyze
from chan.buysell import THIRD, find_bspoints
from chan.types import Direction
from chan.zhongshu import build_zhongshus, legs_from_bis
from test_stress import random_bars

# --------------------------------------------------------------------------
# 56 课 + 57 课：530 印花税行情，1 分钟中枢 [4087, 4122] 与它的第三类卖点
# --------------------------------------------------------------------------

#: 56/57 课复原出来的转折点位，依次成笔。
#:
#: 原文（56 课 p134）：「ABC 三段就有了重叠，因此就构成了一个 1 分钟的中枢，
#: 区间在 4087 到 4122 点。」次日（57 课 p136）：「56 段只到了 4077」，而
#: 「今天一大早的上冲没有触及 4087 点，所以就构成了该中枢的第三类卖点」。
#:
#: 落到点位上就是下面这串。
#:
#: 尾巴上那两段（4077→4000→4050）是凑出来的，原文只说「后面理所当然的下跌」。
#: 但少了它们这个 case 根本不成立，原因有两个，都是机制上的：
#:
#: 1. 18课 p40 判「离开」要**连续两段**不触及 [ZD, ZG]。回抽段自己是第一段，
#:    所以后面必须再有一段，否则 `depart_leg` 永远是 None，三卖出不来。
#: 2. 末段永远未确认、不参与构成中枢，所以还得再多垫一段，回抽段才轮得到
#:    `confirmed=True`。
#:
#: 中间那几段 4087→4110→4070→4105 是中枢延伸。它们也不能省，有两个用处：
#:
#: - 少了它们，「连续两段不触及」里的第一段就轮到离开段头上，中枢的第三段
#:   会被算成离开段，整个形态退化成 C18 那种 4 段的情形。
#: - 其中 4070 那一下把 DD 压到 ZD 之下，这样 DD(4070) 和 ZD(4087) 才是两个
#:   不同的数，`test_..._zd_not_dd` 才真的能区分出用错了哪一个。两者相等的
#:   夹具上，那条测试是恒真的。
LESSON_56 = [4150.0, 4087.0, 4122.0, 4087.0, 4110.0, 4070.0, 4105.0,
             4030.0, 4077.0, 4000.0, 4050.0]

ZG_56, ZD_56 = 4122.0, 4087.0
PULLBACK_56 = 4077.0


@pytest.fixture
def lesson56():
    bis = mk_bis(LESSON_56)
    legs = legs_from_bis(bis)
    return bis, legs, build_zhongshus(legs)


def test_the_zhongshu_interval_matches_the_text(lesson56):
    """「区间在 4087 到 4122 点」——ZG/ZD 必须正好是这两个数。

    原文（第 348 行）给了两个等价写法：一般式 `[max(a2,b2,c2), min(a1,b1,c1)]`
    是**三段的公共重叠区间**，简化式 `ZG=min(g1,g2), ZD=max(d1,d2)` 里的 1、2
    指的是前两个 **Z 走势段**——「与中枢方向一致的次级别走势类型」，不是时间
    上的前两段。原文自己证了两者相等（回升形成的中枢有 a1=b1、b2=c2）。

    这里特意记一笔，是因为把简化式里的「前两段」读成时间顺序的前两段，是个
    读起来毫无破绽的错：方向一致的那两段恰好常常就排在前面，大部分行情上两
    种算法给的数一模一样，只有第三段明显收窄时才分道扬镳。
    """
    _, _, zss = lesson56
    assert len(zss) == 1, "ABC 三段应当只构成一个中枢"
    z = zss[0]
    assert (z.zd, z.zg) == (ZD_56, ZG_56)


def test_the_pullback_that_fails_to_reach_zd_is_a_third_sell(lesson56):
    """「56 段只到了 4077」→ 没够着 ZD=4087 → 三卖。"""
    bis, legs, zss = lesson56
    pts = [p for p in find_bspoints(legs, zss, bis) if p.kind is THIRD]
    assert len(pts) == 1, f"应当只有一个三类点，得到 {pts}"
    p = pts[0]
    assert not p.is_buy, "向下离开中枢、回抽不破 ZD，是三**卖**"
    assert p.price == PULLBACK_56
    assert p.confirmed


def test_a_pullback_that_does_reach_zd_is_not_a_third_sell():
    """反证：把回抽从 4077 抬到 4090（> ZD 4087），信号必须消失。

    原文的判据是「没有触及 4087 点」，所以 4087 这个数必须是真门槛，不能是
    个附近的近似。只差 13 个点，结论就得反过来。

    触及之后其实连「离开」都不成立了——回抽重新进了中枢，`depart_leg` 退回
    None，这一段是中枢震荡的一部分。所以这里不是「有信号但被判掉」，而是
    **形态压根没形成**，两种口径下都不该有点。
    """
    prices = list(LESSON_56)
    prices[prices.index(PULLBACK_56)] = 4090.0
    bis = mk_bis(prices)
    legs = legs_from_bis(bis)
    pts = [p for p in find_bspoints(legs, build_zhongshus(legs), bis) if p.kind is THIRD]
    assert pts == [], "回抽升破了 ZD，不该再有三卖"


def test_the_third_sell_is_measured_against_zd_not_dd(lesson56):
    """三卖比 ZD，不比 DD。这是 SKILL.md 里点名的头号读错。

    这个夹具上 DD=4030（离开段的低点），ZD=4087。拿 DD 当门槛的话，
    4077 高于 4030，信号会被判掉——结论和原文正好相反。
    """
    _, _, zss = lesson56
    z = zss[0]
    assert z.dd < z.zd, "夹具本身要能区分这两个数，否则这条测试是空的"
    assert PULLBACK_56 < z.zd, "原文：没有触及 4087"
    assert PULLBACK_56 > z.dd, "而它确实高于 DD——所以用 DD 判就会得出相反结论"


# --------------------------------------------------------------------------
# 61 课 p154：三类买卖点的 5 段结构
# --------------------------------------------------------------------------


def test_a_third_point_always_sits_on_the_fifth_leg(lesson56):
    """61 课 p154 原文：

      「一个第三类买卖点，至少需要有 5 段次级别的，前三段构成中枢，
        第四段离开中枢，第 5 段构成第三类买卖点。」

    这是**独立于我们的推导**的一条结构约束。实现里它是从「中枢至少三段 +
    离开段 + 回抽段」推出来的副产品，没有谁直接写过 5 这个数；哪天有人把
    中枢的起手条件放宽，这里会立刻响。
    """
    bis, legs, zss = lesson56
    p = [x for x in find_bspoints(legs, zss, bis) if x.kind is THIRD][0]
    z = zss[p.zs_index]
    assert p.leg - z.start_leg >= 4, "中枢起手算第 1 段，三类点至少落在第 5 段"
    assert z.depart_leg == p.leg - 1, "离开段紧挨着三类点"
    assert not p.ambiguous, "这是标准的 5 段以上形态，不该打 C18 标记"


@pytest.mark.parametrize("gappy", [False, True], ids=["clean", "gappy"])
def test_short_third_points_are_flagged_not_silently_emitted(gappy: bool):
    """61 课的 5 段要求和 18 课的离开机制对不上，这里把那道缝钉死。

    18 课 p40 判定「离开」的条件是**连续两段不触及** [ZD, ZG]。而三类点的回抽
    按定义就不重回中枢，所以它自己就贡献了「不触及」的第一段——于是中枢的
    第三段完全可能同时就是离开段（C-core 已登记这个形态），四段走完就给出
    一个三类点，比 61 课要求的 5 段少一段。

    两边都是原文，没法都满足。取 18 课的宽口径，但 4 段的必须带 `ambiguous`
    ——这种形态里「围绕中枢的震荡」根本没发生过，中枢刚成立就被自己的第三段
    带走了，信号弱得多。

    **这条测试的要害不是「允许 4 段」，是「4 段不许装成 5 段」。** 真正要防的
    是它悄悄混进扫描结果，和标准形态并排显示、看不出区别。
    """
    seen_short = 0
    for case in range(30):
        rng = random.Random(case * 70001 + int(gappy))
        a = analyze(random_bars(rng, gappy=gappy), check=True)
        legs = legs_from_bis(a.bis)
        zss = build_zhongshus(legs)
        for p in find_bspoints(legs, zss, a.bis):
            if p.kind is not THIRD:
                continue
            z = zss[p.zs_index]
            assert z.depart_leg is not None and z.depart_leg == p.leg - 1
            n = p.leg - z.start_leg + 1
            assert n >= 4, f"case={case} 三类点只用了 {n} 段，连 4 段都不到"
            if n < 5:
                seen_short += 1
                assert p.ambiguous, (
                    f"case={case} 只用了 {n} 段却没打 C18 标记——"
                    f"61课 p154 要求至少 5 段"
                )
                assert any("C18" in r for r in p.reasons)
    assert seen_short, "随机行情里一个 4 段形态都没出现，这条测试没测到东西"


def test_second_and_third_may_coincide_but_first_may_not():
    """61 课 p154：74 点「既是一个第二类卖点，又是一个第三类卖点」。

    所以 2 和 3 同点是**合法**的，不是该去重的重复信号——原文还说这种时候
    「后面的力度值得关注」。而 21 课 p45 明说 1 与 2、1 与 3 不可能重合。

    这里验的是 `check_bspoints` 的取向：它必须放过 2+3，拦住 1+2 / 1+3。
    那条拦截已经有单测，这里补的是**不该拦**的那一半——只测拦截的话，
    一个「把所有同点都判违规」的实现能全绿。
    """
    from chan.buysell import FIRST, SECOND, BSPoint
    from chan.invariants import InvariantError
    from chan.buysell import check_bspoints

    def pt(kind, leg, bar):
        return BSPoint(kind=kind, is_buy=False, leg=leg, bar=bar, price=100.0,
                       zs_index=0, source="test")

    check_bspoints([pt(SECOND, 4, 10), pt(THIRD, 4, 10)])  # 不该抛

    with pytest.raises(InvariantError):
        check_bspoints([pt(FIRST, 4, 10), pt(SECOND, 4, 10)])
    with pytest.raises(InvariantError):
        check_bspoints([pt(FIRST, 4, 10), pt(THIRD, 4, 10)])


# --------------------------------------------------------------------------
# 61 课 p154：背驰的前置门槛
# --------------------------------------------------------------------------


def test_no_new_extreme_means_no_beichi():
    """61 课 p154：「因为背驰如果没有创新高，是不存在的。」

    原文用这句话解释「走到 69 时并不构成任何背驰」。它是**前置门槛**不是
    加分项：MACD 面积再怎么萎缩，没创新高就根本谈不上背驰。

    实现里这道门槛在 `beichi.py`，这里从外部确认它确实拦得住——构造一个
    c 段不创新高的 a+A+b+B+c，断言出不来 1 类点。
    """
    import numpy as np

    from chan.beichi import detect_all
    from chan.macd import macd as compute

    # 上涨趋势：两个中枢依次抬高，但最后一段**没有**越过前高
    prices = [100.0, 130.0, 110.0, 140.0, 120.0,   # 中枢 A
              170.0, 150.0, 180.0, 160.0,          # 中枢 B（整体抬高）
              175.0, 165.0]                        # c 段只到 175 < 180
    bis = mk_bis(prices)
    legs = legs_from_bis(bis)
    zss = build_zhongshus(legs)
    n = max(b.end.peak_idx for b in bis) + 1
    m = compute(np.linspace(100.0, 175.0, n))

    for b in detect_all(legs, zss, bis, m):
        if b.direction is Direction.UP:
            pytest.fail(f"c 段没创新高却报了顶背驰：{b}")
