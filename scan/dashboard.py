"""盘后看板：一批票的 30 分钟 / 5 分钟最新买卖点和区间套结论，一只一行。

    python -m scan dash --jobs 16 --progress          # 缓存里 30/5 分钟都有的全部票
    python -m scan dash 000651 600519                 # 只看几只

输出是 `_plot/interactive/dashboard_data.js`，给同目录的 `dashboard.html` 用，
点一行打开 30/5 分钟联立查看器（`viewer.html`）。

## 窗口必须和查看器一致

同一只票换一个起点，线段可能划得不一样。看板上写「08-17 3买」，点进去查看器
里却没有，这比没有看板还糟。所以 `window()` 是两边共用的唯一入口：最后 N30 根
30 分钟，加上同一起点之后的全部 5 分钟，K线下标都从 0 重排。

**重排不是洁癖。** `analyze_level` 拿 `bar.idx` 去对 MACD 和时间戳，切片保留
原下标的话，MACD 对错位，所有靠背驰的点（1/2 类）会**无声地**全丢，`nest`
的时间戳也变成空串。实测 000651：不重排 30 分钟只剩 2 个 3买，重排后多出 1卖。

## 防位翻

和 `fullscan` 一样：子进程跑、`stable()` 算两遍比摘要、K线前后各取一次摘要
（见 `chan.verify`、CONVENTIONS.md 第十节）。这台机器的长任务数字不能裸信。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from chan.multilevel import analyze_level, nest
from chan.verify import MAX_TRIES, bars_digest, stable
from feeds import Level
from feeds.cache import DEFAULT_SOURCE, load, path_for

from .scanner import _TASK, FAIL, _iterate

#: 30 分钟取最后多少根。2000 根约 16 个月，30 分钟线段中枢才攒得出几个
N30 = 2000

#: 每个级别带回最近几个点。看板主列只用最后一个，其余放在悬停提示里
RECENT = 4

OK = "ok"


def window(code: str, n30: int = N30, *, source: str = DEFAULT_SOURCE,
           root: Path | None = None):
    """看板和查看器共用的K线窗口。返回 ``(30 分钟, 5 分钟)``，下标从 0 起。"""
    b30 = load(code, Level.M30, root, source=source)[-n30:]
    if not b30:
        return [], []
    b5 = [x for x in load(code, Level.M5, root, source=source) if x.ts >= b30[0].ts]
    return ([replace(x, idx=i) for i, x in enumerate(b30)],
            [replace(x, idx=i) for i, x in enumerate(b5)])


def cached_codes(source: str = DEFAULT_SOURCE, root: Path | None = None) -> list[str]:
    """缓存里 30 分钟、5 分钟都有的票。"""
    def codes(lv: Level) -> set[str]:
        d = path_for("000001", lv, root, source=source).parent
        return {p.stem for p in d.glob("*.csv")} if d.exists() else set()
    return sorted(codes(Level.M30) & codes(Level.M5))


def _pt(p, bars) -> dict:
    close = bars[-1].close
    return {
        "label": p.label, "kind": p.kind, "buy": p.is_buy,
        "quasi": p.quasi, "conf": p.confirmed,
        "ts": bars[p.bar].ts, "price": round(p.price, 4),
        "ago": len(bars) - 1 - p.bar,  # 距最后一根几根K线
        "ret": round(close / p.price - 1, 5),  # 从这个点到最新收盘
        "source": p.source,
    }


def _level(a, bars) -> dict:
    return {"trend": a.trend, "error": a.error, "bars": len(bars),
            "last_ts": bars[-1].ts if bars else "",
            "points": [_pt(p, bars) for p in a.points[-RECENT:]]}


def _nest(n, by_name) -> dict:
    def step(s):
        if s is None or s.point is None:
            return None
        bars = by_name[s.level]
        return {"level": s.level, "label": s.point.label, "quasi": s.point.quasi,
                "ts": bars[s.point.bar].ts}
    return {"agreement": n.agreement, "notes": list(n.notes),
            "anchor": step(n.anchor), "precise": step(n.precise),
            "steps": [s.note for s in n.steps]}


def summarize(b30, b5) -> dict:
    """一只票的一行。纯函数，给 `stable` 包。"""
    a30 = analyze_level(Level.M30.value, b30)
    a5 = analyze_level(Level.M5.value, b5)
    by_name = {Level.M30.value: b30, Level.M5.value: b5}
    return {"close": b30[-1].close, "last_ts": b30[-1].ts,
            "m30": _level(a30, b30), "m5": _level(a5, b5),
            "nest": _nest(nest([a30, a5]), by_name)}


def _one(code: str) -> dict:
    """子进程里算一只。异常一律转成 FAIL 记录，理由同 `scanner._one`。"""
    t = _TASK
    try:
        b30, b5 = window(code, t["n30"], source=t["source"])
        if len(b30) < 60 or len(b5) < 60:
            return {"code": code, "status": FAIL,
                    "error": f"K线不够：30 分钟 {len(b30)} 根、5 分钟 {len(b5)} 根"}
        before = bars_digest(b30), bars_digest(b5)
        row, chk = stable(lambda: summarize(b30, b5), tries=t["verify"], label=code)
        after = bars_digest(b30), bars_digest(b5)
        rec = {"code": code, "status": OK, **row}
        # 和 fullscan 一样，只在不正常时记账
        if chk.runs > 2 or not chk.ok or before != after:
            rec["verify"] = {"runs": chk.runs, "ok": chk.ok, "bars": before == after}
        return rec
    except Exception as e:  # noqa: BLE001
        return {"code": code, "status": FAIL, "error": f"{type(e).__name__}: {e}"}


def run(codes: list[str], out: Path, *, n30: int = N30, jobs: int = 1,
        verify: int = MAX_TRIES, source: str = DEFAULT_SOURCE,
        on_progress=None) -> dict:
    """扫一轮写出 `out`（一个 `window.DASH = {...}` 的 .js）。

    不做续传：2458 只 16 进程两分钟跑完，续传的复杂度不划算。
    """
    task = {"n30": n30, "source": source, "verify": verify}
    rows, failed = [], []
    for n, rec in enumerate(_iterate(codes, task, jobs, fn=_one), 1):
        (rows if rec.get("status") == OK else failed).append(rec)
        if on_progress is not None:
            on_progress(n, len(codes), rec)

    rows.sort(key=lambda r: r["code"])
    bad = [r for r in rows if r.get("verify")
           and (not r["verify"]["ok"] or not r["verify"]["bars"])]
    data = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source": source, "n30": n30, "verify": verify,
        "rows": rows,
        "failed": [{"code": r["code"], "error": r.get("error", "")} for r in failed],
        "health": {"recomputed": sum(1 for r in rows if r.get("verify")) - len(bad),
                   "unstable": [r["code"] for r in bad]},
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再改名：浏览器正开着看板时刷新，读不到半个文件
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("window.DASH = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, out)
    return {"out": str(out), "total": len(codes), "ok": len(rows),
            "failed_count": len(failed), "health": data["health"]}
