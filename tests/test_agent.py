"""模型那一层。

这里要防的东西和 `test_backtest.py` 不同。走查那边，未来数据要泄漏得先穿过
一个结构化的切片，比较难混进去；而提示词是**拼字符串**拼出来的，随手带进去
一句「这只票后来怎么样了」既不报错也不会让任何断言变红，只会让模型看起来
特别准。

所以主测试还是投毒，但这次比的是**提示词本身一个字都不能变**。
"""

from __future__ import annotations

import json

import pytest
from conftest import zigzag

from backtest.agent import SCHEMA, Call, judge, parse, prompt_at
from backtest.replay import Replay, _breached, _load_done
from chan.types import Bar
from feeds import Level
from scan.report import analyze_one

LV = Level.DAY


def _bars(n: int = 40, seed: int = 29):
    import random

    rng = random.Random(seed)
    pivots = [100.0]
    for i in range(n):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pivots.append(max(pivots[-1] + (step if i % 2 == 0 else -step), 10.0))
    return zigzag(pivots)


@pytest.fixture(scope="module")
def bars():
    return _bars()


def _poison(bars: list[Bar], t: int) -> list[Bar]:
    out = list(bars[: t + 1])
    for b in bars[t + 1:]:
        out.append(Bar(idx=b.idx, ts="2099-12-31", open=1e6, high=1e6,
                       low=1e6, close=1e6, volume=0.0))
    return out


# --------------------------------------------------------------------------
# 提示词不许带未来
# --------------------------------------------------------------------------


@pytest.mark.parametrize("t", [80, 100, 120])
def test_the_prompt_does_not_change_when_the_future_is_poisoned(bars, t):
    """第 t 步的提示词必须逐字只由前 t 根K线决定。

    这条红了，模型那部分的所有结论作废——它看到的东西里混进了答案。
    """
    if t >= len(bars) - 1:
        pytest.skip("夹具太短")
    assert prompt_at(bars, "000001", LV, t) == prompt_at(
        _poison(bars, t), "000001", LV, t)


@pytest.mark.parametrize("t", [80, 120])
def test_no_date_after_the_cursor_shows_up_in_the_prompt(bars, t):
    """投毒那条是整体比对，这条直接找未来的日期。

    两条测的是同一件事的不同面：投毒管「值有没有影响结论」，这条管
    「有没有把未来的日期直接印出来」。后者更蠢也更容易犯。
    """
    p = prompt_at(bars, "000001", LV, t)
    for b in bars[t + 1:]:
        assert b.ts not in p, f"提示词里出现了 {b.ts}，那是第 t 根之后的"


def test_the_point_under_judgement_is_always_shown(bars):
    """`focus` 的点必须出现在正文里并打上星号。

    漏掉的话模型会去判最后那个点，而我们拿它的答案去对 focus 的收益——
    错得完全没有痕迹。
    """
    seen = 0
    for t in range(25, len(bars)):
        r = analyze_one(bars[: t + 1], "000001", LV, zs_from="bi",
                        include_quasi=True, recent=None)
        if "error" in r or not r["bspoints"]:
            continue
        for p in r["bspoints"]:
            key = (p["kind"], p["side"], p["bar"])
            # `zs_from` 两边必须一样。点是在 bi 上算出来的，提示词也得用 bi
            # 去划，否则 focus 的 key 在另一批中枢上根本不存在。以前两处的
            # 默认值碰巧都是 "bi"，不传也能过；默认值改成 "seg" 之后（和
            # scan/plot 对齐，C-zslevel）这个隐含耦合就露出来了。
            text = prompt_at(bars, "000001", LV, t, zs_from="bi", focus=key)
            assert "←← 就判断这一个" in text
            assert p["ts"] in text
            seen += 1
        if seen > 30:
            return
    assert seen, "夹具里没走出买卖点，这条测试失效了"


def test_focusing_on_a_point_that_is_not_there_is_an_error(bars):
    """宁可抛，也不要默默生成一个没打星号的提示词。"""
    with pytest.raises(ValueError):
        prompt_at(bars, "000001", LV, 120, focus=(9, "buy", 3))


@pytest.mark.parametrize("t", [80, 120])
def test_the_blind_prompt_mentions_no_buy_or_sell_point(bars, t):
    """对照组的正文里不许留下任何买卖点痕迹。

    漏一处，「蒙住后模型还同意引擎吗」这个问题就白问了——而最容易漏的是
    `reasons`，里面到处是「类1买」「三买」。所以蒙住靠的是**整段删掉**，
    这条测试就是看有没有真的删干净。
    """
    p = prompt_at(bars, "000001", LV, t, blind=True)
    body = p.split("（本次")[0]
    for w in ("买", "卖", "背驰", "reasons"):
        assert w not in body, f"蒙住版正文里还有「{w}」"


def test_blind_and_focus_cannot_be_combined(bars):
    """蒙住了就没有「那一个点」，同时给是调用方搞错了，别静默忽略。"""
    with pytest.raises(ValueError):
        prompt_at(bars, "000001", LV, 120, blind=True, focus=(1, "buy", 10))


@pytest.mark.parametrize("t", [80, 120])
def test_the_blind_prompt_also_hides_the_future(bars, t):
    """蒙住组是另一条代码路径，不保证自动继承不偷看未来这条性质。"""
    if t >= len(bars) - 1:
        pytest.skip("夹具太短")
    assert prompt_at(bars, "000001", LV, t, blind=True) == prompt_at(
        _poison(bars, t), "000001", LV, t, blind=True)


def test_the_schema_asks_for_an_invalidation_price(bars):
    """失效价是唯一能事后独立核对的字段，不能从模板里掉出去。"""
    assert "invalidation" in SCHEMA
    assert "invalidation" in prompt_at(bars, "000001", LV, 120)


# --------------------------------------------------------------------------
# 解析：本地模型不会乖乖只输出 JSON
# --------------------------------------------------------------------------


_GOOD = ('{"direction":"up","horizon":10,"invalidation":12.5,'
         '"confidence":0.8,"why":"20课p43"}')


@pytest.mark.parametrize("wrap", [
    "{raw}",
    "```json\n{raw}\n```",
    "我的判断如下：\n{raw}\n希望对你有帮助。",
    "<think>想了很久</think>\n{raw}",
])
def test_the_json_is_dug_out_of_whatever_the_model_wrapped_it_in(wrap):
    """包一层解释、套个代码块、带个思维链——都不算失败。"""
    c = parse(wrap.format(raw=_GOOD))
    assert c.ok and c.direction == "up" and c.invalidation == 12.5


@pytest.mark.parametrize("raw", ["完全没有 JSON", "{这不是合法的}", ""])
def test_unparseable_is_recorded_as_failure_not_as_unclear(raw):
    """「模型说不知道」和「我们没读懂它」在统计里是两件事。

    混成一个，报告里就看不出 unclear 多是模型谨慎还是我们解析烂。
    """
    c = parse(raw)
    assert not c.ok and c.error


def test_junk_fields_are_clamped_rather_than_crashing():
    """27B 会给出 confidence=5、horizon="十天" 这种东西。整轮不该为此中断。"""
    c = parse('{"direction":"sideways","horizon":"十天",'
              '"invalidation":"不确定","confidence":5}')
    assert c.ok
    assert c.direction == "unclear"      # 不认识的方向退回 unclear
    assert c.horizon == 0
    assert c.invalidation is None
    assert c.confidence == 1.0           # 夹到 [0,1]


# --------------------------------------------------------------------------
# 对账
# --------------------------------------------------------------------------


def _call(direction: str) -> Call:
    return Call(t=0, ok=True, direction=direction)


@pytest.mark.parametrize("side,direction,realized,want", [
    # realized 是「对信号有利」的收益：买点有利=涨，卖点有利=跌
    ("buy", "up", 0.05, "hit"),
    ("buy", "up", -0.05, "miss"),
    ("buy", "down", -0.05, "hit"),
    ("sell", "down", 0.05, "hit"),    # 卖点 +5% 有利 = 价格跌了 = 说 down 对
    ("sell", "up", 0.05, "miss"),
    ("sell", "up", -0.05, "hit"),     # 卖点 -5% 不利 = 价格涨了 = 说 up 对
])
def test_the_sell_side_is_flipped_back_before_judging(side, direction,
                                                      realized, want):
    """卖点这里漏翻一次符号，看空的判断会全部报反——而且胜率看着还不错。"""
    assert judge(_call(direction), side, realized) == want


def test_saying_i_do_not_know_is_never_counted_as_wrong():
    """算成错会鼓励模型瞎猜，而瞎猜的 50% 在报告里比老实说不知道好看。"""
    assert judge(_call("unclear"), "buy", 0.05) is None
    assert judge(_call("unclear"), "buy", -0.05) is None
    assert judge(Call(t=0, ok=False), "buy", 0.05) is None


def test_a_point_with_no_future_is_not_judged():
    assert judge(_call("up"), "buy", None) is None


# --------------------------------------------------------------------------
# 失效价 / 续跑
# --------------------------------------------------------------------------


def test_the_invalidation_is_checked_on_the_right_side(bars):
    """买点的失效价在下方、卖点在上方。搞反的话打穿率会恒为 0 或 1。"""
    lo = min(b.low for b in bars[101:121])
    hi = max(b.high for b in bars[101:121])
    assert _breached(lo + 1e-6, "buy", bars, 100, 20) is True
    assert _breached(lo - 1.0, "buy", bars, 100, 20) is False
    assert _breached(hi - 1e-6, "sell", bars, 100, 20) is True
    assert _breached(hi + 1.0, "sell", bars, 100, 20) is False


def test_no_invalidation_means_unknown_not_safe(bars):
    """模型没给失效价，不能算「没被打穿」——那会让它显得比给了的更稳。"""
    assert _breached(None, "buy", bars, 100, 20) is None


def test_a_half_written_line_does_not_block_resuming(tmp_path):
    """中断发生在写一行的中间是常态。坏行跳过，好行要留住。"""
    f = tmp_path / "r.jsonl"
    good = Replay(kind=3, side="buy", label="3买", bar=83, ts="2024-06-28",
                  first_seen=104, first_seen_ts="2024-07-29",
                  ok=True, direction="up")
    from dataclasses import asdict
    f.write_text(json.dumps(asdict(good), ensure_ascii=False) + "\n"
                 + '{"kind":1,"side":"bu',  # 崩在这里
                 encoding="utf-8")
    done = _load_done(f)
    assert list(done) == [(3, "buy", 83)]
    assert done[(3, "buy", 83)].first_seen_ts == "2024-07-29"


def _row(bar: int, *, ok: bool, direction: str = "up") -> str:
    from dataclasses import asdict
    r = Replay(kind=1, side="buy", label="1买", bar=bar, ts="2024-01-01",
               first_seen=bar, first_seen_ts="2024-01-02", ok=ok,
               direction=direction if ok else "unclear",
               error="" if ok else "Connection error")
    return json.dumps(asdict(r), ensure_ascii=False)


def test_a_failed_call_is_retried_rather_than_counted_as_done(tmp_path):
    """模型服务挂掉时，几十个点会在一分钟内各记一条失败。

    要是续跑把这些当「问过了」，报告会看起来完整但少掉**连续的一整段**
    样本——偏得比随机丢样本厉害得多。
    """
    f = tmp_path / "r.jsonl"
    f.write_text("\n".join([_row(1, ok=True), _row(2, ok=False)]) + "\n",
                 encoding="utf-8")
    assert list(_load_done(f)) == [(1, "buy", 1)]


@pytest.mark.parametrize("order", [["fail", "good"], ["good", "fail"]])
def test_a_later_failed_retry_does_not_erase_a_good_answer(tmp_path, order):
    """同一个点两条记录，不管哪条在前，留下的都该是成功那条。"""
    f = tmp_path / "r.jsonl"
    rows = {"good": _row(7, ok=True, direction="down"), "fail": _row(7, ok=False)}
    f.write_text("\n".join(rows[k] for k in order) + "\n", encoding="utf-8")
    done = _load_done(f)
    assert done[(1, "buy", 7)].ok
    assert done[(1, "buy", 7)].direction == "down"


# --------------------------------------------------------------------------
# 复判汇总
# --------------------------------------------------------------------------


def _pair(bar: int, side: str, direction: str, ret: float | None, h: int = 5):
    """造一条「模型说了 direction、真实收益是 ret」的记录 + 对应的 Outcome。"""
    from backtest.score import Outcome
    from backtest.walk import Sighting

    s = Sighting(kind=1, side=side, label="1买" if side == "buy" else "1卖",
                 bar=bar, ts="2024-01-01", price=10.0,
                 first_seen=bar, last_seen=bar)
    o = Outcome(s, bar, 10.0, {} if ret is None else {h: ret}, 0.0, 0.0, {},
                enough_future=ret is not None)
    r = Replay(kind=1, side=side, label=s.label, bar=bar, ts=s.ts,
               first_seen=bar, first_seen_ts=s.ts, ok=True,
               direction=direction, confidence=0.5)
    if direction != "unclear":
        r.agrees = (direction == "up") == (side == "buy")
        v = judge(Call(t=0, ok=True, direction=direction), side, ret)
        if v:
            r.verdict[str(h)] = v
    return r, o


def test_agreement_is_measured_only_over_answers_that_took_a_side(bars):
    """`unclear` 不该拉低也不该抬高「和引擎一致率」——它没表态。"""
    from backtest.replay import summarize_replay

    pairs = [_pair(10, "buy", "up", 0.05), _pair(11, "buy", "down", -0.05),
             _pair(12, "buy", "unclear", 0.05)]
    s = summarize_replay([p[0] for p in pairs], [p[1] for p in pairs], bars)
    assert s["unclear"] == 1
    assert s["agree_with_engine"] == pytest.approx(0.5)  # 3 个里只算表态的 2 个


def test_the_base_follows_the_direction_the_model_actually_said(bars):
    """模型全说跌时，基准要用做空基准；全按做多比会凭空送它一大截 edge。"""
    from backtest.replay import summarize_replay
    from backtest.score import base_rate

    pairs = [_pair(b, "sell", "down", 0.05) for b in (10, 11, 12)]
    s = summarize_replay([p[0] for p in pairs], [p[1] for p in pairs], bars)
    assert s["h"]["5"]["base"] == pytest.approx(base_rate(bars, 5, "sell")[0])
    assert s["h"]["5"]["n_up"] == 0


def test_the_split_separates_agreement_from_disagreement(bars):
    """这是回答「挂模型值不值」的那张表，分组不能错。"""
    from backtest.replay import summarize_replay

    pairs = [_pair(10, "buy", "up", 0.10),    # 同意，赚
             _pair(11, "buy", "up", 0.20),    # 同意，赚
             _pair(12, "buy", "down", -0.30)]  # 反对，（对信号而言）亏
    s = summarize_replay([p[0] for p in pairs], [p[1] for p in pairs], bars)
    split = s["split"]["5"]
    assert split["同意"]["n"] == 2
    assert split["同意"]["mean_ret"] == pytest.approx(0.15)
    assert split["反对"]["n"] == 1
    assert split["反对"]["mean_ret"] == pytest.approx(-0.30)


def test_a_run_where_everything_failed_still_renders(bars):
    """服务整段挂掉时，报告要说「没有可用判断」，而不是抛异常或印 0%。"""
    from backtest.replay import render_replay, summarize_replay

    r = Replay(kind=1, side="buy", label="1买", bar=10, ts="2024-01-01",
               first_seen=10, first_seen_ts="2024-01-01", ok=False,
               error="Connection error")
    out = render_replay(summarize_replay([r], [], bars))
    assert "失败 1" in out
