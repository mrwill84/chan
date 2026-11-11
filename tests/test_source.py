"""数据源分目录，以及 baostock 那层薄包装。

测的重点不是「能不能拉到数据」——那要联网，而且联网测试会在别人的 CI 上
莫名其妙地红。测的是**两个源不会混**：混了不报错，只是价格悄悄换了一套
坐标系，这种错查起来要命（见 CONVENTIONS C-src）。

baostock 本体用假对象替掉。要验的是我们这边的转换和过滤，不是人家的接口。
"""

from __future__ import annotations

import pytest
from conftest import zigzag

from feeds import Level, cache
from feeds.akshare_feed import AkshareFeed
from feeds.baostock_feed import BaostockFeed, _bs_code, _ts

LV = Level.DAY


# --------------------------------------------------------------------------
# 缓存分目录
# --------------------------------------------------------------------------


def test_the_source_is_in_the_path(tmp_path):
    p = cache.path_for("600519", LV, tmp_path, source="baostock")
    assert p.parent.parent.name == "baostock"


def test_two_sources_do_not_see_each_others_bars(tmp_path):
    """这是整条约定的点：换了源就该是「缓存里没有」，不是拿到另一套价格。"""
    cache.save("600519", LV, zigzag([100.0, 110.0, 105.0, 120.0]), tmp_path,
               source="akshare")
    assert cache.load("600519", LV, tmp_path, source="akshare")
    assert cache.load("600519", LV, tmp_path, source="baostock") == []


def test_the_same_code_can_be_cached_under_both_sources(tmp_path):
    cache.save("600519", LV, zigzag([100.0, 110.0, 105.0]), tmp_path, source="akshare")
    cache.save("600519", LV, zigzag([200.0, 220.0, 210.0]), tmp_path, source="baostock")
    a = cache.load("600519", LV, tmp_path, source="akshare")
    b = cache.load("600519", LV, tmp_path, source="baostock")
    assert a[0].close != b[0].close


def test_feeds_declare_their_source():
    from feeds.tencent_feed import TencentFeed

    assert AkshareFeed.source == "akshare"
    assert BaostockFeed.source == "baostock"
    assert TencentFeed.source == "tencent"
    assert cache.DEFAULT_SOURCE == "baostock"


def test_make_feed_knows_every_source():
    """`make_feed` 漏掉一个源的症状是「`--source` 换了没生效」，很难看出来。"""
    from feeds import make_feed

    for name in ("baostock", "akshare", "tencent"):
        assert make_feed(name).source == name
    with pytest.raises(ValueError, match="不认识的数据源"):
        make_feed("yahoo")


def test_a_feed_without_a_source_is_refused(tmp_path):
    """默认成某个源的话，忘了声明的新 feed 会把数据写进别人的目录。"""

    class Nameless:
        def get_bars(self, code, level, start=None, end=None):
            return zigzag([100.0, 110.0, 105.0])

    with pytest.raises(ValueError, match="source"):
        cache.get_or_fetch(Nameless(), "600519", LV, root=tmp_path)


def test_get_or_fetch_writes_into_the_feeds_own_directory(tmp_path):
    class Fake:
        source = "baostock"

        def get_bars(self, code, level, start=None, end=None):
            return zigzag([100.0, 110.0, 105.0])

    cache.get_or_fetch(Fake(), "600519", LV, root=tmp_path)
    assert cache.path_for("600519", LV, tmp_path, source="baostock").exists()
    assert not cache.path_for("600519", LV, tmp_path, source="akshare").exists()


# --------------------------------------------------------------------------
# 扫描：源是身份的一部分
# --------------------------------------------------------------------------


def test_switching_source_refuses_to_resume(tmp_path):
    from scan.scanner import ConfigMismatch, run_scan

    bars = zigzag([100.0 + (i % 2) * 20 for i in range(80)])
    for src in ("akshare", "baostock"):
        cache.save("000001", LV, bars, tmp_path / "cache", source=src)

    out = tmp_path / "run.jsonl"
    kw = dict(root=tmp_path / "cache", within=10**6)
    run_scan(["000001"], LV, out, source="baostock", **kw)
    with pytest.raises(ConfigMismatch, match="source"):
        run_scan(["000001"], LV, out, source="akshare", **kw)


def test_the_failure_message_names_the_source(tmp_path):
    """「缓存里没有」在两个源的世界里是半句话——没说是哪个源没有。"""
    from scan.scanner import read_progress, run_scan

    out = tmp_path / "run.jsonl"
    run_scan(["000001"], LV, out, root=tmp_path / "cache", source="baostock")
    assert "baostock" in read_progress(out).done["000001"]["error"]


# --------------------------------------------------------------------------
# baostock 的转换层
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code,want", [
    ("600519", "sh.600519"), ("000001", "sz.000001"), ("300750", "sz.300750"),
])
def test_code_is_prefixed_by_exchange(code, want):
    assert _bs_code(code) == want


def test_beijing_is_refused_with_a_reason():
    """静默回空列表的话，北交所的票会表现成「这只票没有K线」。"""
    with pytest.raises(ValueError, match="北交所"):
        _bs_code("830799")


def test_minute_timestamp_is_the_bar_close():
    """17 位里只有前 14 位有用。收盘时刻这个口径和东财一致，换源时间戳才对得上。"""
    assert _ts("2024-01-02", "20240102100000000") == "2024-01-02 10:00:00"
    assert _ts("2024-01-02", "20240102093500000") == "2024-01-02 09:35:00"


def test_day_rows_keep_the_plain_date():
    assert _ts("2024-01-02", "") == "2024-01-02"


class _FakeRS:
    """baostock 的游标形态：`error_code` + `next()` + `get_row_data()`。"""

    def __init__(self, rows, error_code="0"):
        self._rows, self._i = rows, -1
        self.error_code = error_code
        self.error_msg = "" if error_code == "0" else "boom"

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return self._rows[self._i]


class _FakeBS:
    def __init__(self, rows, error_code="0"):
        self.rows, self.error_code = rows, error_code
        self.calls = []

    def query_history_k_data_plus(self, code, fields, **kw):
        self.calls.append((code, fields, kw))
        return _FakeRS(self.rows, self.error_code)


class _TruncatingBS(_FakeBS):
    """第一次回一整页就断（翻页请求失败），重查才给全。

    这是实测过的真实行为：4 个进程并发拉，600050 回 4000 根、600051 回 2000
    根，串行是 5832 和 7156。`error_code` 全程是 `"0"`。
    """

    def __init__(self, full, page):
        super().__init__(full)
        self.full, self.page = full, page

    def query_history_k_data_plus(self, code, fields, **kw):
        self.calls.append((code, fields, kw))
        rows = self.full[: self.page] if len(self.calls) == 1 else self.full
        return _FakeRS(rows)


def _day_rows(n, start=1):
    return [[f"2024-{(start + i) // 28 + 1:02d}-{(start + i) % 28 + 1:02d}",
             "10", "12", "9", "11", "100"] for i in range(n)]


def _feed_with(monkeypatch, bs):
    import feeds.baostock_feed as mod

    monkeypatch.setattr(mod, "_login", lambda: None)
    monkeypatch.setattr(mod, "RETRY_WAIT", 0.0)  # 重试的抖动不该拖慢测试
    monkeypatch.setitem(__import__("sys").modules, "baostock", bs)
    return BaostockFeed()


def test_suspended_rows_are_dropped_not_zero_filled(monkeypatch):
    """补零会造出平盘K线，被包含处理吃掉，打乱K线计数（CONVENTIONS 六）。"""
    rows = [
        ["2024-01-02", "10", "12", "9", "11", "100"],
        ["2024-01-03", "", "", "", "", ""],
        ["2024-01-04", "0", "0", "0", "0", "0"],
        ["2024-01-05", "11", "13", "10", "12", "200"],
    ]
    bars = _feed_with(monkeypatch, _FakeBS(rows)).get_bars("600519", Level.DAY)
    assert [b.ts for b in bars] == ["2024-01-02", "2024-01-05"]


def test_duplicate_timestamps_are_collapsed(monkeypatch):
    rows = [
        ["2024-01-02", "10", "12", "9", "11", "100"],
        ["2024-01-02", "10", "12", "9", "11", "100"],
        ["2024-01-03", "11", "13", "10", "12", "200"],
    ]
    bars = _feed_with(monkeypatch, _FakeBS(rows)).get_bars("600519", Level.DAY)
    assert [b.ts for b in bars] == ["2024-01-02", "2024-01-03"]


def test_bars_are_indexed_from_zero(monkeypatch):
    rows = [["2024-01-0%d" % d, "10", "12", "9", "11", "1"] for d in (2, 3, 4)]
    bars = _feed_with(monkeypatch, _FakeBS(rows)).get_bars("600519", Level.DAY)
    assert [b.idx for b in bars] == [0, 1, 2]


def test_minute_start_is_clamped_to_what_exists(monkeypatch):
    """给 2010 也只会拿到 2020 起的数据，不如老实把下限说出来。"""
    from feeds.baostock_feed import MIN_EPOCH

    bs = _FakeBS([])
    _feed_with(monkeypatch, bs).get_bars("600519", Level.M30, start="2010-01-01")
    assert bs.calls[0][2]["start_date"] == MIN_EPOCH


def test_day_start_is_not_clamped(monkeypatch):
    bs = _FakeBS([])
    _feed_with(monkeypatch, bs).get_bars("600519", Level.DAY, start="2001-01-01")
    assert bs.calls[0][2]["start_date"] == "2001-01-01"


def test_always_asks_for_hfq(monkeypatch):
    """前复权在东财上能算出负价（C-adj）。这里钉住的是我们要的是哪一档。"""
    bs = _FakeBS([])
    _feed_with(monkeypatch, bs).get_bars("600519", Level.DAY)
    assert bs.calls[0][2]["adjustflag"] == "1"


def test_a_query_error_raises_instead_of_returning_nothing(monkeypatch):
    """回空列表会被上层读成「这只票没数据」，和限流、断网混成一件事。"""
    feed = _feed_with(monkeypatch, _FakeBS([], error_code="10001"))
    feed.retries = 2
    with pytest.raises(RuntimeError, match="baostock 查询失败"):
        feed.get_bars("600519", Level.DAY)


# --------------------------------------------------------------------------
# 翻页截断：`next()` 把「翻页失败」和「没有更多数据」报成同一件事
# --------------------------------------------------------------------------


def test_a_truncated_page_is_retried(monkeypatch):
    """整页倍数的结果必须重查。存下残缺历史不报错，后面全部划线都是错的。"""
    import feeds.baostock_feed as mod

    page = mod._page_size()
    bs = _TruncatingBS(_day_rows(page + 37), page)
    bars = _feed_with(monkeypatch, bs).get_bars("600519", Level.DAY)
    assert len(bars) == page + 37
    assert len(bs.calls) == 2, "第一次拿到整页倍数就该再查一遍"


def test_a_clean_partial_page_is_not_retried(monkeypatch):
    """绝大多数票不是整页倍数。为它们白查一遍，全市场就是多几小时。"""
    bs = _FakeBS(_day_rows(137))
    assert len(_feed_with(monkeypatch, bs).get_bars("600519", Level.DAY)) == 137
    assert len(bs.calls) == 1


def test_data_really_ending_on_a_page_boundary_is_accepted(monkeypatch):
    """真在页边界上结束时，续查回的还是同一段——不能当成截断一直往下要。"""
    import feeds.baostock_feed as mod

    page = mod._page_size()
    bs = _FakeBS(_day_rows(page))  # 这个假对象不看 start，每次都回同一页
    assert len(_feed_with(monkeypatch, bs).get_bars("600519", Level.DAY)) == page
    assert len(bs.calls) == 2, "续查一次确认没有了就该停，不能死循环"


def test_paging_forward_asks_from_the_last_day_it_got(monkeypatch):
    """续查的起点是已拿到的最后一天，不是原始 start——否则是无限重复第一页。"""
    import feeds.baostock_feed as mod

    page = mod._page_size()
    rows = _day_rows(page + 5)
    bs = _TruncatingBS(rows, page)
    _feed_with(monkeypatch, bs).get_bars("600519", Level.DAY)
    assert bs.calls[1][2]["start_date"] == rows[page - 1][0]


def test_an_error_set_while_paging_is_caught(monkeypatch):
    """开头那次 error_code 检查管不到翻页——翻页失败时它才被写上去。"""

    class _PageFails(_FakeBS):
        def query_history_k_data_plus(self, code, fields, **kw):
            self.calls.append((code, fields, kw))
            rs = _FakeRS(_day_rows(10))
            orig = rs.next

            def nxt():
                if not orig():
                    rs.error_code = "10002007"  # baostock 的写法：读完才看得到
                    return False
                return True

            rs.next = nxt
            return rs

    feed = _feed_with(monkeypatch, _PageFails([]))
    feed.retries = 1
    with pytest.raises(RuntimeError, match="翻页"):
        feed.get_bars("600519", Level.DAY)


# --------------------------------------------------------------------------
# 会话被顶掉：所有进程都是 anonymous，后登录的把先登录的挤下去
# --------------------------------------------------------------------------


class _KickedOnce(_FakeBS):
    """第一次查询回「用户未登录」，重新登录之后才给数据。"""

    def __init__(self, rows):
        super().__init__(rows)
        self.logins = 0

    def query_history_k_data_plus(self, code, fields, **kw):
        self.calls.append((code, fields, kw))
        if self.logins == 0:
            return _FakeRS([], error_code="10001001")
        return _FakeRS(self.rows)


def _feed_counting_logins(monkeypatch, bs):
    import feeds.baostock_feed as mod

    monkeypatch.setattr(mod, "RETRY_WAIT", 0.0)
    monkeypatch.setattr(mod, "_login", lambda: None)

    def relogin():
        bs.logins += 1

    monkeypatch.setattr(mod, "_relogin", relogin)
    monkeypatch.setitem(__import__("sys").modules, "baostock", bs)
    return BaostockFeed()


def test_being_kicked_out_triggers_a_relogin(monkeypatch):
    """10001001 不是限流。原地重查多少遍都是它，必须先把登录重做一遍。"""
    bs = _KickedOnce(_day_rows(5))
    bars = _feed_counting_logins(monkeypatch, bs).get_bars("600519", Level.DAY)
    assert len(bars) == 5
    assert bs.logins == 1, "被顶掉之后应当重新登录，而不是干重查"


def test_a_dead_socket_also_triggers_a_relogin(monkeypatch):
    """10002007 是「这条连接废了」。`bs.login()` 每次新建 socket，重登录即重连。"""

    class _RecvFails(_FakeBS):
        def query_history_k_data_plus(self, code, fields, **kw):
            self.calls.append((code, fields, kw))
            if self.logins == 0:
                return _FakeRS([], error_code="10002007")
            return _FakeRS(self.rows)

    bs = _RecvFails(_day_rows(5))
    bs.logins = 0
    bars = _feed_counting_logins(monkeypatch, bs).get_bars("600519", Level.DAY)
    assert len(bars) == 5 and bs.logins == 1


def test_other_errors_do_not_relogin(monkeypatch):
    """重登录会反过来顶掉别的进程。不是连接问题就别碰它。"""
    bs = _FakeBS([], error_code="10004001")
    bs.logins = 0
    feed = _feed_counting_logins(monkeypatch, bs)
    feed.retries = 2
    with pytest.raises(RuntimeError, match="10004001"):
        feed.get_bars("600519", Level.DAY)
    assert bs.logins == 0


def test_the_socket_gets_a_timeout_after_login(monkeypatch):
    """baostock 自己一个 settimeout 都没有，卡住就是永远卡住（SOCKET_TIMEOUT）。"""
    import feeds.baostock_feed as mod

    class _Sock:
        timeout = None

        def settimeout(self, t):
            type(self).timeout = t

    import baostock.common.context as ctx

    monkeypatch.setattr(ctx, "default_socket", _Sock(), raising=False)
    mod._set_timeout()
    assert _Sock.timeout == mod.SOCKET_TIMEOUT


def test_no_socket_yet_is_not_an_error(monkeypatch):
    """取不到 socket 不该拦住拉数据，最坏也就是退回原来那个会卡住的行为。"""
    import baostock.common.context as ctx

    import feeds.baostock_feed as mod

    monkeypatch.setattr(ctx, "default_socket", None, raising=False)
    mod._set_timeout()  # 不抛就算过


def test_relogin_clears_the_logged_in_flag(monkeypatch):
    """`_login` 看 `_logged_in` 就直接返回。不清掉这个标记，重登录是空操作。"""
    import feeds.baostock_feed as mod

    monkeypatch.setattr(mod, "_logged_in", True)
    seen = []
    monkeypatch.setattr(mod, "_login", lambda: seen.append(mod._logged_in))
    mod._relogin()
    assert seen == [False]


def test_a_blacklisted_ip_stops_immediately(monkeypatch):
    """退避重试对封禁是反作用：越撞封得越久。碰到就停，别留在重试循环里。"""
    import feeds.baostock_feed as mod

    class _Banned:
        error_code, error_msg = "10001011", "黑名单用户，请与管理员联系"

    calls = []

    class _BS:
        def login(self):
            calls.append(1)
            return _Banned()

    monkeypatch.setattr(mod, "_logged_in", False)
    monkeypatch.setitem(__import__("sys").modules, "baostock", _BS())
    with pytest.raises(RuntimeError, match="拉黑"):
        mod._login()
    assert len(calls) == 1, "黑名单一次就该停，不能重试六次"


def test_login_creates_its_socket_with_a_timeout(monkeypatch):
    """真正会卡死的是登录自己那一次 recv——等登录返回再 settimeout 已经晚了。"""
    import socket

    import feeds.baostock_feed as mod

    seen = []

    class _BS:
        def login(self):
            seen.append(socket.getdefaulttimeout())

            class _OK:
                error_code, error_msg = "0", ""

            return _OK()

    monkeypatch.setattr(mod, "_logged_in", False)
    monkeypatch.setattr(mod, "_set_timeout", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "baostock", _BS())
    before = socket.getdefaulttimeout()
    mod._login()
    assert seen == [mod.SOCKET_TIMEOUT], "登录期间全局默认超时要是生效的"
    assert socket.getdefaulttimeout() == before, "用完要还回去，这是进程级设置"
