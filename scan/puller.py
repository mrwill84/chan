"""把全市场的K线拉进缓存。可中断、可续。

## 并发对 baostock 是净亏损——这一条是实测翻过来的

本来这个模块是按「八进程并行把 35 小时压到四五小时」设计的。同一组 8 只票
量下来，结论正相反：

| 并发 | 总耗时 | 每只 | 最慢一只 | 失败 |
|---|---|---|---|---|
| 1 | 77s | 9.6s | 16s | 0 |
| 2 | 103s | 12.9s | 71s | 0 |
| 4 | 102s | 12.7s | 91s | 0 |
| 8 | 181s | 22.6s | 181s | 1 |

**单调变差**，不是超过某个数才变差。原因是 baostock 的 `login()` 默认参数就
是 `user_id='anonymous'`——所有进程共用一个账号，服务端按账号记会话，后登录
的把先前的顶掉，被顶掉的进程下一次查询回 `10001001`，当前这段得重新登录从
头再拉。加进程只是加互相顶的频率。

所以 `jobs` 的上限由 feed 自己声明（`feed.max_jobs`），baostock 是 1。这个
参数留着是因为 akshare 那边是无状态 HTTP，确实能并发。

## 省时间要靠少拉，不是靠多开

耗时是按**页**算的（每页 2000 行）。同一只票：全历史 5833 根要 17~20 秒，
2020 年起的 1643 根只要 1~3 秒。所以回填的默认起点是
`cache.DEFAULT_START`，和分钟线的数据起点同一天——反正更早的日线在三级别
联立里也只能得到 `coverage: "none"`。

量级因此变成：**日线全市场 5224 只 ≈ 三四个小时**，一晚上的事。

分钟线不要全市场拉。30 分钟是日线的八倍行数，5 分钟是四十倍，全市场拉
5 分钟是上百小时。正确的用法是先用日线扫出候选（几十到几百只），再只给
这些票补 30 分钟和 5 分钟。

## 为什么是进程不是线程

baostock 的连接是**模块级的全局 socket**，一个进程只能有一个，收发还没有锁。
多线程共用它，返回的数据会串台——A 股票的请求拿到 B 股票的响应，而且不报错。
所以 worker 是独立进程，各自 login 一次（`feeds.baostock_feed` 里 atexit
注销）。Windows 上走 spawn，worker 函数必须是模块级的。

## 续传靠的是缓存本身

不另记进度文件：缓存目录里有没有那个 CSV，就是「这只拉没拉过」的唯一真相。
另记一份进度，两份状态迟早对不上——而对不上的那一刻，你不知道该信哪个。

所以 `missing` 模式下判断只用 `path.exists()`，不读 CSV：5224 个文件全部解析
一遍，光「确认已经有了」就要好几分钟。
"""

from __future__ import annotations

from pathlib import Path

from feeds import Blacklisted, Level, cache, make_feed

#: 拉取模式。
#: - ``missing``：缓存里没有才拉。续传用这个，也是默认。
#: - ``update``：增量补末尾。盘后每天跑一轮用这个。
#: - ``refresh``：整条重拉。换了源、或怀疑历史数据有问题时才用。
MODES = ("missing", "update", "refresh")

_TASK: dict = {}
_FEED = None


def _init(task: dict) -> None:
    """每个 worker 进程起来时跑一次。

    feed 在这里造、不在每只票上造：baostock 的 login 是一次网络往返，
    每只票登录一遍等于把拉取时间直接翻倍。
    """
    global _FEED
    _TASK.update(task)
    _FEED = make_feed(task["source"])


def _one(code: str) -> dict:
    """拉一只的所有级别。**异常转成记录**，不让它冒到池子外面。

    一只退市票、一只北交所票就带走整个 worker 的话，一轮几小时的拉取会在
    第几百只上无声地少掉一截——而缺数据在这套系统里的表现是「这段时间没有
    买卖点」，不是报错。

    **唯一的例外是封禁**（`Blacklisted`）。那是全局状态不是这只票的问题，
    兜下来接着跑等于拿 5224 次登录去撞同一堵墙，把封禁撞得更久，最后只收获
    一份「全部失败」。它要原样抛出去中止整轮。
    """
    root = Path(_TASK["root"]) if _TASK["root"] else None
    mode = _TASK["mode"]
    out: dict = {"code": code, "levels": {}}
    for name in _TASK["levels"]:
        lv = Level(name)
        try:
            if mode == "missing" and cache.path_for(
                code, lv, root, source=_TASK["source"]
            ).exists():
                out["levels"][name] = {"status": "cached"}
                continue
            start = _TASK.get("start")
            if mode == "update":
                bars, added = cache.update(_FEED, code, lv, root=root, start=start)
            else:
                bars = cache.get_or_fetch(
                    _FEED, code, lv, root=root, refresh=True, start=start
                )
                added = len(bars)
            out["levels"][name] = {
                "status": "ok" if bars else "empty",
                "bars": len(bars), "added": added,
                "first_ts": bars[0].ts if bars else "",
                "last_ts": bars[-1].ts if bars else "",
            }
        except Blacklisted:
            raise
        except Exception as e:  # noqa: BLE001
            out["levels"][name] = {"status": "fail",
                                   "error": f"{type(e).__name__}: {e}"}
    return out


def run_pull(
    codes: list[str],
    levels: list[Level],
    *,
    source: str = cache.DEFAULT_SOURCE,
    mode: str = "missing",
    root: Path | None = None,
    jobs: int = 1,
    start: str | None = None,
    on_progress=None,
) -> dict:
    """拉一轮。回统计 + 失败清单，**不回全部明细**。

    5224 只 × 3 个级别的逐只明细是一万五千行 JSON，没人读、也喂不进模型的
    上下文。真正要看的是「几只失败了、为什么」。

    ``jobs`` 会被压到 `feed.max_jobs`（baostock 是 1，原因见模块文档）。压了
    会在结果里留下 `jobs_requested`——静悄悄地改掉用户给的数，比慢更难查。
    """
    if mode not in MODES:
        raise ValueError(f"不认识的拉取模式：{mode}（有 {' / '.join(MODES)}）")

    cap = getattr(make_feed(source), "max_jobs", 1)
    jobs_requested = jobs
    jobs = max(1, min(jobs, cap))

    task = {"source": source, "mode": mode, "root": str(root) if root else None,
            "levels": [lv.value for lv in levels],
            "start": start or cache.DEFAULT_START}

    stat = {"ok": 0, "cached": 0, "empty": 0, "fail": 0}
    added_total = 0
    failed: list[dict] = []
    n = 0
    for rec in _iterate(codes, task, jobs):
        n += 1
        for name, r in rec["levels"].items():
            stat[r["status"]] += 1
            added_total += r.get("added", 0)
            if r["status"] == "fail":
                failed.append({"code": rec["code"], "level": name,
                               "error": r["error"]})
        if on_progress is not None:
            on_progress(n, len(codes), rec)

    return {
        "source": source, "mode": mode, "jobs": jobs,
        "jobs_requested": jobs_requested,
        "start": task["start"],
        "levels": [lv.value for lv in levels],
        "codes": len(codes),
        "fetched": stat["ok"], "already_cached": stat["cached"],
        "empty": stat["empty"], "failed_count": stat["fail"],
        "bars_added": added_total,
        # 失败清单截断：几千只全都失败时（断网、限流），前 50 条已经够看出
        # 是同一个原因，剩下的只是把真正的信息顶出屏幕。
        "failed": failed[:50],
        "failed_truncated": max(0, len(failed) - 50),
    }


def _iterate(codes: list[str], task: dict, jobs: int):
    if jobs <= 1:
        _init(task)
        for code in codes:
            yield _one(code)
        return

    from multiprocessing import Pool

    # chunksize=1：每只票好几秒，分块只会让某个 worker 攒一堆活、其他的空转。
    # 划线那边用 8 是因为单只是毫秒级，调度开销才是大头，这里正相反。
    with Pool(processes=jobs, initializer=_init, initargs=(task,)) as pool:
        yield from pool.imap_unordered(_one, codes, chunksize=1)
