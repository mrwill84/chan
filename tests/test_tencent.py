"""腾讯 feed 的转换层。全程离线——`_get` 用假回包替掉。

这个源的危险之处和 baostock 不一样。baostock 出错会**报错**；腾讯出错会
**安静地给出一份看起来很正常的K线**：

- 行里的字段顺序是 `日期,开,收,高,低`——**收在高低前面**。照常见的 OHLC 顺序
  读，得到的每根K线高低开收全错位，但 `to_bars` 的体检全过（high 依然 >= low），
  划出来的笔和中枢全错，而且错得很像样。
- 回包的键名随接口变（`day` / `hfqday` / `m30`）。猜错键名拿到的是空列表，
  在批量拉取里会变成「这只票没有K线」，和退市票混在一起看不出来。
- 分钟线接口没有复权参数。不复权的除权缺口会造出假分型（base.py 第 1 条）。

所以下面测的几乎全是「**错了也不报错**」的那些点。
"""

from __future__ import annotations

import pytest

from feeds import Level
from feeds.base import Blacklisted
from feeds.tencent_feed import DAY_CAP, TencentFeed, _mts, _rows, _symbol, truncated

# 腾讯日线行：[日期, 开, 收, 高, 低, 量]
DAY_ROWS = [
    ["2026-10-14", "100.0", "108.0", "110.0", "99.0", "1000"],
    ["2026-10-15", "108.0", "104.0", "109.0", "103.0", "2000"],
    ["2026-10-16", "104.0", "112.0", "113.0", "104.0", "3000"],
]


def feed(monkeypatch, payload, **kw):
    """把 `_get` 换成固定回包，并记下被请求的 URL。"""
    seen: list[str] = []

    def fake_get(url, timeout):
        seen.append(url)
        return payload(url) if callable(payload) else payload

    import feeds.tencent_feed as mod

    monkeypatch.setattr(mod, "_get", fake_get)
    f = TencentFeed(sleep=0.0, **kw)
    return f, seen


def day_payload(rows=DAY_ROWS, sym="sz300308", key="hfqday"):
    return {"data": {sym: {"qt": {"ignore": 1}, key: rows}}}


# --------------------------------------------------------------------------
# 字段顺序：错了不会报错，只会把线划歪
# --------------------------------------------------------------------------


def test_close_comes_before_high_and_low_in_the_raw_row(monkeypatch):
    """腾讯的行是 `日期,开,收,高,低`。按 OHLC 读会全错位，而且体检照样通过。

    第一行 `开100 收108 高110 低99`：如果误按 `开,高,低,收` 读，会得到
    `高=108 低=110`——那样 `to_bars` 反倒会抓到 high<low。但第二行
    `开108 收104 高109 低103` 误读成 `高=104 低=109`……也会被抓到。
    真正危险的是第三行这种**收盘在区间上沿**的形态：误读成 `高=112 低=113`
    同样会被抓。所以这里直接把四个数都钉死，不指望体检兜底。
    """
    f, _ = feed(monkeypatch, day_payload())
    b = f.get_bars("300308", Level.DAY)[0]
    assert (b.open, b.high, b.low, b.close) == (100.0, 110.0, 99.0, 108.0)


def test_every_bar_keeps_its_own_values(monkeypatch):
    f, _ = feed(monkeypatch, day_payload())
    bars = f.get_bars("300308", Level.DAY)
    assert [b.close for b in bars] == [108.0, 104.0, 112.0]
    assert [b.high for b in bars] == [110.0, 109.0, 113.0]
    assert [b.idx for b in bars] == [0, 1, 2]


# --------------------------------------------------------------------------
# 回包解析
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["day", "hfqday", "qfqday", "m30"])
def test_the_rows_are_found_whatever_the_key_is_called(key):
    """键名随接口和复权方式变。按名字取，换个接口就静默变成「没有K线」。"""
    assert _rows(day_payload(key=key), "sz300308") == DAY_ROWS


def test_non_kline_fields_in_the_same_node_are_skipped():
    """回包里混着 `qt`、`prec` 这些非K线字段，不能撞上就返回。"""
    p = {"data": {"sz300308": {"prec": "4200", "qt": {"x": 1}, "hfqday": DAY_ROWS}}}
    assert _rows(p, "sz300308") == DAY_ROWS


def test_an_unknown_symbol_gives_no_rows_rather_than_a_crash():
    assert _rows(day_payload(), "sh600519") == []


@pytest.mark.parametrize("code,want", [
    ("600519", "sh600519"), ("300308", "sz300308"), ("000001", "sz000001"),
])
def test_symbol_carries_the_exchange_prefix(code, want):
    assert _symbol(code) == want


@pytest.mark.parametrize("code", ["830799", "920000", "430047"])
def test_beijing_is_refused_because_only_today_comes_back(code):
    """腾讯对北交所只回当天一根。放过去会**污染缓存**——一根也会落盘，
    之后 `pull --update` 看见文件存在就只做增量，这只票的「历史」就永远是
    从我们第一次拉它那天开始的几根，而且不再有任何提示。"""
    with pytest.raises(ValueError, match="北交所"):
        _symbol(code)


def test_minute_timestamps_become_comparable_strings():
    """`to_bars` 靠字符串比大小判时间递增，紧凑的 12 位数字也能比，但和日线
    的 `YYYY-MM-DD` 对不齐——区间过滤取 `ts[:10]`，格式不统一就筛不出来。"""
    assert _mts("202610161500") == "2026-10-16 15:00"
    assert _mts(202610160930) == "2026-10-16 09:30"


# --------------------------------------------------------------------------
# 三条硬限制，每一条都必须**明确报错**而不是静默降级
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", [Level.M30, Level.M5])
def test_minute_bars_are_refused_because_they_are_unadjusted(monkeypatch, level):
    """静默给一份不复权的分钟线，是这个源最容易犯的错。

    它不会报错，只会在除权那天造出一个假缺口、假分型，然后给出一个
    看起来完全正常的买卖点。
    """
    f, seen = feed(monkeypatch, day_payload())
    with pytest.raises(RuntimeError, match="不复权"):
        f.get_bars("300308", level)
    assert seen == [], "拦截必须发生在发请求之前"


def test_minute_bars_can_be_opted_into_explicitly(monkeypatch):
    rows = [["202610161430", "10.0", "10.8", "11.0", "9.9", "100"],
            ["202610161500", "10.8", "10.4", "10.9", "10.3", "200"]]
    f, seen = feed(monkeypatch, day_payload(rows, key="m30"),
                   allow_unadjusted_min=True)
    bars = f.get_bars("300308", Level.M30)
    assert [b.ts for b in bars] == ["2026-10-16 14:30", "2026-10-16 15:00"]
    assert (bars[0].high, bars[0].low, bars[0].close) == (11.0, 9.9, 10.8)
    assert "mkline" in seen[0], "分钟线走的是另一个接口"


def test_the_universe_is_refused_with_somewhere_else_to_go():
    """回空列表的话，`fullscan` 会「成功」扫描 0 只票。"""
    with pytest.raises(RuntimeError, match="代码表"):
        TencentFeed(sleep=0.0).list_universe()


# --------------------------------------------------------------------------
# 640 根上限
# --------------------------------------------------------------------------


def test_the_request_asks_for_the_cap_and_for_hfq(monkeypatch):
    f, seen = feed(monkeypatch, day_payload())
    f.get_bars("300308", Level.DAY)
    assert f"{DAY_CAP},hfq" in seen[0]


def test_hitting_the_cap_is_reported(monkeypatch):
    import datetime as dt

    d0 = dt.date(2024, 1, 1)
    rows = [[(d0 + dt.timedelta(days=i)).isoformat(),
             "10.0", "10.5", "11.0", "9.5", "1"] for i in range(DAY_CAP)]
    f, _ = feed(monkeypatch, day_payload(rows))
    bars = f.get_bars("300308", Level.DAY)
    assert len(bars) == DAY_CAP
    assert truncated(bars, Level.DAY), "撞上上限必须说出来，否则会被当成全历史"


def test_short_history_is_not_reported_as_truncated(monkeypatch):
    f, _ = feed(monkeypatch, day_payload())
    assert not truncated(f.get_bars("300308", Level.DAY), Level.DAY)


def test_the_date_window_is_applied_on_our_side(monkeypatch):
    """接口对 start/end 不可靠——给 2015 年它照样只回最近 640 根。

    所以筛选必须在这边再做一遍，否则调用方要的是一个月、拿到的是两年半，
    而两者都「有数据」，不会报错。
    """
    f, _ = feed(monkeypatch, day_payload())
    bars = f.get_bars("300308", Level.DAY, start="2026-10-15", end="2026-10-15")
    assert [b.ts for b in bars] == ["2026-10-15"]
    assert bars[0].idx == 0, "筛完要重新从 0 编号，引擎全程靠 idx 回溯"


# --------------------------------------------------------------------------
# 限流
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", [403, 429, 501, 502, 503])
def test_throttling_raises_blacklisted_not_a_plain_failure(monkeypatch, code):
    """封禁是**全局状态**。当成普通失败的话，`scan.puller` 会接住它，
    然后拿剩下五千次请求去撞同一堵墙。"""
    import urllib.error

    import feeds.tencent_feed as mod

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", code, "nope", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(Blacklisted):
        TencentFeed(sleep=0.0, retries=3).get_bars("300308", Level.DAY)


def test_501_is_throttling_not_a_missing_endpoint(monkeypatch):
    """`501 Not Implemented` 读起来像「这个接口没有」，实际是腾讯的限流话术。

    实测：前 385 只全正常，之后对**所有**代码一律 501，包括几分钟前刚拉成功
    过的 000001。当成普通失败的话，每只票退避重试三次再记一条失败，整轮会
    闷头撞墙十几分钟——这正是当时发生的事。
    """
    import urllib.error

    import feeds.tencent_feed as mod

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 501, "Not Implemented", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(Blacklisted, match="限流"):
        TencentFeed(sleep=0.0).get_bars("000001", Level.DAY)


def test_a_real_client_error_is_not_mistaken_for_throttling(monkeypatch):
    """404 这种是真·这一只的问题，不能把整轮停掉。"""
    import urllib.error

    import feeds.tencent_feed as mod

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "nope", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as e:
        TencentFeed(sleep=0.0, retries=2).get_bars("000001", Level.DAY)
    assert not isinstance(e.value, Blacklisted)


def test_the_sleep_floor_cannot_be_lowered_in_production():
    """`sleep=0.3` + 串行 ≈1.3 请求/秒，实测跑到第 385 只就被封。"""
    assert TencentFeed(sleep=0.3).sleep == TencentFeed.MIN_SLEEP
    assert TencentFeed().sleep == TencentFeed.MIN_SLEEP
    assert TencentFeed(sleep=5.0).sleep == 5.0, "调大是允许的"
    assert TencentFeed(sleep=0.0).sleep == 0.0, "测试里不发请求，留个零的口子"


def test_serial_is_the_ceiling():
    """并发只会更快撞墙。这个数被实测压到 1 了，不要再往上调。"""
    assert TencentFeed.max_jobs == 1


def test_blacklisted_is_not_retried(monkeypatch):
    """重试只会把封禁撞得更久。"""
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        raise Blacklisted("429")

    import feeds.tencent_feed as mod

    monkeypatch.setattr(mod, "_get", fake_get)
    with pytest.raises(Blacklisted):
        TencentFeed(sleep=0.0, retries=5).get_bars("300308", Level.DAY)
    assert len(calls) == 1


def test_ordinary_failures_are_retried_then_reported(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        raise TimeoutError("slow")

    import feeds.tencent_feed as mod

    monkeypatch.setattr(mod, "_get", fake_get)
    with pytest.raises(RuntimeError, match="重试 3 次"):
        TencentFeed(sleep=0.0, retries=3).get_bars("300308", Level.DAY)
    assert len(calls) == 3
