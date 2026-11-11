"""走势多义性。33课 p79、36课 pp.86-88。

验证的核心命题：**多义性是有限的、可枚举的**（36课 p88「多义性不是含糊性」），
所以程序能把全部合法读法算出来，模型只需要在里面选。
"""

from __future__ import annotations

from test_zhongshu import mk_legs

from chan.ambiguity import MAX_READINGS, MAX_SHIFT, enumerate_readings, is_ambiguous
from chan.zhongshu import MIN_LEGS, TREND_UP, build_zhongshus


def test_unique_reading_is_not_ambiguous():
    """只能这么读的走势，就该只给一条，不要制造假歧义。"""
    rs = enumerate_readings(mk_legs([10, 20, 12, 18]), require_confirmed=False)
    assert len(rs) == 1
    assert not is_ambiguous(rs)
    assert rs[0].zhongshus[0].zd == 12


def test_greedy_reading_is_always_enumerated():
    """`build_zhongshus` 的贪心读法必须在枚举结果里。

    两条路径共用 `zhongshu_at`，贪心就是「每层 shift=0」那条分支；枚举不到
    说明两者对「什么是合法中枢」的理解分叉了，必有一边是 bug。

    注意它**不保证排第一**——36课 p87 的「避繁就简」可能更偏好起手更晚但不
    出现延伸的组合。这不是 bug，这就是多义性。
    """
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    greedy = tuple(
        (z.start_leg, z.end_leg) for z in build_zhongshus(legs, require_confirmed=False)
    )
    rs = enumerate_readings(legs, require_confirmed=False)
    assert greedy in {r.key for r in rs}


def test_readings_are_deduped_and_sorted():
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30, 44, 31, 43])
    rs = enumerate_readings(legs, require_confirmed=False)
    assert len(rs) == len({r.key for r in rs})
    assert [r.score for r in rs] == sorted(r.score for r in rs)


def test_every_reading_is_internally_consistent():
    """每条读法里的中枢必须互不共段、按时间排好。"""
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30, 44, 31, 43])
    for r in enumerate_readings(legs, require_confirmed=False):
        for a, b in zip(r.zhongshus, r.zhongshus[1:]):
            assert a.end_leg < b.start_leg
        for z in r.zhongshus:
            assert z.leg_count >= MIN_LEGS


def test_alternative_start_produces_different_zhongshu():
    """33课 p79 第 677-678 行的实质：起手段不同，中枢区间就不同。

    原文的例子是同一段走势，中枢可以是 2911-2892（从 03071330 起），也可以是
    2947-2905（从 03091030 起），**两者的第三类买点位置不同**。这里用合成
    数据复现同一现象：至少有两条读法给出不同的第一个中枢区间。
    """
    # 开头多一段，使得「从第 0 段起」和「从第 1 段起」都能凑出中枢
    legs = mk_legs([16, 10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    rs = enumerate_readings(legs, require_confirmed=False)
    assert is_ambiguous(rs)
    zones = {(r.zhongshus[0].zd, r.zhongshus[0].zg) for r in rs if r.zhongshus}
    assert len(zones) > 1, f"没有枚举出区间不同的读法：{zones}"


def test_level_up_reading_ranks_last():
    """33课 p79：延伸到 9 段就该升级别看，这种读法最不可取，排序要靠后。"""
    pts = [10.0, 20.0]
    for i in range(12):
        pts.append(12.0 + 0.1 * i)
        pts.append(18.0 - 0.1 * i)
    rs = enumerate_readings(mk_legs(pts), require_confirmed=False)
    scored = [r for r in rs if r.has_level_up]
    clean = [r for r in rs if not r.has_level_up]
    for a in clean:
        for b in scored:
            assert a.score < b.score


def test_simplicity_beats_extension():
    """36课 p87：「尽量避繁就简……如果有组合使得不出现扩展，当然就采取该种组合」。"""
    legs = mk_legs([10, 20, 12, 18, 13, 19, 14, 50, 40, 48, 42, 46, 30])
    rs = enumerate_readings(legs, require_confirmed=False)
    if any(not r.has_extension for r in rs) and any(r.has_extension for r in rs):
        best = rs[0]
        assert not best.has_extension


def test_notes_are_populated():
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    for r in enumerate_readings(legs, require_confirmed=False):
        assert r.notes and all(isinstance(n, str) for n in r.notes)


def test_trend_is_computed_per_reading():
    """36课 p87：「当下采取什么组合，就要按该种组合的具体图形意义来判断」。

    走势类型必须跟着读法走，不能全局算一个。
    """
    legs = mk_legs([10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    rs = enumerate_readings(legs, require_confirmed=False)
    assert rs[0].trend == TREND_UP
    for r in rs:
        assert r.trend in ("up", "down", "panzheng")


def test_offsets_bounded():
    legs = mk_legs([16, 10, 20, 12, 18, 13, 50, 40, 48, 42, 46, 30])
    for r in enumerate_readings(legs, require_confirmed=False):
        assert all(0 <= o <= MAX_SHIFT for o in r.offsets)


def test_enumeration_is_bounded():
    """分支因子 3、深度等于中枢个数，必须有硬上限，否则长历史会指数爆炸。"""
    pts = [10.0]
    for i in range(40):
        pts.append(pts[-1] + (8 if i % 2 == 0 else -6))
    rs = enumerate_readings(mk_legs(pts), require_confirmed=False)
    assert len(rs) <= MAX_READINGS


def test_degenerate():
    """凑不出中枢时给**一条空读法**，而不是空列表。

    这样调用方永远能写 `readings[0]`，不用为「还没有中枢」单独分支。
    """
    for legs in ([], mk_legs([10, 20]), mk_legs([10, 20, 5])):
        rs = enumerate_readings(legs, require_confirmed=False)
        assert len(rs) == 1
        assert rs[0].zhongshus == []
        assert not is_ambiguous(rs)


def test_unconfirmed_tail_respected():
    legs = mk_legs([10, 20, 12, 18], last_unconfirmed=True)
    rs = enumerate_readings(legs, require_confirmed=True)
    assert all(r.zhongshus == [] for r in rs)


# --------------------------------------------------------------------------
# 第二层多义性：划好的中枢串怎么归成走势类型
#
# 17课 p36 只说「一个中枢是盘整、两个以上依次同向不重叠的中枢是趋势」，
# 它**没有**规定遇到一串同向中枢时必须全并成一个趋势。
# --------------------------------------------------------------------------


def _up_chain(n: int):
    """n 个依次升高、互不重叠的中枢。用来数切法。"""
    from chan.zhongshu import build_zhongshus

    pts = [0.0]
    for k in range(n):
        base = k * 100.0
        pts += [base + 30, base + 10, base + 25, base + 15, base + 120]
    legs = mk_legs(pts)
    zss = build_zhongshus(legs, require_confirmed=False)
    return zss


def test_a_chain_of_n_same_direction_zhongshus_has_2_to_the_n_minus_1_readings():
    """一串 n 个依次同向的中枢有 2^(n-1) 种合法切法——这是组合事实，不是口味。

    3 个同向中枢：[z0z1z2] [z0z1][z2] [z0][z1z2] [z0][z1][z2]，四种。
    从前只有头一种（`tree._greedy_groups`）和偶尔的第三种
    （`forward.trailing_trend`）露过面。
    """
    from chan.ambiguity import enumerate_groupings

    zss = _up_chain(3)
    assert len(zss) == 3, f"夹具失效了，造出来的是 {len(zss)} 个中枢"
    assert relation_chain_is_trend(zss)
    gs = enumerate_groupings(zss, limit=99)
    assert len(gs) == 4, [g.key for g in gs]
    assert {g.key for g in gs} == {
        ((0, 2),), ((0, 1), (2, 2)), ((0, 0), (1, 2)),
        ((0, 0), (1, 1), (2, 2)),
    }


def relation_chain_is_trend(zss) -> bool:
    from chan.zhongshu import TREND_DOWN, relation

    return all(relation(a, b) in (TREND_UP, TREND_DOWN)
               for a, b in zip(zss, zss[1:]))


def test_the_greedy_grouping_is_always_first():
    """首选必须恒等于 `tree._greedy_groups`，否则加这个枚举就把主口径换掉了。

    这一条是整次改动的安全带：`scan.report` 的 `trend` 走的是树的切分，
    枚举只负责**追加**备选。实测 30 只缓存日线票 × bi/seg 共 60 条序列全部
    一致，这里用合成夹具把它钉在单元测试里。
    """
    from chan.ambiguity import enumerate_groupings
    from chan.tree import _greedy_groups

    for n in (1, 2, 3, 4):
        zss = _up_chain(n)
        want, i = [], 0
        for kind, grp in _greedy_groups(zss):
            want.append((i, i + len(grp) - 1))
            i += len(grp)
        assert enumerate_groupings(zss, limit=99)[0].key == tuple(want), n


def test_simpler_readings_sort_first():
    """36课 p87「尽量避繁就简」：走势类型个数少的排前面。"""
    from chan.ambiguity import enumerate_groupings

    gs = enumerate_groupings(_up_chain(3), limit=99)
    assert [len(g.runs) for g in gs] == sorted(len(g.runs) for g in gs)


def test_limit_keeps_the_simple_readings_not_the_shredded_ones():
    """截断必须留下「简」的那些。

    `_walk_groups` 是深度优先 + 边走边截的，`_runs_at` 若按短的在前生成，
    前 `limit` 条就全是「每个中枢各自成一个盘整」那种最碎的切法，首选根本
    排不进来。这条钉的就是生成顺序。
    """
    from chan.ambiguity import enumerate_groupings

    zss = _up_chain(5)
    gs = enumerate_groupings(zss, limit=3)
    assert len(gs) == 3
    assert gs[0].key == ((0, 4),), gs[0].key
    assert all(len(g.runs) <= 2 for g in gs), [g.key for g in gs]


def test_last_run_differs_ignores_disagreement_that_is_all_in_the_past():
    """门槛盯的是**当下所在**的那一段，不是「有没有别的切法」。

    后者实测 58/60 为真，当筛子等于没筛子。这里造一个分歧全在前面的串：
    末段怎么切都一样，就该返回 False。
    """
    from chan.ambiguity import enumerate_groupings, last_run_differs

    zss = _up_chain(3)
    gs = enumerate_groupings(zss, limit=99)
    assert last_run_differs(gs)   # 同向串的末段当然可长可短

    # 只有一个中枢：唯一切法，没有多义
    assert not last_run_differs(enumerate_groupings(_up_chain(1), limit=99))


def test_empty_zhongshu_list_gives_no_groupings():
    from chan.ambiguity import enumerate_groupings

    assert enumerate_groupings([]) == []
