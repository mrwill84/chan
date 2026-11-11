"""把级别树逐层打印出来。

用法：PYTHONPATH=. python _plot/tree_dump.py <code> <level>
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from chan.pipeline import analyze
from chan.tree import build_tree
from chan.zhongshu import classify, legs_from_segs, relation
from feeds import Level, cache


def ts(bars, bis, leg, end=False):
    return bars[bis[leg.src_end].end.peak_idx if end
                else bis[leg.src_start].start.peak_idx].ts[:10]


def main(code, level):
    bars = cache.load(code, Level(level), source="baostock")
    a = analyze(bars, check=True)
    legs = legs_from_segs(a.segs, a.bis)
    layers = build_tree(legs)
    for L in layers:
        print(f"\n=== L{L.level}  legs={len(L.legs)}  中枢={len(L.zhongshus)}"
              f"  走势类型={len(L.trends)}  拆开的={sorted(L.split_from)}")
        for z in L.zhongshus:
            d = ""
            if L.level == 0:
                d = (f"  {ts(bars, a.bis, L.legs[z.start_leg])}"
                     f"~{ts(bars, a.bis, L.legs[z.core_end], True)}")
            print(f"  中枢{z.index:<2} 段{z.start_leg}..{z.end_leg:<3}"
                  f" {z.zd:6.2f}~{z.zg:6.2f}  GG{z.gg:6.2f} DD{z.dd:6.2f}"
                  f" {z.core_end - z.start_leg + 1}段"
                  f"{'  [拆]' if z.index in L.split_from else ''}{d}")
        for t in L.trends:
            print(f"  走势{t.index:<2} {t.kind:<9} 段{t.start_leg}..{t.end_leg:<3}"
                  f" [{t.low:.2f},{t.high:.2f}] {t.start_price:.2f}->"
                  f"{t.end_price:.2f}  中枢{[z.index for z in t.zhongshus]}")
        if len(L.zhongshus) > 1:
            print("  相邻关系: " + " | ".join(
                f"{j}→{j+1} {relation(L.zhongshus[j], L.zhongshus[j+1])}"
                for j in range(len(L.zhongshus) - 1)))
        print(f"  classify = {classify(L.zhongshus)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
