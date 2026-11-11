"""增量更新与批量拉取。

这里测的全是**沉默的错**：增量把半截K线留在历史里、重叠日两份数据选错一份、
失败的票被静默跳过。它们都不会抛异常，只会让某一天的高低点悄悄变成错的，
而分型只看高低点。

不联网。feed 用假的——要验的是我们这边的合并和容错，不是人家的接口。
"""

from __future__ import annotations

import pytest

from feeds import Level, cache
from feeds.base import to_bars
from scan.puller import run_pull

LV = Level.DAY


def _rows(bars):
    return [(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]


def _days(n: int, *, base: float = 100.0):
    """n 根连续日K。

    不用 `conftest.zigzag`：那个是为笔和分型造的「无包含关系」锯齿，一个转折价
    会展开成好几根。这里关心的只有**时间戳怎么合并**，根数和价格必须一眼能数。
    """
    return to_bars([
        (f"2024-01-{i + 1:02d}", base + i, base + i + 1, base + i - 1, base + i, 1.0)
        for i in range(n)
    ])


class FakeFeed:
    """按日期区间回K线。记录每次被问到的 `start`。"""

    source = "baostock"

    def __init__(self, bars):
        self.bars = list(bars)
        self.starts: list[str | None] = []

    def get_bars(self, code, level, start=None, end=None):
        self.starts.append(start)
        keep = [b for b in self.bars if start is None or b.ts >= start]
        return to_bars(_rows(keep))


# --------------------------------------------------------------------------
# 增量
# --------------------------------------------------------------------------


def test_empty_cache_falls_back_to_a_full_pull(tmp_path):
    feed = FakeFeed(_days(4))
    bars, added = cache.update(feed, "600519", LV, root=tmp_path)
    assert len(bars) == 4 and added == 4
    # 「整条拉」也是从默认起点拉，不是从上市首日。耗时按页算，差十倍。
    assert feed.starts == [cache.DEFAULT_START]


def test_the_default_horizon_matches_the_minute_epoch():
    """两个级别用不同起点的话，三级别联立的历史视野就说不成一句话了。"""
    from feeds.baostock_feed import MIN_EPOCH

    assert cache.DEFAULT_START <= MIN_EPOCH


def test_an_explicit_start_wins(tmp_path):
    feed = FakeFeed(_days(4))
    cache.update(feed, "600519", LV, root=tmp_path, start="2015-01-01")
    assert feed.starts == ["2015-01-01"]


def test_start_does_not_apply_once_there_is_a_cache(tmp_path):
    """已经有缓存时起点由缓存末尾定。这个函数只往后追，不往前补历史。"""
    first = _days(4)
    cache.save("600519", LV, first, tmp_path, source="baostock")
    feed = FakeFeed(_days(6))
    cache.update(feed, "600519", LV, root=tmp_path, start="2015-01-01")
    assert feed.starts == [first[-1].ts]


def test_only_the_tail_is_refetched(tmp_path):
    """整条重拉和增量在结果上看不出区别，差的是二十秒还是一整天。"""
    first = _days(4)
    cache.save("600519", LV, first, tmp_path, source="baostock")

    feed = FakeFeed(_days(6))
    bars, added = cache.update(feed, "600519", LV, root=tmp_path)
    assert added == 2 and len(bars) == 6
    # 从缓存最后一根那天起问，不是从头
    assert feed.starts == [first[-1].ts]


def test_the_overlapping_day_is_taken_from_the_new_data(tmp_path):
    """盘中拉过一次的话，缓存里当天那根是半截的。它必须被收盘后的整根顶掉。"""
    half = _days(3)
    cache.save("600519", LV, half, tmp_path, source="baostock")
    stale_ts, stale_high = half[-1].ts, half[-1].high

    full = _days(3)
    full[-1] = full[-1].__class__(
        idx=full[-1].idx, ts=stale_ts, open=full[-1].open,
        high=stale_high + 50, low=full[-1].low, close=full[-1].close,
        volume=full[-1].volume * 3,
    )
    bars, added = cache.update(FakeFeed(full), "600519", LV, root=tmp_path)

    assert added == 0, "同一天不算新增"
    assert bars[-1].high == stale_high + 50, "当天高点没有被收盘后的整根修正"


def test_nothing_new_leaves_the_cache_alone(tmp_path):
    """停牌、或者当天还没开盘。不能把缓存写空。"""
    bars0 = _days(4)
    cache.save("600519", LV, bars0, tmp_path, source="baostock")

    class Nothing(FakeFeed):
        def get_bars(self, code, level, start=None, end=None):
            self.starts.append(start)
            return []

    bars, added = cache.update(Nothing([]), "600519", LV, root=tmp_path)
    assert added == 0
    assert [b.ts for b in bars] == [b.ts for b in bars0]
    assert len(cache.load("600519", LV, tmp_path, source="baostock")) == 4


def test_update_reindexes_from_zero(tmp_path):
    """引擎全程靠 idx 回溯，合并完不重排就是一串错下标。"""
    cache.save("600519", LV, _days(3), tmp_path, source="baostock")
    bars, _ = cache.update(FakeFeed(_days(5)), "600519", LV, root=tmp_path)
    assert [b.idx for b in bars] == list(range(len(bars)))


def test_update_stays_inside_its_own_source(tmp_path):
    cache.save("600519", LV, _days(3), tmp_path, source="akshare")
    bars, added = cache.update(
        FakeFeed(_days(3, base=200.0)), "600519", LV, root=tmp_path,
    )
    assert added == 3, "baostock 那边是空的，应当整条拉"
    assert len(cache.load("600519", LV, tmp_path, source="akshare")) == 3


def test_minute_timestamps_only_send_the_date(tmp_path):
    """feed 要的 start 是 YYYY-MM-DD，把 '2024-01-02 15:00:00' 整个送过去会被拒。"""
    rows = [(f"2024-01-02 {h:02d}:00:00", 10.0, 12.0, 9.0, 11.0, 1.0)
            for h in (10, 11, 13)]
    cache.save("600519", Level.M30, to_bars(rows), tmp_path, source="baostock")
    feed = FakeFeed(to_bars(rows))
    cache.update(feed, "600519", Level.M30, root=tmp_path)
    assert feed.starts == ["2024-01-02"]


# --------------------------------------------------------------------------
# 批量拉取
# --------------------------------------------------------------------------


@pytest.fixture
def fake_source(monkeypatch):
    """把 `make_feed` 换掉。`scan.puller` 是 `from feeds import make_feed`
    导进来的，所以要打 puller 模块上的那个名字。"""
    import scan.puller as mod

    made: list[FakeFeed] = []

    def fake(source):
        f = FakeFeed(_days(4))
        f.source = source
        made.append(f)
        return f

    monkeypatch.setattr(mod, "make_feed", fake)
    return made


def test_pull_fetches_what_is_missing(tmp_path, fake_source):
    r = run_pull(["600519", "000001"], [LV], root=tmp_path)
    assert r["fetched"] == 2 and r["failed_count"] == 0
    assert cache.path_for("600519", LV, tmp_path, source="baostock").exists()


def test_pull_skips_what_is_already_there(tmp_path, fake_source):
    """续传就是这一条。35 小时的活，重跑不能从头再来一遍。"""
    cache.save("600519", LV, _days(3, base=1000.0), tmp_path, source="baostock")
    r = run_pull(["600519", "000001"], [LV], root=tmp_path)
    assert r["already_cached"] == 1 and r["fetched"] == 1


def test_a_cached_symbol_is_not_even_read(tmp_path, fake_source, monkeypatch):
    """5224 个六千行 CSV 全解析一遍，光「确认已经有了」就要几分钟。"""
    cache.save("600519", LV, _days(3, base=1000.0), tmp_path, source="baostock")
    monkeypatch.setattr(cache, "load", lambda *a, **k: pytest.fail("不该读 CSV"))
    assert run_pull(["600519"], [LV], root=tmp_path)["already_cached"] == 1


def test_one_bad_symbol_does_not_take_down_the_batch(tmp_path, monkeypatch):
    """退市票、北交所票会抛。一轮 35 小时的拉取不能死在第 300 只上。"""
    import scan.puller as mod

    class Picky(FakeFeed):
        def get_bars(self, code, level, start=None, end=None):
            if code == "830799":
                raise ValueError("北交所：baostock 不提供")
            return super().get_bars(code, level, start, end)

    monkeypatch.setattr(mod, "make_feed",
                        lambda s: Picky(_days(3)))
    r = run_pull(["600519", "830799", "000001"], [LV], root=tmp_path)
    assert r["fetched"] == 2 and r["failed_count"] == 1
    assert "北交所" in r["failed"][0]["error"]
    assert r["failed"][0]["code"] == "830799"


def test_update_mode_goes_through_the_incremental_path(tmp_path, fake_source):
    cache.save("600519", LV, _days(2), tmp_path, source="baostock")
    r = run_pull(["600519"], [LV], root=tmp_path, mode="update")
    assert r["already_cached"] == 0, "update 模式不该跳过已有的"
    assert r["bars_added"] == 2 and r["fetched"] == 1


def test_refresh_mode_ignores_the_cache(tmp_path, fake_source):
    cache.save("600519", LV, _days(3, base=1000.0), tmp_path, source="baostock")
    r = run_pull(["600519"], [LV], root=tmp_path, mode="refresh")
    assert r["fetched"] == 1 and r["already_cached"] == 0
    assert cache.load("600519", LV, tmp_path, source="baostock")[0].close == 100.0


def test_every_level_is_counted_separately(tmp_path, fake_source):
    r = run_pull(["600519"], [LV, Level.M30], root=tmp_path)
    assert r["fetched"] == 2 and r["codes"] == 1


def test_an_unknown_mode_is_refused_before_anything_is_pulled(tmp_path):
    with pytest.raises(ValueError, match="拉取模式"):
        run_pull(["600519"], [LV], root=tmp_path, mode="incremental")


def test_progress_reports_against_the_code_count(tmp_path, fake_source):
    """进度分母是票数不是票×级别——预计剩余时间全靠它。"""
    seen = []
    run_pull(["600519", "000001"], [LV, Level.M30], root=tmp_path,
             on_progress=lambda n, total, rec: seen.append((n, total)))
    assert seen == [(1, 2), (2, 2)]


# --------------------------------------------------------------------------
# 并发上限由数据源自己说了算
# --------------------------------------------------------------------------


def test_jobs_is_capped_by_the_feed(tmp_path, monkeypatch):
    """baostock 并发是净亏损（见 scan.puller 的实测表），给多少都按 1 跑。"""
    import scan.puller as mod

    class Capped(FakeFeed):
        max_jobs = 1

    def fake(source):
        f = Capped(_days(4))
        f.source = source
        return f

    monkeypatch.setattr(mod, "make_feed", fake)
    r = run_pull(["600519"], [LV], root=tmp_path, jobs=8)
    assert r["jobs"] == 1
    assert r["jobs_requested"] == 8, "压下去了要留痕，否则慢得没法解释"


def test_a_feed_that_allows_concurrency_keeps_its_jobs(tmp_path, monkeypatch):
    import scan.puller as mod

    class Wide(FakeFeed):
        max_jobs = 4

    captured = {}

    def fake(source):
        f = Wide(_days(4))
        f.source = source
        return f

    def spy(codes, task, jobs):
        captured["jobs"] = jobs
        return iter(())  # 不真去起进程池，这里要验的只是传下去的数

    monkeypatch.setattr(mod, "make_feed", fake)
    monkeypatch.setattr(mod, "_iterate", spy)
    r = run_pull(["600519"], [LV], root=tmp_path, jobs=4)
    assert r["jobs"] == 4 and captured["jobs"] == 4


def test_the_pull_records_the_horizon_it_used(tmp_path, fake_source):
    """缓存里存的是哪一段，结果里要说得出来——否则没人知道该不该重拉。"""
    r = run_pull(["600519"], [LV], root=tmp_path)
    assert r["start"] == cache.DEFAULT_START


def test_a_ban_aborts_the_whole_run(tmp_path, monkeypatch):
    """封禁是全局状态。兜成「这只失败了」接着跑，就是拿几千次请求去撞同一堵墙。"""
    from feeds import Blacklisted
    import scan.puller as mod

    class Banned(FakeFeed):
        def get_bars(self, code, level, start=None, end=None):
            raise Blacklisted("黑名单")

    def fake(source):
        f = Banned(_days(4))
        f.source = source
        return f

    monkeypatch.setattr(mod, "make_feed", fake)
    with pytest.raises(Blacklisted):
        run_pull(["600519", "000001", "601398"], [LV], root=tmp_path)


def test_an_ordinary_bad_symbol_still_only_fails_itself(tmp_path, monkeypatch):
    """对照组：退市票、北交所票不能带走整轮。"""
    import scan.puller as mod

    class Picky(FakeFeed):
        def get_bars(self, code, level, start=None, end=None):
            if code == "000001":
                raise ValueError("北交所")
            return super().get_bars(code, level, start=start, end=end)

    def fake(source):
        f = Picky(_days(4))
        f.source = source
        return f

    monkeypatch.setattr(mod, "make_feed", fake)
    r = run_pull(["600519", "000001"], [LV], root=tmp_path)
    assert r["fetched"] == 1 and r["failed_count"] == 1


# --------------------------------------------------------------------------
# 缓存完整性（摘要边车）
#
# 这几条守的是最早的那一段没人看的路：K线从磁盘到内存。CSV 写了一半、
# `pull` 被 Ctrl-C 打断、磁盘翻了一位——**全都不抛异常**，截断的 CSV 仍然是
# 合法 CSV，只是少了几百根K线，而少掉的那段历史只表现为「中枢比记忆中少」。
# --------------------------------------------------------------------------


def test_saving_records_a_digest(tmp_path):
    cache.save("600519", LV, _days(4), tmp_path, source="baostock")
    assert cache.digest_path_for("600519", LV, tmp_path,
                                 source="baostock").exists()
    r = cache.integrity("600519", LV, tmp_path, source="baostock")
    assert r["ok"] is True and r["n"] == 4


def test_a_truncated_csv_is_caught(tmp_path):
    """少掉几根K线 = 少掉一段历史。文件照样是合法 CSV，没人会报错。"""
    cache.save("600519", LV, _days(8), tmp_path, source="baostock")
    p = cache.path_for("600519", LV, tmp_path, source="baostock")
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    p.write_text("".join(lines[:5]), encoding="utf-8")

    assert cache.integrity("600519", LV, tmp_path, source="baostock")["ok"] \
        is False
    # 默认读法照旧返回（少了三根，不报错）——这正是要防的那件事
    assert len(cache.load("600519", LV, tmp_path, source="baostock")) == 4
    with pytest.raises(cache.CacheCorrupt):
        cache.load("600519", LV, tmp_path, source="baostock", check=True)


def test_one_flipped_price_is_caught(tmp_path):
    """根数没变、只有一个价翻了位。文件大小一样，眼睛看不出来。"""
    cache.save("600519", LV, _days(6), tmp_path, source="baostock")
    p = cache.path_for("600519", LV, tmp_path, source="baostock")
    txt = p.read_text(encoding="utf-8")
    bad = txt.replace("100.0", "100.5", 1)
    assert bad != txt
    p.write_text(bad, encoding="utf-8")

    assert cache.integrity("600519", LV, tmp_path, source="baostock")["ok"] \
        is False


def test_never_recorded_is_none_not_true(tmp_path):
    """三态。缓存里已有的 CSV 一个边车都没有，**它们不是「健康」**。

    混成布尔，一整个没记过的目录会报成「全部健康」——和
    `walk_health` 的 `checked` 那一位要防的是同一件事。
    """
    cache.save("600519", LV, _days(4), tmp_path, source="baostock")
    cache.digest_path_for("600519", LV, tmp_path, source="baostock").unlink()

    r = cache.integrity("600519", LV, tmp_path, source="baostock")
    assert r["ok"] is None and r["recorded"] is None and r["exists"] is True
    # 没记过的不许被当成坏的——否则存量缓存一夜之间全红
    cache.load("600519", LV, tmp_path, source="baostock", check=True)


def test_a_missing_cache_is_not_corrupt(tmp_path):
    r = cache.integrity("600519", LV, tmp_path, source="baostock")
    assert r["exists"] is False and r["ok"] is None and r["n"] == 0


def test_update_refreshes_the_digest(tmp_path):
    """增量追加之后边车必须跟着走，否则下一次体检会报假损坏。"""
    cache.save("600519", LV, _days(3), tmp_path, source="baostock")
    cache.update(FakeFeed(_days(5)), "600519", LV, root=tmp_path)
    assert cache.integrity("600519", LV, tmp_path, source="baostock")["ok"] \
        is True


def test_the_digest_follows_the_source_directory(tmp_path):
    """两个源各有各的边车。串了就会拿 akshare 的摘要去判 baostock 的数据。"""
    cache.save("600519", LV, _days(3), tmp_path, source="baostock")
    cache.save("600519", LV, _days(3, base=200.0), tmp_path, source="akshare")
    for src in ("baostock", "akshare"):
        assert cache.integrity("600519", LV, tmp_path, source=src)["ok"] \
            is True
