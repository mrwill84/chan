"""可续传的全市场扫描。

测的全是**崩溃之后那一刻**的行为：已算的认不认、半行残留会不会毁掉下一次
追加、条件改了会不会悄悄混进去。正常跑通那条路反而最不值得测——它在
`test_report.py` 里已经被 `scan_many` 覆盖过了。

「条件改了」这条最要命：混出来的结果长得和正常结果一模一样，没有任何迹象
提示它是两套条件拼起来的。
"""

from __future__ import annotations

import json

import pytest
from conftest import zigzag

from feeds import Level, cache
from scan.scanner import (
    FAIL,
    HIT,
    MISS,
    ConfigMismatch,
    collect,
    read_progress,
    repair,
    run_scan,
)

LV = Level.DAY


def _saw(n: int = 60, seed: int = 29):
    import random

    rng = random.Random(seed)
    pv = [100.0]
    for i in range(n):
        step = rng.uniform(5, 30) * rng.uniform(0.5, 1.5)
        pv.append(max(pv[-1] + (step if i % 2 == 0 else -step), 10.0))
    return zigzag(pv)


@pytest.fixture
def cached(tmp_path):
    """造一个只有本地缓存的小「市场」。扫描层只读缓存，不碰网络。"""
    root = tmp_path / "cache"
    codes = ["000001", "000002", "000003"]
    for i, c in enumerate(codes):
        cache.save(c, LV, _saw(seed=29 + i), root)
    cache.save("000900", LV, zigzag([100.0, 110.0, 105.0]), root)  # K线太短 → FAIL
    return root, codes + ["000900"]


def _run(tmp_path, cached, codes=None, **kw):
    root, all_codes = cached
    kw.setdefault("within", 10**6)
    return run_scan(codes or all_codes, LV, tmp_path / "run.jsonl", root=root, **kw)


# --------------------------------------------------------------------------
# 续传
# --------------------------------------------------------------------------


def test_second_run_computes_nothing_new(tmp_path, cached):
    a = _run(tmp_path, cached)
    b = _run(tmp_path, cached)
    assert a["computed_now"] == 4 and a["resumed"] == 0
    assert b["computed_now"] == 0 and b["resumed"] == 4
    assert b["hit_count"] == a["hit_count"]


def test_resume_only_computes_the_remainder(tmp_path, cached):
    _, codes = cached
    _run(tmp_path, cached, codes=codes[:2])
    b = _run(tmp_path, cached)
    assert b["computed_now"] == 2 and b["resumed"] == 2
    assert b["hit_count"] + b["miss_count"] + b["failed_count"] == 4


def test_misses_are_recorded_too(tmp_path, cached):
    """不记「算过但没中」的话，每次重跑都要把几千只没中的重算一遍。"""
    r = _run(tmp_path, cached, within=0)
    assert r["miss_count"] >= 1
    again = _run(tmp_path, cached, within=0)
    assert again["computed_now"] == 0


def test_the_miss_reason_survives_the_round_trip(tmp_path, cached):
    """原因要写进 JSONL，不能只活在这一轮的内存里。

    续传时大部分记录来自上一轮的文件，「为什么 0 命中」这个问题恰恰是在
    最后一轮才有人问的——那时上几轮的理由必须还在。
    """
    r = _run(tmp_path, cached, within=0)
    assert sum(r["missed_because"].values()) == r["miss_count"]
    assert collect(tmp_path / "run.jsonl")["missed_because"] == r["missed_because"]


def test_every_miss_reason_has_a_sentence_for_it(tmp_path, cached):
    """CLI 拿 `MISS_REASONS` 翻译成人话。缺一条就会直接把内部 key 印给用户。"""
    from scan.report import _match
    from scan.scanner import MISS_REASONS

    r = _run(tmp_path, cached, within=0)
    assert set(r["missed_because"]) <= set(MISS_REASONS)
    # 代码里产得出的每一种，表里都得有——靠 `_match` 的源码取，不手抄
    produced = {"none", "stale", "quasi", "confirmed", "kind", "side"}
    assert produced == set(MISS_REASONS), "新增了一种原因却没给它一句人话"
    assert _match.__doc__  # 顺手钉住：这个函数的「为什么」写在文档串里


def test_the_extension_can_translate_every_trend_value():
    """扩展里 `TREND_CN` 的键要和引擎发得出的走势代号逐字对上。

    这条是真踩过的：那张表写的是 `trend_up`/`trend_down`，而引擎从来只发
    `up`/`down`（`chan.zhongshu` 的常量）。两年没人发现，因为 `trend` 当时
    恒等于 `panzheng`——口径一修好（C-trendnow），用户立刻就会在命中表里看到
    一个光秃秃的 `down`。

    只读 TS 源码比对字符串，不跑 node：这里要钉的就是「两边手抄的常量会分家」
    这件事本身，而它在文本层面就看得出来。
    """
    import re
    from pathlib import Path

    from chan.tree import NO_ZHONGSHU
    from chan.zhongshu import PANZHENG, TREND_DOWN, TREND_UP
    from scan.report import _kind_json

    src = (Path(__file__).resolve().parents[1]
           / "pi" / "extensions" / "chan" / "index.ts").read_text(encoding="utf-8")
    body = re.search(r"const TREND_CN[^{]*\{(.*?)\}", src, re.S)
    assert body, "index.ts 里找不到 TREND_CN，改名了就把这条测试一起改"
    keys = set(re.findall(r"^\s*\"?([A-Za-z_]\w*)\"?\s*:", body.group(1), re.M))
    # "none" 不是引擎常量，是 `multilevel.analyze_level` 给算不出来的那一层的
    # 哨兵值，所以单独列在这里而不是从 chan 里 import。
    #
    # `NO_ZHONGSHU` 现在自己就是机器键（从前是中文串「无中枢」，靠
    # `_kind_json` 在出口归一）。仍然过一遍那个函数而不是手抄 "no_zhongshu"
    # ——手抄就是这条测试当初要防的那件事。
    assert keys == {TREND_UP, TREND_DOWN, PANZHENG, "none",
                    _kind_json(NO_ZHONGSHU)}


def test_kind_codes_are_machine_keys_in_both_languages():
    """四个走势类型代号必须全是 ASCII，且显示名在 Python 侧有一份。

    中文做机器键踩过一次（`NO_ZHONGSHU = "无中枢"`）：它会一路流进
    `structuredContent`，渲染层再去猜。这条钉住代号和显示名的分工。
    """
    from chan.tree import KIND_CN, NO_ZHONGSHU
    from chan.zhongshu import PANZHENG, TREND_DOWN, TREND_UP

    codes = {TREND_UP, TREND_DOWN, PANZHENG, NO_ZHONGSHU}
    assert all(c.isascii() for c in codes), codes
    assert set(KIND_CN) == codes
    assert not any(v.isascii() for v in KIND_CN.values()), "显示名该是中文"


def test_failures_are_recorded_and_not_retried(tmp_path, cached):
    r = _run(tmp_path, cached)
    assert r["failed_count"] == 1
    recs = read_progress(tmp_path / "run.jsonl").done
    assert recs["000900"]["status"] == FAIL and "不足" in recs["000900"]["error"]


def test_missing_cache_is_a_failure_not_a_crash(tmp_path, cached):
    r = _run(tmp_path, cached, codes=["000001", "999999"])
    assert r["failed_count"] == 1
    rec = read_progress(tmp_path / "run.jsonl").done["999999"]
    assert "缓存里没有" in rec["error"]


def test_force_starts_over(tmp_path, cached):
    _run(tmp_path, cached)
    r = _run(tmp_path, cached, force=True)
    assert r["computed_now"] == 4 and r["resumed"] == 0


# --------------------------------------------------------------------------
# 配置不一致
# --------------------------------------------------------------------------


@pytest.mark.parametrize("change", [
    {"within": 3}, {"zs_from": "bi"}, {"kinds": {1}},
    {"include_quasi": True}, {"confirmed_only": True},
])
def test_changing_the_filter_refuses_to_resume(tmp_path, cached, change):
    _run(tmp_path, cached)
    with pytest.raises(ConfigMismatch, match="另一套条件"):
        _run(tmp_path, cached, **change)


def test_the_refusal_says_what_changed(tmp_path, cached):
    """只说「配置不一致」等于让人自己去 diff 两行 JSON。"""
    _run(tmp_path, cached)
    with pytest.raises(ConfigMismatch, match=r"within.*3"):
        _run(tmp_path, cached, within=3)


def test_same_filter_different_level_also_refuses(tmp_path, cached):
    root, codes = cached
    run_scan(codes, Level.DAY, tmp_path / "run.jsonl", root=root, within=10**6)
    with pytest.raises(ConfigMismatch):
        run_scan(codes, Level.M30, tmp_path / "run.jsonl", root=root, within=10**6)


def test_force_overrides_the_refusal(tmp_path, cached):
    _run(tmp_path, cached)
    r = _run(tmp_path, cached, within=3, force=True)
    assert r["computed_now"] == 4


def test_a_file_without_a_config_line_is_refused(tmp_path, cached):
    p = tmp_path / "run.jsonl"
    p.write_text(json.dumps({"code": "000001", "status": HIT}) + "\n", encoding="utf-8")
    with pytest.raises(ConfigMismatch, match="没有配置行"):
        _run(tmp_path, cached)


# --------------------------------------------------------------------------
# 崩溃残留
# --------------------------------------------------------------------------


def test_a_half_written_last_line_is_dropped(tmp_path, cached):
    """崩溃时最后一行多半写了一半。为它拒绝整个文件就把续传本身废了。"""
    _run(tmp_path, cached, codes=["000001", "000002"])
    p = tmp_path / "run.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write('{"code": "000003", "sta')
    prog = read_progress(p)
    assert prog.broken_tail and set(prog.done) == {"000001", "000002"}


def test_appending_after_a_broken_tail_does_not_glue_lines(tmp_path, cached):
    """修不掉半行的话，下一次追加会接在它后面，拼出谁也读不了的一行。"""
    _run(tmp_path, cached, codes=["000001"])
    p = tmp_path / "run.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write('{"code": "000002", "sta')

    r = _run(tmp_path, cached)
    assert r["failed_count"] + r["hit_count"] + r["miss_count"] == 4
    for line in p.read_text(encoding="utf-8").splitlines():
        json.loads(line)  # 每一行都必须还是合法 JSON


def test_a_broken_line_in_the_middle_is_not_tolerated(tmp_path, cached):
    """中间坏掉不是崩溃残留，是文件被动过。继续用它等于拿错结果冒充完整结果。"""
    _run(tmp_path, cached)
    p = tmp_path / "run.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[2] = "{坏掉了"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ConfigMismatch, match="损坏"):
        read_progress(p)


def test_repair_is_a_noop_when_nothing_is_broken(tmp_path, cached):
    _run(tmp_path, cached)
    p = tmp_path / "run.jsonl"
    before = p.read_bytes()
    repair(read_progress(p))
    assert p.read_bytes() == before


# --------------------------------------------------------------------------
# 收口
# --------------------------------------------------------------------------


def test_collect_reads_back_what_the_run_wrote(tmp_path, cached):
    r = _run(tmp_path, cached)
    c = collect(tmp_path / "run.jsonl")
    assert c["hit_count"] == r["hit_count"]
    assert c["failed_count"] == r["failed_count"]
    assert c["scanned"] == 4 and c["level"] == LV.value


def test_collect_sorts_newest_first(tmp_path, cached):
    _run(tmp_path, cached)
    hits = collect(tmp_path / "run.jsonl")["hits"]
    assert [h["point"]["ts"] for h in hits] == sorted(
        (h["point"]["ts"] for h in hits), reverse=True)


def test_collect_result_is_json_serializable(tmp_path, cached):
    _run(tmp_path, cached)
    json.dumps(collect(tmp_path / "run.jsonl"), ensure_ascii=False)


def test_multiprocess_gives_the_same_answer(tmp_path, cached):
    """多进程只该改变快慢，不该改变结果。"""
    root, codes = cached
    a = run_scan(codes, LV, tmp_path / "a.jsonl", root=root, within=10**6, jobs=1)
    b = run_scan(codes, LV, tmp_path / "b.jsonl", root=root, within=10**6, jobs=2)
    assert a["hit_count"] == b["hit_count"]
    ha = {h["code"]: h["point"]["ts"] for h in collect(tmp_path / "a.jsonl")["hits"]}
    hb = {h["code"]: h["point"]["ts"] for h in collect(tmp_path / "b.jsonl")["hits"]}
    assert ha == hb


def test_progress_callback_sees_every_symbol(tmp_path, cached):
    seen = []
    _run(tmp_path, cached, on_progress=lambda n, t, rec: seen.append((n, rec["code"])))
    assert [n for n, _ in seen] == [1, 2, 3, 4]
    assert len({c for _, c in seen}) == 4


def test_status_is_always_one_of_the_three(tmp_path, cached):
    _run(tmp_path, cached)
    got = {r["status"] for r in read_progress(tmp_path / "run.jsonl").done.values()}
    assert got <= {HIT, MISS, FAIL}
