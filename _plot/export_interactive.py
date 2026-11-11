"""导出 30 分钟 + 5 分钟的 K 线、笔、线段、中枢、买卖点为 JSON，供交互式 HTML 查看器用。
用法：PYTHONPATH=. python _plot/export_interactive.py 000651 [N_30MIN_BARS] [OUT_DIR]
  默认取最后 2000 根 30 分钟（约 16 个月）和对应时间段的 5 分钟。
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from backtest.live import seg_draw_end
from chan.buysell import find_bspoints
from chan.macd import macd
from chan.pipeline import analyze
from chan.zhongshu import build_zhongshus, legs_from_bis, legs_from_segs
from scan.dashboard import N30, window


def export_level(bars, label: str) -> dict:
    w = [replace(x, idx=i) for i, x in enumerate(bars)]
    a = analyze(w, check=False)
    m = macd([x.close for x in w])

    # K 线
    ohlc = [[x.ts, x.open, x.high, x.low, x.close] for x in w]

    # 笔
    bis = []
    for b in a.bis:
        bis.append({
            "x0": b.start.peak_idx, "y0": b.start.price,
            "x1": b.end.peak_idx, "y1": b.end.price,
            "dir": b.direction.name, "conf": b.confirmed,
        })

    # 线段
    segs = []
    for s in a.segs:
        end = seg_draw_end(s, a.bis)  # 未完成段画到同向极值，见其文档
        segs.append({
            "x0": s.start.peak_idx, "y0": s.start.price,
            "x1": end.peak_idx, "y1": end.price,
            "dir": s.direction.name, "conf": s.confirmed,
        })

    # 笔中枢
    bl = legs_from_bis(a.bis)
    bi_zs = []
    for z in build_zhongshus(bl):
        bi_zs.append({
            "x0": a.bis[bl[z.start_leg].src_start].start.peak_idx,
            "x1": a.bis[bl[z.core_end].src_end].end.peak_idx,
            "zd": z.zd, "zg": z.zg, "dd": z.dd, "gg": z.gg,
        })

    # 线段中枢
    sl = legs_from_segs(a.segs, a.bis)
    seg_zs = []
    for z in build_zhongshus(sl):
        seg_zs.append({
            "x0": a.bis[sl[z.start_leg].src_start].start.peak_idx,
            "x1": a.bis[sl[z.core_end].src_end].end.peak_idx,
            "zd": z.zd, "zg": z.zg, "dd": z.dd, "gg": z.gg,
        })

    # 买卖点（事后）：笔中枢、线段中枢两套各划一遍，查看器里切换
    def bsp(legs):
        return [{
            "bar": p.bar, "price": p.price, "kind": p.kind, "is_buy": p.is_buy,
            "label": p.label, "quasi": p.quasi, "conf": p.confirmed,
            "source": p.source,
        } for p in find_bspoints(legs, build_zhongshus(legs), a.bis, m)]

    # MACD
    macd_data = []
    dif = m.dif
    dea = m.dea
    hist = m.hist
    for i in range(len(w)):
        macd_data.append([round(dif[i], 4), round(dea[i], 4), round(hist[i], 4)])

    return {
        "label": label,
        "ohlc": ohlc,
        "bis": bis,
        "segs": segs,
        "bi_zs": bi_zs,
        "seg_zs": seg_zs,
        "pts_bi": bsp(bl),
        "pts_seg": bsp(sl),
        "macd": macd_data,
    }


def main():
    code = sys.argv[1]
    n30 = int(sys.argv[2]) if len(sys.argv) > 2 else N30
    out = Path(sys.argv[3] if len(sys.argv) > 3 else "_plot/interactive")
    out.mkdir(parents=True, exist_ok=True)

    # 和看板（scan dash）同一个窗口，否则看板上的点在这里可能对不上
    w30, w5 = window(code, n30)

    print(f"{code}: 30min {len(w30)} bars ({w30[0].ts[:10]} ~ {w30[-1].ts[:10]})")
    print(f"       5min  {len(w5)} bars ({w5[0].ts[:10]} ~ {w5[-1].ts[:10]})")

    d30 = export_level(w30, "30min")
    d5 = export_level(w5, "5min")

    # 建立 30 分钟 K 线 ts → 5 分钟 bar 下标的映射（用于同步）
    ts5_map = {x.ts: i for i, x in enumerate(w5)}
    sync = []
    for i, bar in enumerate(w30):
        i5_start = ts5_map.get(bar.ts)
        if i5_start is None:
            # 找最近的
            for j in range(len(w5)):
                if w5[j].ts >= bar.ts:
                    i5_start = j
                    break
        sync.append(i5_start if i5_start is not None else 0)

    data = {"code": code, "m30": d30, "m5": d5, "sync": sync}
    # 写成 .js 而不是 .json：查看器直接双击 file:// 打开，浏览器不让 fetch 本地文件，
    # 但允许 <script src> 加载
    p = out / f"{code}.js"
    with open(p, "w", encoding="utf-8") as f:
        f.write("window.CHAN_DATA = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";\n")
    print(f"→ {p}  ({p.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
