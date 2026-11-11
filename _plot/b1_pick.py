"""去掉止损（事后探索）：挑「止损有影响」的几笔，列出有/无止损的结果，并按无止损的出场画图。
用法：PYTHONPATH=. python _plot/b1_pick.py [每类挑几笔=6]
  A 类：不止损反而更好（止损把后来回升的单子砍在低点）；B 类：止损确实救了一把（不止损亏得更多）。
  只在原来被止损（broken）的笔里挑；图用 `_plot/b1_trades.py` 的格式，出场取无止损那次。
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "_plot")
import b1_trades  # noqa: E402

ARM, ENT, EX_STOP, EX_NOSTOP = ".tmp/b1_arm_e", ".tmp/b1_ent_e", ".tmp/b1_exit_e", ".tmp/b1_exit_nostop_e"
OUT = Path("_plot/b1_nostop")


def nets(code, ev, ex_dir, b5, ref5):
    from backtest.costs import Costs, enter_long, exit_long
    from backtest.sublevel import _read_steps

    ex = _read_steps(Path(ex_dir) / f"{code}.{ev['e5']}.jsonl")
    if not ex:
        return None
    last = max(ex)
    why = ex[last].get("trigger") or "cap"
    e = enter_long(b5, ev["e5"], code, ref5)
    if isinstance(e, str):
        return None
    tr = exit_long(b5, ev["e5"], e, last, code, Costs(slippage=0.001), ref=ref5, intraday=True)
    return None if isinstance(tr, str) else (tr.net, why, b5[tr.exit_bar].ts, tr.exit_bar - tr.entry_bar)


def main(k: int) -> None:
    from backtest.costs import prev_day_close
    from feeds import Level, cache

    rows = []
    for p in sorted(Path(ENT).glob("*.entries.jsonl")):
        code = p.name.split(".")[0]
        evs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
        if not evs:
            continue
        b5 = cache.load(code, Level.M5)
        ref5 = prev_day_close(b5)
        for ev in evs:
            s, n = nets(code, ev, EX_STOP, b5, ref5), nets(code, ev, EX_NOSTOP, b5, ref5)
            if s and n and s[1] == "broken":
                rows.append((code, ev, s, n))
    print(f"原来被止损的笔 {len(rows)}；无止损净均值 {sum(r[3][0] for r in rows) / len(rows):+.2%}，"
          f"有止损净均值 {sum(r[2][0] for r in rows) / len(rows):+.2%}")
    better = sum(1 for r in rows if r[3][0] > r[2][0])
    print(f"其中不止损更好 {better}/{len(rows)}")
    a = sorted(rows, key=lambda r: r[3][0] - r[2][0], reverse=True)[:k]
    b = sorted(rows, key=lambda r: r[2][0] - r[3][0], reverse=True)[:k]
    n = 0
    for tag, grp in (("A 止损砍在低点、不止损更好", a), ("B 止损救了一把、不止损更差", b)):
        print(f"\n== {tag}")
        for code, ev, s, nn in grp:
            n += 1
            print(f"{n:2d} {code} 入场 {ev['ts'][5:16]} {ev['price']:.4g} | 有止损 {s[0]:+.2%} 出 {s[2][5:16]} 持有{s[3]} | "
                  f"无止损 {nn[0]:+.2%} 出 {nn[2][5:16]} 持有{nn[3]}（{nn[1]}）")
            b1_trades.one(code, ev, EX_NOSTOP, OUT, n, stop_mode="off")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
