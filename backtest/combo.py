"""BOLL 旁路提醒 + 笔级别3类点确认的组合走查。

组合不改变划线，也不拿 BOLL 候选价提前进场。唯一问题是：

    在结构买卖点第一次可见时，如果此前已有同方向、未失效的 BOLL 提醒，
    用这个条件筛掉一部分结构信号，剩下的表现是否更好？

提醒默认 60 根内有效；20/120 是预先登记的敏感性对照，不事后挑窗。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from statistics import median

from chan.indicators import higher_bollinger
from chan.macd import macd
from chan.pipeline import analyze
from chan.segend import provisional_end
from chan.types import Bar, Direction
from chan.verify import stable
from feeds import Level, cache

from .score import HORIZONS, evaluate, summarize
from .walk import Sighting, walk_forward


class BollAlert:
    """一次提醒 episode。重新失效后再出现，算新 episode 而不是复活旧的。"""

    def __init__(self, side: str, candidate_bar: int, price: float,
                 segment_start: int, t: int):
        self.side = side
        self.candidate_bar = candidate_bar
        self.price = price
        self.segment_start = segment_start
        self.first_seen = t
        self.seen = [t]
        self.invalidated_at: int | None = None

    def last_seen_before(self, t: int) -> int | None:
        past = [x for x in self.seen if x <= t]
        return past[-1] if past else None


def _reliable_analyze(bars: list[Bar], verify: int):
    if verify <= 1:
        return analyze(bars, check=True)
    a, chk = stable(lambda: analyze(bars, check=True), tries=verify,
                    label=f"combo alert n={len(bars)}")
    if not chk.ok:
        raise RuntimeError(f"提醒走查 n={len(bars)}：多数校验失败")
    return a


def walk_boll_alerts(bars: list[Bar], *, step: int = 1, warmup: int = 120,
                     verify: int = 3) -> list[BollAlert]:
    """逐前缀记录 `boll_second`，只报警，不调用 `split_tail`。

    顶部提醒在收盘重新站上高一级上轨时失效，底部镜像。episode 的 ``seen``
    保留全部出现时间；之后配对只能使用信号日当天的或更早的记录。
    """
    alerts: list[BollAlert] = []
    active: dict[tuple, BollAlert] = {}
    for t in range(warmup - 1, len(bars), step):
        prefix = bars[:t + 1]
        a = _reliable_analyze(prefix, verify)

        if active:
            boll = higher_bollinger(prefix)
            for key, alert in list(active.items()):
                band = boll.lower[t] if alert.side == "buy" else boll.upper[t]
                invalid = (prefix[t].close < band if alert.side == "buy"
                           else prefix[t].close > band)
                if invalid:
                    alert.invalidated_at = t
                    del active[key]

        if not a.segs:
            continue
        tail = a.segs[-1]
        m = macd([b.close for b in prefix])
        j = provisional_end(a.bis, m, tail, "boll_second", prefix)
        if j is None:
            continue
        side = "sell" if tail.direction is Direction.UP else "buy"
        candidate = a.bis[j].end.peak_idx
        key = (side, candidate, tail.start.peak_idx)
        if key in active:
            active[key].seen.append(t)
        else:
            alert = BollAlert(side, candidate, a.bis[j].end.price,
                              tail.start.peak_idx, t)
            alerts.append(alert)
            active[key] = alert
    return alerts


class ComboPair:
    def __init__(self, sighting: Sighting, alert: BollAlert, last_alert: int):
        self.sighting = sighting
        self.alert = alert
        self.last_alert = last_alert

    @property
    def notice(self) -> int:
        """结构第一次可见之前，BOLL 最早提前了多少根提醒。"""
        return self.sighting.first_seen - self.alert.first_seen


def attach_alerts(sightings: list[Sighting], alerts: list[BollAlert], *,
                  window: int = 60) -> list[ComboPair]:
    """按结构首次可见时间顺序配对；一个提醒 episode 最多确认一个3类点。

    这里虽然事后遍历，但每个条件都只引用 ``t`` 当时已经存在的状态。尤其
    不能用 episode 最终的 last_seen 判断新鲜度，那会把未来的再次出现偷渡
    到过去的信号上。
    """
    pairs: list[ComboPair] = []
    used: set[int] = set()
    ordered = sorted((s for s in sightings if s.kind == 3),
                     key=lambda s: (s.first_seen, s.bar, s.side))
    for s in ordered:
        best = None
        best_last = -1
        for i, alert in enumerate(alerts):
            if i in used or alert.side != s.side:
                continue
            if alert.invalidated_at is not None and s.first_seen >= alert.invalidated_at:
                continue
            last = alert.last_seen_before(s.first_seen)
            if last is None or s.first_seen - last > window:
                continue
            if last > best_last:
                best, best_last = (i, alert), last
        if best is not None:
            i, alert = best
            used.add(i)
            pairs.append(ComboPair(s, alert, best_last))
    return pairs


def _groups(points: list[Sighting], bars: list[Bar], horizons: tuple[int, ...]) -> dict:
    outcomes = evaluate(points, bars, horizons=horizons)
    summary = summarize(outcomes, bars, horizons=horizons)
    return {name: {"n": g["n"], "mean_lag": g["mean_lag"],
                   "survived": g["survived"], "stable": g["stable"],
                   "h": {str(h): {"n": g["h"][h]["n"],
                                  "win_rate": g["h"][h]["win_rate"],
                                  "edge_win": g["h"][h]["edge_win"],
                                  "mean_ret": g["h"][h]["mean_ret"],
                                  "edge_ret": g["h"][h]["edge_ret"]}
                         for h in horizons}}
            for name, g in summary["groups"].items()}


def evaluate_combo(bars: list[Bar], sightings: list[Sighting],
                   alerts: list[BollAlert], *, horizons: tuple[int, ...] = HORIZONS,
                   windows: tuple[int, ...] = (20, 60, 120)) -> dict:
    """同一批结构信号，比较全部 / 有提醒确认 / 无提醒三组。"""
    base = [s for s in sightings if s.kind == 3]
    out = {"n_alerts": len(alerts),
           "n_invalidated": sum(a.invalidated_at is not None for a in alerts),
           "n_structure": len(base), "windows": {}}
    for window in windows:
        pairs = attach_alerts(sightings, alerts, window=window)
        selected = [p.sighting for p in pairs]
        chosen = {id(s) for s in selected}
        rest = [s for s in base if id(s) not in chosen]
        notices = sorted(p.notice for p in pairs)
        out["windows"][str(window)] = {
            "matched": len(pairs),
            "notice": {"median": median(notices) if notices else None,
                       "mean": sum(notices) / len(notices) if notices else None,
                       "min": notices[0] if notices else None,
                       "max": notices[-1] if notices else None},
            "baseline": _groups(base, bars, horizons),
            "combo": _groups(selected, bars, horizons),
            "without_alert": _groups(rest, bars, horizons),
            "pairs": [{"side": p.sighting.side, "bar": p.sighting.bar,
                       "first_seen": p.sighting.first_seen,
                       "stable": p.sighting.stable,
                       "alert_first": p.alert.first_seen,
                       "alert_last": p.last_alert,
                       "candidate_bar": p.alert.candidate_bar,
                       "candidate_price": p.alert.price,
                       "notice": p.notice} for p in pairs],
        }
    return out


def _print_report(code: str, out: dict, primary: int) -> None:
    print(f"== {code} BOLL提醒 + 笔级别3类点 ==")
    print(f"提醒 {out['n_alerts']} 次（失效 {out['n_invalidated']}），"
          f"结构3类点 {out['n_structure']} 个")
    for key, w in out["windows"].items():
        n = w["notice"]
        med = f"{n['median']:.1f}" if n["median"] is not None else "--"
        print(f"窗口 {key:>3}：配对 {w['matched']}/{out['n_structure']}，"
              f"提醒提前结构中位 {med} 根")
    w = out["windows"][str(primary)]
    print(f"\n主口径窗口 {primary}（20/120 只是敏感性，不挑最优）")
    for kind in ("3买", "3卖"):
        b = w["baseline"].get(kind)
        c = w["combo"].get(kind)
        if not b:
            continue
        print(f"{kind} 基线 n={b['n']} 延迟={b['mean_lag']:.1f} 稳定={b['stable']}")
        if c:
            print(f"    组合 n={c['n']} 延迟={c['mean_lag']:.1f} 稳定={c['stable']}")
            for h, row in c["h"].items():
                base_row = b["h"][h]
                print(f"    {h:>3}根 收益 {row['mean_ret']:+.2%} "
                      f"(基线 {base_row['mean_ret']:+.2%})  "
                      f"edge {row['edge_ret']:+.2%}  "
                      f"胜率 {row['win_rate']:.1%}")
        else:
            print("    组合：没有配对信号")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("code")
    ap.add_argument("--level", default="day", choices=[x.value for x in Level])
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--tail", type=int, default=0, help="只用最近N根；跨票统一窗口用")
    ap.add_argument("--verify", type=int, default=3)
    ap.add_argument("--windows", default="20,60,120")
    ap.add_argument("--primary-window", type=int, default=60)
    ap.add_argument("--horizons", default=",".join(map(str, HORIZONS)))
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    level = Level(a.level)
    bars = cache.load(a.code, level)
    if a.tail:
        bars = [replace(b, idx=i) for i, b in enumerate(bars[-a.tail:])]
    sightings = walk_forward(bars, a.code, level, zs_from="bi", step=a.step,
                             verify=a.verify)
    alerts = walk_boll_alerts(bars, step=a.step, verify=a.verify)
    horizons = tuple(int(x) for x in a.horizons.split(","))
    windows = tuple(int(x) for x in a.windows.split(","))
    out = evaluate_combo(bars, sightings, alerts, horizons=horizons, windows=windows)
    out.update({"code": a.code, "level": level.value, "step": a.step,
                "tail": a.tail, "verify": a.verify, "zs_from": "bi"})
    _print_report(a.code, out, a.primary_window)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("明细写到了", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
