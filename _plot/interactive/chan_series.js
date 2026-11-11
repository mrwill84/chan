// 把 export_interactive.py 导出的一个级别的数据转成 ECharts option。
// 30 分钟、5 分钟两张图共用这一套，只是数据不同。

const COL = {
  up: "#e03131", down: "#2f9e44",           // A 股习惯：红涨绿跌
  bi: "#666", seg: "#111",
  bizs: "#845ef7", segzs: "#a66b2c",
  buy: "#c2255c", sell: "#1971c2",
  sel: "rgba(255, 196, 0, 0.18)",
};

// "2026-09-29 10:35:00" → "09-29 10:35"
const shortTs = (ts) => ts.slice(5, 16);

// 笔、线段首尾相接：确认的连成实线，末端未确认的单独一条虚线
function chain(items) {
  const solid = [], dashed = [];
  items.forEach((s) => {
    const dst = s.conf ? solid : dashed;
    if (dst.length === 0) dst.push([s.x0, s.y0]);
    dst.push([s.x1, s.y1]);
  });
  return { solid, dashed };
}

function lineSeries(name, data, color, width, dashed) {
  return {
    name, type: "line", data, xAxisIndex: 0, yAxisIndex: 0,
    symbol: "none", silent: true, animation: false,
    lineStyle: { color, width, type: dashed ? "dashed" : "solid" },
    z: 3,
  };
}

// 中枢挂在一条空 series 的 markArea 上，这样能单独开关
function zsSeries(name, zss, cats, color) {
  return {
    name, type: "line", data: [], xAxisIndex: 0, yAxisIndex: 0,
    markArea: {
      silent: true,
      itemStyle: { color: color + "1f", borderColor: color, borderWidth: 1.2 },
      label: { show: true, position: "insideTopLeft", color, fontSize: 11 },
      data: zss.map((z) => [
        { xAxis: cats[z.x0], yAxis: z.zg, name: `${z.zd.toFixed(1)}~${z.zg.toFixed(1)}` },
        { xAxis: cats[Math.min(z.x1, cats.length - 1)], yAxis: z.zd },
      ]),
    },
  };
}

function pointSeries(name, pts, isBuy) {
  const c = isBuy ? COL.buy : COL.sell;
  return {
    name, type: "scatter", xAxisIndex: 0, yAxisIndex: 0, z: 10,
    symbol: "triangle", symbolSize: 13,
    symbolRotate: isBuy ? 0 : 180,
    symbolOffset: [0, isBuy ? 12 : -12],
    label: {
      show: true, position: isBuy ? "bottom" : "top", distance: 12,
      fontSize: 12, fontWeight: "bold", color: c,
      formatter: (p) => p.data.text,
    },
    data: pts.filter((p) => p.is_buy === isBuy).map((p) => ({
      value: [p.bar, p.price],
      text: (p.quasi ? "类" : "") + p.label + (p.conf ? "" : "?"),
      pt: p,
      itemStyle: {
        color: p.quasi ? "#fff" : c, borderColor: c, borderWidth: 1.5,
        opacity: p.conf ? 1 : 0.45,
      },
    })),
  };
}

// 每个级别的蜡烛行只建一次（按 ohlc 数组缓存），回放每一步只拷引用
const CANDLES = new WeakMap();
function candleRows(L) {
  let k = CANDLES.get(L.ohlc);
  if (!k) CANDLES.set(L.ohlc, (k = L.ohlc.map((r) => [r[1], r[4], r[3], r[2]]))); // 开 收 低 高
  return k;
}

// opts: { show: {bi, seg, bizs, segzs, pts}, src: "pts_seg"|"pts_bi", sel: [a, b] | null, title,
//         range: [a, b) 只画这几根K线和 MACD（回放用：b 是当下，a 往前留一段），默认全部 }
function buildOption(L, opts) {
  const cats = L.ohlc.map((r) => r[0]);
  const n = L.ohlc.length;
  const [a, b] = opts.range || [0, n];
  // 蜡烛图**不认**行里带的下标（实测：encode/dimensions 都没用，第 N 行就画在第 N 个类目，
  // 前四个数当开收低高），所以只能从 0 给起；截到 b 就行，b 之后的类目空着。
  // 不能用 "-" 填满全长：缩放的过滤去不掉 "-"，一万多条会触发 ECharts 的 large 模式
  // （超过 600 条），蜡烛只剩影线没有实体。MACD 的柱和线认下标，可以只给 [a, b)
  const rows = candleRows(L);
  const k = b === n ? rows : rows.slice(0, b);
  const sh = opts.show;
  const series = [{
    name: "K", type: "candlestick", data: k, xAxisIndex: 0, yAxisIndex: 0,
    itemStyle: { color: COL.up, color0: COL.down, borderColor: COL.up, borderColor0: COL.down },
    barMaxWidth: 12,
  }];

  if (sh.bi) {
    const c = chain(L.bis);
    series.push(lineSeries("笔", c.solid, COL.bi, 1.2, false));
    series.push(lineSeries("笔(未确认)", c.dashed, COL.bi, 1.2, true));
  }
  if (sh.seg) {
    const c = chain(L.segs);
    series.push(lineSeries("线段", c.solid, COL.seg, 2.6, false));
    series.push(lineSeries("线段(未确认)", c.dashed, COL.seg, 2.6, true));
  }
  if (sh.bizs) series.push(zsSeries("笔中枢", L.bi_zs, cats, COL.bizs));
  if (sh.segzs) series.push(zsSeries("线段中枢", L.seg_zs, cats, COL.segzs));
  if (sh.pts) {
    series.push(pointSeries("买点", L[opts.src], true));
    series.push(pointSeries("卖点", L[opts.src], false));
  }

  // 选中范围：30 分钟图上是那一根，5 分钟图上是它对应的 6 根。
  // 用占满整格的柱子画，markArea 在类目轴上首尾同一根时宽度是 0
  series.push({
    id: "sel", name: "sel", type: "bar", xAxisIndex: 0, yAxisIndex: 2,
    barWidth: "100%", silent: true, z: 1, itemStyle: { color: COL.sel },
    data: selData(opts.sel),
  });

  // MACD。红绿柱拆成两条 series（叠在一起），不给每根柱子单独配颜色对象——
  // 一万多根时那些对象是最大的开销
  const pos = [], neg = [], dif = [], dea = [];
  for (let i = a; i < b; i++) {
    const r = L.macd[i];
    (r[2] >= 0 ? pos : neg).push([i, r[2]]);
    dif.push([i, r[0]]);
    dea.push([i, r[1]]);
  }
  const hist = (name, data, color) => ({ name, type: "bar", xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 6,
    stack: "macd", data, itemStyle: { color }, large: true });
  series.push(hist("MACD+", pos, COL.up), hist("MACD-", neg, COL.down));
  series.push({ name: "DIF", type: "line", xAxisIndex: 1, yAxisIndex: 1, symbol: "none",
    data: dif, lineStyle: { width: 1, color: "#333" } });
  series.push({ name: "DEA", type: "line", xAxisIndex: 1, yAxisIndex: 1, symbol: "none",
    data: dea, lineStyle: { width: 1, color: "#f08c00" } });

  const xAxis = (gi, showLabel) => ({
    type: "category", data: cats, gridIndex: gi, boundaryGap: true,
    axisLabel: { show: showLabel, formatter: shortTs },
    axisTick: { show: false }, axisLine: { lineStyle: { color: "#bbb" } },
    axisPointer: { label: { formatter: (p) => shortTs(p.value) } },
  });

  return {
    animation: false,
    title: { text: opts.title, left: 8, top: 2, textStyle: { fontSize: 14 } },
    legend: { show: false },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: {
      trigger: "axis", axisPointer: { type: "cross" },
      formatter: (ps) => tipText(L, ps, b),
    },
    grid: [
      { left: 60, right: 20, top: 26, bottom: "30%" },
      { left: 60, right: 20, top: "74%", bottom: 40 },
    ],
    xAxis: [xAxis(0, false), xAxis(1, true)],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: "#f0f0f0" } } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      { gridIndex: 0, min: 0, max: 1, show: false }, // 选中高亮专用
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], height: 16, bottom: 6 },
    ],
    series,
  };
}

// [a, b] → 稀疏柱子数据，每根占满 0~1
function selData(sel) {
  const out = [];
  if (sel) for (let i = sel[0]; i <= sel[1]; i++) out.push([i, 1]);
  return out;
}

// 时间戳 → K线下标，按 ohlc 数组缓存（回放每步都是新的 L，但 ohlc 是同一个）
const TS_INDEX = new WeakMap();
function indexOfTs(L, ts) {
  let m = TS_INDEX.get(L.ohlc);
  if (!m) TS_INDEX.set(L.ohlc, (m = new Map(L.ohlc.map((r, i) => [r[0], i]))));
  return m.get(ts);
}

// end：只有 end 之前的K线算看得见（回放时之后的是未来）
function tipText(L, ps, end) {
  const i = indexOfTs(L, ps[0].axisValue);
  if (i == null || i >= end) return ps[0].axisValue;
  const r = L.ohlc[i];
  const m = L.macd[i];
  let s = `${r[0]}<br>开 ${r[1].toFixed(2)} 高 ${r[2].toFixed(2)}<br>低 ${r[3].toFixed(2)} 收 ${r[4].toFixed(2)}`;
  s += `<br>DIF ${m[0]} DEA ${m[1]} 柱 ${m[2]}`;
  return s;
}
