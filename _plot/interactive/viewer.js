// 30 分钟 / 5 分钟联立查看：点 30 分钟一根 K 线，5 分钟图定位到它内部的 6 根。

const D = window.CHAN_DATA;
if (!D) {
  // 没导出过这只票（直接双击打开时），或 serve.py 导出失败
  const code = new URLSearchParams(location.search).get("code") || "000651";
  document.body.innerHTML = `<pre style="padding:20px;white-space:pre-wrap">${window.CHAN_ERROR
    || `没有 ${code} 的数据。在仓库根目录跑：\nPYTHONPATH=. python _plot/export_interactive.py ${code}\n\n或者用 python _plot/interactive/serve.py 打开看板，点到哪只自动导出哪只。`}</pre>`;
  throw new Error("no data");
}
const M30 = D.m30, M5 = D.m5, SYNC = D.sync;
const c30 = echarts.init(document.getElementById("c30"));
const c5 = echarts.init(document.getElementById("c5"));
const $ = (id) => document.getElementById(id);

const CTX30 = 12; // 点选后 5 分钟图显示所选那根前后各 12 根 30 分钟的范围
const state = { sel30: null, src: "pts_seg" };

// ---- 两个级别的下标换算 ----------------------------------------------------

// 30 分钟第 i 根覆盖的 5 分钟下标 [a, b]。时间戳都是 K 线收盘时刻，
// SYNC[i] 是和它同一时刻收盘的那根 5 分钟
function span5(i) {
  const b = SYNC[i];
  const a = i > 0 ? SYNC[i - 1] + 1 : Math.max(0, b - 5);
  return [a, b];
}

// 5 分钟第 j 根落在哪一根 30 分钟里：第一个 SYNC[i] >= j 的 i
function to30(j) {
  let lo = 0, hi = SYNC.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (SYNC[mid] >= j) hi = mid; else lo = mid + 1;
  }
  return lo;
}

// ---- 缩放 ------------------------------------------------------------------

// 当前可见的下标范围。用百分比换算：startValue 在没缩放过时不一定有
function zoomRange(chart) {
  const o = chart.getOption();
  if (!o || !o.dataZoom || !o.dataZoom.length) return null;
  const n = o.xAxis[0].data.length;
  const z = o.dataZoom[0];
  return [Math.round((z.start / 100) * (n - 1)), Math.round((z.end / 100) * (n - 1))];
}

// 程序自己在缩放时为 true。datazoom 事件是同步触发的，靠它分辨「用户拖的」和「代码挪的」
let PROG_ZOOM = false;

function zoomTo(chart, a, b, n) {
  a = Math.max(0, Math.round(a));
  b = Math.min(n - 1, Math.round(b));
  PROG_ZOOM = true;
  try {
    chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, startValue: a, endValue: b });
  } finally {
    PROG_ZOOM = false;
  }
}

// ---- 绘制 ------------------------------------------------------------------

function showFlags() {
  return {
    bi: $("t_bi").checked, seg: $("t_seg").checked,
    bizs: $("t_bizs").checked, segzs: $("t_segzs").checked,
    pts: $("t_pts").checked,
  };
}

function selRanges() {
  if (state.sel30 == null) return [null, null];
  return [[state.sel30, state.sel30], span5(state.sel30)];
}

// 开关图层时整张重画，但保留两张图当前的缩放
function render() {
  const keep30 = zoomRange(c30);
  const keep5 = zoomRange(c5);
  const [s30, s5] = selRanges();
  const show = showFlags();
  c30.setOption(buildOption(M30, { show, src: state.src, sel: s30, title: `${D.code} 30 分钟` }), true);
  c5.setOption(buildOption(M5, { show, src: state.src, sel: s5, title: `${D.code} 5 分钟（次级别）` }), true);
  if (keep30) zoomTo(c30, keep30[0], keep30[1], M30.ohlc.length);
  if (keep5) zoomTo(c5, keep5[0], keep5[1], M5.ohlc.length);
  renderList();
}

// 只换选中高亮，不重画
function paintSel() {
  const [s30, s5] = selRanges();
  c30.setOption({ series: [{ id: "sel", data: selData(s30) }] });
  c5.setOption({ series: [{ id: "sel", data: selData(s5) }] });
}

// ---- 选中 ------------------------------------------------------------------

// move5：5 分钟图要不要跟着挪过去。点 5 分钟图本身时不挪，免得画面乱跳
function select30(i, move5 = true) {
  i = Math.max(0, Math.min(SYNC.length - 1, i));
  state.sel30 = i;
  paintSel();

  // 所选那根不在 30 分钟可见范围里就把它挪到中间
  const [a30, b30] = zoomRange(c30);
  if (i < a30 || i > b30) {
    const w = b30 - a30;
    zoomTo(c30, i - w / 2, i + w / 2, M30.ohlc.length);
  }
  if (move5) {
    const lo = span5(Math.max(0, i - CTX30))[0];
    const hi = span5(Math.min(SYNC.length - 1, i + CTX30))[1];
    zoomTo(c5, lo, hi, M5.ohlc.length);
  }
  renderInfo();
  markListSel();
}

function fmtPt(p) {
  return `${p.quasi ? "类" : ""}${p.label}${p.conf ? "" : "（未确认）"} ${p.price.toFixed(2)}`;
}

function renderInfo() {
  const i = state.sel30;
  const r = M30.ohlc[i];
  const [a, b] = span5(i);
  let s = `30 分钟 #${i}  ${r[0]}\n开 ${r[1].toFixed(2)}  高 ${r[2].toFixed(2)}\n低 ${r[3].toFixed(2)}  收 ${r[4].toFixed(2)}\n`;
  s += `→ 5 分钟 #${a}~#${b}（${M5.ohlc[a][0].slice(11, 16)}~${M5.ohlc[b][0].slice(11, 16)}）\n`;
  const p30 = M30[state.src].filter((p) => p.bar === i);
  const p5 = M5[state.src].filter((p) => p.bar >= a && p.bar <= b);
  for (const p of p30) s += `\n30分 ${fmtPt(p)}\n  ${p.source}`;
  for (const p of p5) s += `\n5分 ${M5.ohlc[p.bar][0].slice(11, 16)} ${fmtPt(p)}\n  ${p.source}`;
  $("info").textContent = s;
}

// ---- 买卖点列表 -------------------------------------------------------------

let listRows = [];

function renderList() {
  const rows = [];
  for (const p of M30[state.src]) rows.push({ lv: "30分", ts: M30.ohlc[p.bar][0], i30: p.bar, j5: null, p });
  for (const p of M5[state.src]) rows.push({ lv: "5分", ts: M5.ohlc[p.bar][0], i30: to30(p.bar), j5: p.bar, p });
  rows.sort((x, y) => (x.ts < y.ts ? -1 : x.ts > y.ts ? 1 : 0));
  listRows = rows;

  const box = $("plist");
  box.innerHTML = "";
  rows.forEach((r, k) => {
    const div = document.createElement("div");
    div.dataset.k = k;
    if (!r.p.conf) div.classList.add("uc");
    div.innerHTML = `<span class="lv">${r.lv}</span>${r.ts.slice(5, 16)} `
      + `<span class="${r.p.is_buy ? "buy" : "sell"}">${r.p.quasi ? "类" : ""}${r.p.label}</span> `
      + `${r.p.price.toFixed(2)}${r.p.conf ? "" : " ?"}`;
    div.onclick = () => jumpRow(r);
    box.appendChild(div);
  });
  markListSel();
}

function jumpRow(r) {
  if (r.j5 == null) { select30(r.i30); return; }
  // 5 分钟的点：5 分钟图以它为中心，30 分钟选中它所在那根
  select30(r.i30, false);
  const half = CTX30 * 6;
  zoomTo(c5, r.j5 - half, r.j5 + half, M5.ohlc.length);
}

function markListSel() {
  for (const div of $("plist").children) {
    const r = listRows[+div.dataset.k];
    div.classList.toggle("sel", r.i30 === state.sel30);
  }
}

// ---- 事件 ------------------------------------------------------------------

// 点图上任意位置都算点那根 K 线（不必正好点中蜡烛）
function onChartClick(chart, n, cb) {
  chart.getZr().on("click", (e) => {
    const px = [e.offsetX, e.offsetY];
    if (!chart.containPixel({ gridIndex: [0, 1] }, px)) return;
    const x = chart.convertFromPixel({ xAxisIndex: 0 }, px[0]);
    if (x == null || isNaN(x)) return;
    cb(Math.max(0, Math.min(n - 1, Math.round(x))));
  });
}

// sel5：最近一次点的是 5 分钟哪一根（回放从这根开始）；点 30 分钟时清掉
onChartClick(c30, M30.ohlc.length, (i) => {
  if (window.REPLAY) return replayClick("30min", i);
  state.sel5 = null;
  select30(i);
});
onChartClick(c5, M5.ohlc.length, (j) => {
  if (window.REPLAY) return replayClick("5min", j);
  state.sel5 = j;
  select30(to30(j), false);
});

// 勾了「跟随」时，5 分钟图显示 30 分钟可见范围对应的那一段
function follow5() {
  if (!$("t_link").checked) return;
  const [a, b] = zoomRange(c30);
  zoomTo(c5, span5(a)[0], span5(b)[1], M5.ohlc.length);
}
c30.on("datazoom", follow5);

for (const id of ["t_bi", "t_seg", "t_bizs", "t_segzs", "t_pts"]) {
  $(id).onchange = () => (window.REPLAY ? replayRender() : render());
}
for (const el of document.querySelectorAll("input[name=src]")) {
  el.onchange = () => { state.src = el.value; render(); if (state.sel30 != null) renderInfo(); };
}
$("t_link").onchange = follow5;

document.addEventListener("keydown", (e) => {
  if (window.REPLAY) {
    if (e.target.tagName === "INPUT" && e.target.type !== "checkbox") return;
    const act = { ArrowLeft: () => goStep(R.k - 1), ArrowRight: () => goStep(R.k + 1),
      " ": togglePlay, n: () => nextEvent(1), N: () => nextEvent(1) }[e.key];
    if (act) { act(); e.preventDefault(); }
    return;
  }
  if (state.sel30 == null) return;
  if (e.key === "ArrowLeft") { select30(state.sel30 - 1); e.preventDefault(); }
  if (e.key === "ArrowRight") { select30(state.sel30 + 1); e.preventDefault(); }
});
window.addEventListener("resize", () => { c30.resize(); c5.resize(); });

// 中间那条竖条：拖动改右边面板的宽度。图跟着重排，一帧最多一次，不然拖起来卡
$("split").addEventListener("mousedown", (e) => {
  e.preventDefault();
  const bar = $("split"), side = $("side");
  bar.classList.add("drag");
  let pending = false;
  const move = (ev) => {
    side.style.width = Math.max(200, Math.min(window.innerWidth - 400, window.innerWidth - ev.clientX - 3)) + "px";
    if (!pending) {
      pending = true;
      requestAnimationFrame(() => { c30.resize(); c5.resize(); pending = false; });
    }
  };
  const up = () => {
    bar.classList.remove("drag");
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
});

// ---- 初始 ------------------------------------------------------------------

// 从选中的那根开始回放：点过 5 分钟就从那根，否则从选中 30 分钟那根里的第一根 5 分钟
$("r_start").onclick = () => {
  if (state.sel30 == null) return;
  const j = state.sel5 ?? span5(state.sel30)[0];
  const ts = M5.ohlc[j][0].replace(/\D/g, "").slice(0, 12);
  location.href = `viewer.html?code=${D.code}&replay=replay_${D.code}_${ts}`;
};

function normalStart() {
  $("title").textContent = `${D.code}  30 分钟 ${M30.ohlc.length} 根 · 5 分钟 ${M5.ohlc.length} 根`;
  document.title = `${D.code} 30分/5分`;
  render();
  // ?at=2026-09-29 或 ?at=2026-09-29 10:30 直接跳到那一根，默认是最后一根
  const AT = new URLSearchParams(location.search).get("at");
  let i0 = M30.ohlc.length - 1;
  if (AT) {
    const k = M30.ohlc.findIndex((r) => r[0] >= AT);
    if (k >= 0) i0 = k;
  }
  zoomTo(c30, i0 - 120, i0 + 40, M30.ohlc.length);
  select30(i0);
}

const WANT = new URLSearchParams(location.search).get("replay");
if (window.REPLAY) {
  startReplay();
} else {
  normalStart();
  if (WANT) {
    // 要回放但数据没有：直接双击打开且没跑过 export_replay.py，或 serve.py 生成失败
    const m = WANT.match(/^replay_(\d{6})_(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(?:_\d+)?$/);
    const cmd = m ? `PYTHONPATH=. python _plot/export_replay.py ${m[1]} "${m[2]}-${m[3]}-${m[4]} ${m[5]}:${m[6]}" --pages 0` : "";
    $("info").textContent = (window.REPLAY_ERROR || `没有回放数据 ${WANT}。在仓库根目录跑：\n${cmd}\n\n`
      + "或者用 python _plot/interactive/serve.py 打开，会自动生成（约 15 秒）。");
  }
}