"""92课「中枢震荡的监视器」：Z / Zn，以及下一震荡区间的条件外推。

中枢区间 [A,B] = [ZD,ZG]，中轴 Z=(A+B)/2；每个次级别震荡的中点是
Zn=(low+high)/2。Zn 在 Z 上为偏强、下为偏弱；越过 A/B 是变盘提醒，
**不等于已经形成第三类买卖点**——92课明确说超越可以有多次。

外推不是概率预测：用最近两个 Zn 做线性外推，再把下一震荡的另一个端点
放在 ``2 * Zn_next - 当前端点``。它给出的是一张可证伪的价位表，反向或
跌回中枢即失效；不能写成「必然到达」。
"""
from __future__ import annotations

from dataclasses import dataclass

from .num import gt, lt
from .types import Direction, Leg, ZhongShu
from .zhongshu import usable_count


@dataclass(frozen=True, slots=True)
class ZnReading:
    leg: int
    direction: Direction
    zn: float
    strength: str       # above_z / on_z / below_z
    position: str       # above_b / inside / below_a
    confirmed: bool

    @property
    def crossed(self) -> bool:
        return self.position != "inside"


@dataclass(frozen=True, slots=True)
class ZnForecast:
    """下一根次级震荡的几何外推；near 是已经知道的起点，far 是外推终点。"""
    direction: Direction
    near: float
    far: float
    zn: float
    low: float
    high: float
    basis: str = "linear_zn"


def zn_series(zs: ZhongShu, legs: list[Leg], *,
              require_confirmed: bool = True) -> list[ZnReading]:
    """中枢内各次级震荡的 Zn。离开段不属于「围绕中枢震荡」，不列入。"""
    usable = usable_count(legs, require_confirmed)
    end = min(zs.core_end, usable - 1)
    if zs.start_leg < 0 or end >= len(legs):
        raise IndexError("中枢的 leg 下标超出输入序列")
    z = (zs.zd + zs.zg) / 2.0
    out = []
    for i in range(zs.start_leg, end + 1):
        leg = legs[i]
        zn = (leg.low + leg.high) / 2.0
        strength = "above_z" if gt(zn, z) else "below_z" if lt(zn, z) else "on_z"
        position = ("above_b" if gt(zn, zs.zg)
                    else "below_a" if lt(zn, zs.zd) else "inside")
        out.append(ZnReading(i, leg.direction, zn, strength, position,
                             leg.confirmed))
    return out


def forecast_next(zs: ZhongShu, legs: list[Leg], *,
                  require_confirmed: bool = True) -> ZnForecast | None:
    """外推下一震荡；少于两个读数、已经离开、或外推方向矛盾时弃权。"""
    if zs.depart_leg is not None:
        return None
    readings = zn_series(zs, legs, require_confirmed=require_confirmed)
    if len(readings) < 2:
        return None
    last = legs[readings[-1].leg]
    zn = 2.0 * readings[-1].zn - readings[-2].zn
    direction = last.direction.flip()
    near = last.end_price
    far = 2.0 * zn - near
    if direction is Direction.UP and far <= near:
        return None
    if direction is Direction.DOWN and far >= near:
        return None
    return ZnForecast(direction=direction, near=near, far=far, zn=zn,
                      low=min(near, far), high=max(near, far))
