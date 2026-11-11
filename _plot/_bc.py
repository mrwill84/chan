import sys
sys.stdout.reconfigure(encoding="utf-8")
from chan.beichi import _slice_enter, _slice_leave, span_of
from chan.pipeline import analyze
from chan.zhongshu import build_zhongshus, legs_from_segs
from feeds import Level, cache
bars = cache.load("600009", Level("30min"), source="baostock")
a = analyze(bars, check=True)
legs = legs_from_segs(a.segs, a.bis)
zss = build_zhongshus(legs)
print(f"K线 {len(bars)}  段 {len(legs)}  中枢 {len(zss)}")
print(f"{'k':>2} {'中枢ZD~ZG':>16} {'核心段':>9} {'b段':>9} {'c段':>9}  b向 c向  判定")
for k, z in enumerate(zss):
    elo, ehi = _slice_enter(zss, k)
    llo, lhi = _slice_leave(zss, k, len(legs))
    b = span_of(legs, a.bis, elo, ehi)
    c = span_of(legs, a.bis, llo, lhi)
    bd = b.direction.name[0] if b else "-"
    cd = c.direction.name[0] if c else "-"
    if b is None: v = "b 空区间" + (f"（k=0）" if k == 0 else "")
    elif c is None: v = "c 空区间"
    elif b.direction is not c.direction: v = "**b/c 方向相反**"
    else: v = "可判"
    print(f"{k:>2} {z.zd:7.2f}~{z.zg:7.2f} {z.start_leg:3d}..{z.core_end:<3d} "
          f"{elo:3d}..{ehi:<3d} {llo:3d}..{lhi:<3d}  {bd}   {cd}   {v}")
