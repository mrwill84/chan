// 回放模式：viewer.html?code=000651&replay=replay_000651_202609281300
// 数据由 _plot/export_replay.py（backtest/live.py）生成：每根 5 分钟收盘时、只用截至那一刻的
// K线做的判断。图上只画那一刻看得到的结构，未来的K线留空。
// 不限长度：数据一页 RP_PAGE 根（viewer.html），走到离已加载的末尾不到 PREFETCH 步就在后台要下一页，
// 一直到最新一根。
// 这个文件只定义函数，由 viewer.js 末尾的 startReplay() 启动，用的是 viewer.js 里的全局。

const R = { k: 0, timer: null, ov: "5min:b:seg", loading: false, loadErr: null, after: null };
let RP = null;
const PREFETCH = 60;

const SHORT = {
  bi_beichi: "笔背驰", sub_depart: "反向离开", sub_third: "反向3类",
  sub_complete: "走势完成", force: "力度", sublevel_1bs: "5分1类",
};
const DIR = { UP: "向上", DOWN: "向下" };
const hm = (ts) => ts.slice(5, 16);

// ---- 当下的数据 --------------------------------------------------------------

// 一个级别在那一刻的结构快照。K线本身不截：buildOption 的 range 只画到当下
function asOf(L, snap) {
  const out = {
    ohlc: L.ohlc, macd: L.macd,
    bis: [], segs: [], bi_zs: [], seg_zs: [], pts_seg: [], pts_bi: [],
  };
  if (snap) {
    const d = snap.draw;
    Object.assign(out, { bis: d.bis, segs: d.segs, bi_zs: d.bi_zs, seg_zs: d.seg_zs, pts_seg: d.pts });
  }
  return out;
}

// 各口径这一刻判的段终点，画成菱形
function declSeries(snap) {
  const by = {};
  for (const m of snap ? snap.methods : []) {
    if (!m.ok) continue;
    const k = `${m.bar}|${m.price}`;
    (by[k] = by[k] || { bar: m.bar, price: m.price, names: [] }).names.push(SHORT[m.id]);
  }
  return {
    name: "判段终", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, z: 12,
    symbol: "diamond", symbolSize: 14, itemStyle: { color: "#f08c00", borderColor: "#fff", borderWidth: 1 },
    label: { show: true, position: "right", color: "#e8590c", fontSize: 12, fontWeight: "bold",
      formatter: (p) => "判段终：" + p.data.names.join("、") },
    data: Object.values(by).map((x) => ({ value: [x.bar, x.price], names: x.names })),
  };
}

// 事后（全部K线）划出来的线段，对照用。默认不画——画了就等于偷看未来
function ghostSeries(L) {
  return {
    name: "事后线段", type: "line", xAxisIndex: 0, yAxisIndex: 0, silent: true, symbol: "none", z: 2,
    lineStyle: { color: "#f76707", width: 2, type: "dashed", opacity: 0.55 },
    data: L.segs.length ? [[L.segs[0].x0, L.segs[0].y0], ...L.segs.map((s) => [s.x1, s.y1])] : [],
  };
}

// ---- 理由画到图上 -------------------------------------------------------------
//
// R.ov = "5min:b:seg" / "30min:m:bi_beichi" 这样的串：哪个级别、背驰(b)还是段结束口径(m)、哪一条。
// 右边点哪条理由，图上就画哪条用到的东西：比的是哪两段/两笔、哪个中枢、MACD 哪两块面积。

const OV_B = { area: "rgba(51,154,240,0.11)", line: "#1c7ed6" };   // 前一段 / b
const OV_C = { area: "rgba(240,62,62,0.11)", line: "#e03131" };    // 当下这段 / c

function band(L, x0, x1, col, label, onMacd) {
  const cats = (i) => L.ohlc[Math.max(0, Math.min(i, L.ohlc.length - 1))][0];
  return {
    type: "line", data: [], silent: true,
    xAxisIndex: onMacd ? 1 : 0, yAxisIndex: onMacd ? 1 : 0,
    markArea: {
      itemStyle: { color: col.area, borderColor: col.line, borderWidth: 1 },
      label: { show: true, position: onMacd ? "insideTop" : "insideBottom", color: col.line, fontWeight: "bold", fontSize: 12 },
      data: [[{ xAxis: cats(x0), name: label }, { xAxis: cats(x1) }]],
    },
  };
}

function zsBox(L, z, label) {
  const cats = (i) => L.ohlc[Math.min(i, L.ohlc.length - 1)][0];
  return {
    type: "line", data: [], silent: true, xAxisIndex: 0, yAxisIndex: 0, z: 6,
    markArea: {
      itemStyle: { color: "rgba(0,0,0,0)", borderColor: "#212529", borderWidth: 2.5, borderType: "dashed" },
      label: { show: true, position: "insideTopRight", color: "#212529", fontWeight: "bold" },
      data: [[{ xAxis: cats(z.x0), yAxis: z.zg, name: label }, { xAxis: cats(z.x1), yAxis: z.zd }]],
    },
  };
}

const f1 = (v) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));

// 这一级别的图上要加的理由图层。ov 不是这一级别的就什么都不加
function overlaySeries(L, lv, snap) {
  const [olv, kind, id] = R.ov.split(":");
  if (olv !== lv || !snap) return [];
  const out = [];
  if (kind === "b") {
    const b = snap.beichi[id];
    if (!b || !b.geo) return [];  // 选的是某种读法，这一步没有它了
    const g = b.geo;
    const bn = g.b_name || "b 段";
    out.push(zsBox(L, g.zs, `${g.zs_name || "中枢 B"} [${f1(g.zs.zd)}, ${f1(g.zs.zg)}]`));
    if (g.za) out.push(zsBox(L, g.za, `中枢 A [${f1(g.za.zd)}, ${f1(g.za.zg)}]`));
    out.push(band(L, g.b[0], g.b[1], OV_B, bn), band(L, g.c[0], g.c[1], OV_C, "离开段 c（当下）"));
    out.push(band(L, g.b[0], g.b[1], OV_B, `${bn} 面积 ${f1(g.b[2])}`, true),
      band(L, g.c[0], g.c[1], OV_C, `c 面积 ${f1(g.c[2])}`, true));
    if (g.start) {
      // 当下走势从哪里起步：默认读法把它吸进了前一个走势的中枢时才有
      out.push({
        type: "scatter", silent: true, xAxisIndex: 0, yAxisIndex: 0, z: 11, symbol: "pin", symbolSize: 30,
        itemStyle: { color: "#5f3dc4" },
        label: { show: true, position: "top", color: "#5f3dc4", fontWeight: "bold", formatter: "当下走势起点" },
        data: [[g.start[0], g.start[1]]],
      });
    }
    return out;
  }
  const m = snap.methods.find((x) => x.id === id);
  const g = m && m.geo;
  if (!g) return [];
  out.push(band(L, g.tail[0], snap.n - 1, { area: "rgba(134,142,150,0.08)", line: "#868e96" }, "末段"));
  if (g.strokes) {
    g.strokes.forEach((s, i) => {
      const col = i ? OV_C : OV_B;
      out.push({
        type: "line", silent: true, xAxisIndex: 0, yAxisIndex: 0, z: 8, symbol: "circle", symbolSize: 7,
        lineStyle: { color: col.line, width: 5, opacity: 0.8 }, itemStyle: { color: col.line },
        label: { show: true, position: "top", color: col.line, fontWeight: "bold", formatter: (p) => (p.dataIndex ? s.name : "") },
        data: [[s.x0, s.y0], [s.x1, s.y1]],
      });
      out.push(band(L, s.x0, s.x1, col, `${s.name} 面积 ${f1(s.area)}`, true));
    });
  }
  if (g.zs) out.push(zsBox(L, g.zs, `段内笔中枢 [${f1(g.zs.zd)}, ${f1(g.zs.zg)}]`));
  if (g.runs) {
    out.push(band(L, g.runs[0][0], g.runs[0][1], OV_B, "前一个吻", true),
      band(L, g.runs[1][0], g.runs[1][1], OV_C, "当下的吻", true));
  }
  return out;
}

// 理由图层最左边画到哪根，跟随时把它也框进来——b 段常在好几百根之前
function ovStart(lv, snap) {
  const [olv, kind, id] = R.ov.split(":");
  if (olv !== lv || !snap) return Infinity;
  if (kind === "b") {
    const g = snap.beichi[id]?.geo;
    return g ? Math.min(g.zs.x0, g.b[0], g.za ? g.za.x0 : Infinity, g.start ? g.start[0] : Infinity) : Infinity;
  }
  const g = (snap.methods.find((x) => x.id === id) || {}).geo;
  if (!g) return Infinity;
  return Math.min(g.tail[0], ...(g.strokes || []).map((s) => s.x0), g.zs ? g.zs.x0 : Infinity,
    ...(g.runs || []).map((r) => r[0]));
}

function ovTitle() {
  const [lv, kind, id] = R.ov.split(":");
  const name = kind === "b"
    ? ({ seg: "线段中枢背驰", bi: "笔中枢背驰（末段内）" }[id] || `笔中枢·另一种读法 ${id.slice(4)}`)
    : SHORT[id];
  return `　图上画的理由：${lv === "5min" ? "5分" : "30分"} ${name}（点右边任一条理由切换）`;
}

// ---- 绘制 ------------------------------------------------------------------

// 「当下」那根：竖线 + 标签，一走一根看得见
function nowLine(L, n) {
  return {
    type: "line", data: [], silent: true, xAxisIndex: 0, yAxisIndex: 0,
    markLine: {
      symbol: "none", lineStyle: { color: "#f08c00", width: 2 },
      label: { formatter: "当下", color: "#e8590c", fontWeight: "bold" },
      data: [{ xAxis: L.ohlc[n - 1][0] }],
    },
  };
}

//: 当下之前画多少根。再往前拖就是空的——全画一万多根每走一步都要重算，太卡
const BACK = { "5min": 2500, "30min": 800 };

// 第一次整张设；之后每一步只换 series（replaceMerge），类目轴和缩放都不动，省掉大头
function drawCharts() {
  const st = RP.steps[R.k];
  const n5 = st.t + 1, n30 = st.n30;
  const show = showFlags();
  const draw = (chart, L, lv, n, snap, title) => {
    const from = Math.max(0, Math.min(n - BACK[lv], ovStart(lv, snap) - 20));
    const o = buildOption(asOf(L, snap), { show, src: "pts_seg", sel: n > 0 ? [n - 1, n - 1] : null, title, range: [from, n] });
    o.series.push(declSeries(snap), nowLine(L, n), ...overlaySeries(L, lv, snap));
    if ($("r_ghost").checked) o.series.push(ghostSeries(L));
    if (!R.inited) chart.setOption(o, true);
    else chart.setOption({ title: o.title, series: o.series }, { replaceMerge: ["series"] });
  };
  draw(c30, M30, "30min", n30, st["30min"], `${D.code} 30 分钟（当下：已收盘到 ${hm(M30.ohlc[n30 - 1][0])}）`);
  draw(c5, M5, "5min", n5, st["5min"], `${D.code} 5 分钟（当下 ${hm(st.ts)}）` + ovTitle());
  if ($("r_follow").checked || !R.inited) {
    zoomTo(c30, Math.min(n30 - 80, ovStart("30min", st["30min"]) - 5), n30 + 8, M30.ohlc.length);
    zoomTo(c5, Math.min(n5 - 130, ovStart("5min", st["5min"]) - 10), n5 + 12, M5.ohlc.length);
  }
  R.inited = true;
}

// 自己拖动/缩放了就别再自动跟：关掉「跟随」，想回来再勾上
function stopFollowOnUserZoom(chart) {
  chart.on("datazoom", () => {
    if (PROG_ZOOM || !$("r_follow").checked) return;
    $("r_follow").checked = false;
  });
}

function updatePos() {
  const st = RP.steps[R.k];
  const tail = R.loadErr ? `　⚠ ${R.loadErr}`
    : R.loading ? "　后面的在加载…"
    : RP.next ? "" : "　已到最新一根";
  $("r_pos").textContent = `${R.k + 1} / ${RP.steps.length}　${st.ts.slice(0, 16)}${tail}`;
}

function setTitle() {
  const h = RP.health;
  $("title").textContent = `${D.code} 回放 ${RP.ts0.slice(0, 16)} 起，已加载到 ${RP.ts1.slice(0, 16)}（${RP.steps.length} 根 5 分钟）`
    + (h.unstable.length || h.failed.length ? `　⚠ 自检：${h.unstable.length} 步不稳定、${h.failed.length} 块没算出来` : "");
}

function replayRender() {
  const st = RP.steps[R.k];
  drawCharts();
  $("r_slider").value = R.k;
  updatePos();
  renderReport(st);
  markEvents();
}

// ---- 一页一页往后加载 ---------------------------------------------------------

// 页文件和第一页一样是 window.REPLAY = {...}：插 <script> 载进来（双击打开时 fetch 用不了），
// 读完马上把 window.REPLAY 换回 RP——viewer.js 拿它判断是不是回放模式
function loadNext() {
  if (R.loading || R.loadErr || !RP.next) return;
  R.loading = true;
  const name = `replay_${D.code}_${RP.next.replace(/\D/g, "").slice(0, 12)}_${RP_PAGE}`;
  const fail = (msg) => {
    R.loading = false; R.loadErr = msg; R.after = null; window.REPLAY = RP; updatePos();
  };
  window.REPLAY_ERROR = undefined;
  const s = document.createElement("script");
  s.src = name + ".js";
  s.onerror = () => fail(`没拿到 ${name}.js：没开 serve.py 的话先跑 export_replay.py … --pages 0`);
  s.onload = () => {
    const pg = window.REPLAY;
    window.REPLAY = RP;
    s.remove();
    if (window.REPLAY_ERROR) return fail(window.REPLAY_ERROR);
    if (!pg || pg === RP) return fail(`${name}.js 里没有回放数据`);
    // K线缓存中途更新过，窗口挪了，新页的下标和已加载的对不上
    if (pg.t0 !== RP.t1 + 1 || pg.ts0 !== M5.ohlc[pg.t0]?.[0]) return fail("K线缓存更新过，后面的页接不上，刷新页面重来");
    const from = RP.steps.length;
    RP.steps.push(...pg.steps);
    Object.assign(RP, { t1: pg.t1, ts1: pg.ts1, next: pg.next });
    RP.health.unstable.push(...pg.health.unstable);
    RP.health.failed.push(...pg.health.failed);
    R.loading = false;
    $("r_slider").max = RP.steps.length - 1;
    renderEvents(from);
    setTitle();
    const after = R.after;
    R.after = null;
    if (after) after();
    else { updatePos(); if ($("r_sum")?.open) renderReport(RP.steps[R.k]); }
    prefetch();
  };
  updatePos();
  document.body.appendChild(s);
}

function prefetch() {
  if (RP.steps.length - 1 - R.k < PREFETCH) loadNext();
}

// 要走到还没加载的地方：先停在已加载的末尾，等下一页到了再接着做 then
function waitMore(then) {
  if (!RP.next || R.loadErr) return false;
  R.after = then;
  loadNext();
  return true;
}

function goStep(k) {
  if (k > RP.steps.length - 1) waitMore(() => goStep(k));
  R.k = Math.max(0, Math.min(RP.steps.length - 1, k));
  replayRender();
  prefetch();
}

function nextEvent(dir = 1) {
  for (let k = R.k + dir; k >= 0 && k < RP.steps.length; k += dir) {
    if (RP.steps[k].events.length) return goStep(k);
  }
  if (dir > 0) waitMore(() => nextEvent(1));
}

function togglePlay() {
  if (R.timer) { clearInterval(R.timer); R.timer = null; }
  else {
    R.timer = setInterval(() => {
      if (R.k >= RP.steps.length - 1) {
        // 下一页还在路上就等着，真到头了才停
        if (R.loading || waitMore(null)) return;
        return togglePlay();
      }
      goStep(R.k + 1);
    }, 1000 / +$("r_speed").value);
  }
  $("r_play").textContent = R.timer ? "❚❚ 暂停" : "▷ 播放";
}

// 点图：跳到那一根收盘的那一步
function replayClick(level, i) {
  const t = level === "5min" ? i : SYNC[i];
  const k = t - RP.t0;
  if (k >= 0 && k < RP.steps.length) goStep(k);
}

function replayControls() {
  const g = document.createElement("div");
  g.className = "grp";
  g.innerHTML = `<b style="color:#e8590c">回放</b>
    <button id="r_first">⏮</button><button id="r_pev">⇤ 上个事件</button>
    <button id="r_prev">◀</button><button id="r_next">▶</button>
    <button id="r_nev">下个事件 ⇥</button><button id="r_play">▷ 播放</button>
    <select id="r_speed"><option value="1">1 根/秒</option><option value="3" selected>3 根/秒</option><option value="8">8 根/秒</option></select>
    <input type="range" id="r_slider" min="0" max="${RP.steps.length - 1}" style="width:220px">
    <span id="r_pos"></span>
    <label title="自己拖动或缩放图后会自动关掉，勾上回到当下"><input type="checkbox" id="r_follow" checked> 跟随当下</label>
    <label><input type="checkbox" id="r_ghost"> 显示事后线段（偷看未来）</label>
    <a href="viewer.html?code=${D.code}&at=${encodeURIComponent(RP.ts0)}">退出回放</a>`;
  $("bar").appendChild(g);
  $("r_first").onclick = () => goStep(0);
  $("r_prev").onclick = () => goStep(R.k - 1);
  $("r_next").onclick = () => goStep(R.k + 1);
  $("r_nev").onclick = () => nextEvent(1);
  $("r_pev").onclick = () => nextEvent(-1);
  $("r_play").onclick = togglePlay;
  $("r_speed").onchange = () => { if (R.timer) { togglePlay(); togglePlay(); } };
  $("r_slider").oninput = (e) => goStep(+e.target.value);
  $("r_ghost").onchange = replayRender;
  // 普通模式的这几个控件回放时没意义
  for (const el of document.querySelectorAll("input[name=src], #t_link, #r_start")) el.disabled = true;
  document.querySelector("#bar .help").textContent = "←/→ 一根一根走 · 空格 播放 · N 下个事件 · 点图上某根跳到那一刻";
}

// ---- 右侧：这一刻的判断和理由 --------------------------------------------------

const escH = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
const ul = (lines) => `<ul>${lines.map((x) => `<li>${escH(x)}</li>`).join("")}</ul>`;

function levelHtml(L, tag, lv) {
  if (!L) return `<h5>${tag}</h5><p class="dim">K线还不够，划不出线段</p>`;
  const t = L.tail, p = L.prev;
  let h = `<h5>${tag}</h5><div class="rp-tail">末段：${DIR[t.dir]}，${hm(t.start_ts)} 从 ${t.y0.toFixed(2)} 起，`
    + `${t.nbis} 笔，<b>特征序列还没确认</b>`
    + (p ? `<br><span class="dim">上一段（已确认）：${DIR[p.dir]}，结束于 ${hm(p.ts)} ${p.y1.toFixed(2)}</span>` : "")
    + "</div>";
  const n = L.methods.filter((m) => m.ok).length;
  h += `<div class="rp-sec">末段当下结束了吗？<b>${n ? `${n} 个口径说已结束` : "各口径都说还没结束"}</b></div>`;
  for (const m of L.methods) {
    const head = m.ok ? `✓ ${SHORT[m.id]}：已在 ${m.price.toFixed(2)} 结束` : `✗ ${SHORT[m.id]}`;
    h += `<details data-ov="${lv}:m:${m.id}"${m.ok ? " open" : ""}><summary class="${m.ok ? "yes" : "no"}">${head}</summary>`
      + `<div class="dim">${escH(RP.methods[m.id])}</div>${ul(m.why)}</details>`;
  }
  h += `<div class="rp-sec">背驰</div>`;
  // bi_r1、bi_r2…：末段里的其他笔中枢读法（33课 多义性），这一步有几种就列几种
  const alts = Object.keys(L.beichi).filter((k) => k.startsWith("bi_r")).sort()
    .map((k) => [k, `　└ 另一种读法 ${k.slice(4)}`]);
  for (const [k, name] of [["seg", "线段中枢（c 是当下这段）"], ["bi", "笔中枢（只用末段里的笔，aAb / aAbBc）"], ...alts]) {
    const b = L.beichi[k];
    if (!b) continue;  // 旧数据没有这一项
    const head = b.hit ? `✓ ${name}：${b.kind === "trend" ? "趋势" : "盘整"}${b.top ? "顶" : "底"}背驰` : `✗ ${name}`;
    h += `<details data-ov="${lv}:b:${k}"${b.hit ? " open" : ""}><summary class="${b.hit ? "yes" : "no"}">${head}</summary>${ul(b.why)}</details>`;
  }
  if ("now" in L) {
    h += L.now
      ? `<div class="yes">✓ seg_beichi_now（11-03 规则）：${L.now.kind === "trend" ? "趋势" : "盘整"}底背驰，低点 ${L.now.low.toFixed(2)}${L.now.conf ? "" : "（那笔未确认）"}</div>`
      : `<div class="no">✗ seg_beichi_now（11-03 规则，只判底背驰）</div>`;
  }
  return h;
}

// 事后线段终点 vs 当下：每个终点各口径最早第几步判出，晚了几根 5 分钟。
// 事后 = 查看器数据里用全部K线划的线段（M5.segs / M30.segs）。只对账落在已加载区间里的终点，
// 所以每多载一页都要重算；算一遍要扫全部步，只在展开时算，按已加载步数缓存
function scoreLevel(lv, L, to5, lo, hi) {
  const ends = new Set(L.segs.map((s) => s.x1));
  const rows = L.segs.filter((s) => s.x1 >= lo && s.x1 <= hi)
    // conf=false 的是全部K线里最后那段，终点只是「目前走到这」，不是真终点
    .map((s) => ({ bar: s.x1, price: s.y1, dir: s.dir, conf: s.conf, seq: null, m: {} }));
  const at = new Map(rows.map((r) => [r.bar, r]));
  const seen = {};
  for (const st of RP.steps) {
    const S = st[lv];
    if (!S) continue;
    const r0 = S.prev && at.get(S.prev.x1);
    if (r0 && r0.seq == null) r0.seq = st.t - to5(r0.bar);
    for (const x of S.methods) {
      seen[x.id] = seen[x.id] || new Set();
      if (!x.ok) continue;
      seen[x.id].add(x.bar);
      const r = at.get(x.bar);
      if (r && !(x.id in r.m)) r.m[x.id] = st.t - to5(r.bar);
    }
  }
  const fal = {};
  for (const [id, bars] of Object.entries(seen)) {
    fal[id] = { declared: bars.size, wrong: [...bars].filter((b) => !ends.has(b)).length };
  }
  return { ends: rows, false: fal };
}

function summary() {
  if (R.sum && R.sum.n === RP.steps.length) return R.sum;
  const ts0 = M5.ohlc[RP.t0][0], ts1 = M5.ohlc[RP.t1][0];
  const lo30 = M30.ohlc.findIndex((r) => r[0] > ts0);
  let hi30 = M30.ohlc.findIndex((r) => r[0] > ts1);
  hi30 = (hi30 < 0 ? M30.ohlc.length : hi30) - 1;
  R.sum = {
    n: RP.steps.length,
    "5min": scoreLevel("5min", M5, (e) => e, RP.t0, RP.t1),
    "30min": scoreLevel("30min", M30, (e) => SYNC[e], lo30 < 0 ? Infinity : lo30, hi30),
  };
  return R.sum;
}

function summaryHtml() {
  let h = "";
  const SUM = summary();
  for (const [lv, tag] of [["5min", "5 分钟"], ["30min", "30 分钟"]]) {
    const S = SUM[lv];
    const ids = Object.keys(S.false);
    h += `<h5>${tag}：事后的线段终点，当下晚几根才判出来（单位：5 分钟根数）</h5>`;
    if (!S.ends.length) { h += `<p class="dim">这段时间里事后没有${tag}线段结束</p>`; continue; }
    h += `<table class="rp-tbl"><tr><th>终点</th><th>特征序列</th>${ids.map((i) => `<th>${SHORT[i]}</th>`).join("")}</tr>`;
    const lvL = lv === "5min" ? M5 : M30;
    for (const r of S.ends) {
      const cell = (v) => (v == null ? `<td class="dim">没判出</td>` : `<td>${v}</td>`);
      h += `<tr><td>${DIR[r.dir]} ${hm(lvL.ohlc[r.bar][0])} ${r.price.toFixed(2)}${r.conf ? "" : "（末段，未完）"}</td>`
        + cell(r.seq) + ids.map((i) => cell(r.m[i])).join("") + "</tr>";
    }
    h += `<tr class="dim"><td>判过的终点里错的</td><td></td>${ids.map((i) => `<td>${S.false[i].wrong}/${S.false[i].declared}</td>`).join("")}</tr></table>`;
  }
  return h + `<p class="dim">只算已加载的 ${RP.ts0.slice(0, 16)} ~ ${RP.ts1.slice(0, 16)}，往后走会接着加。`
    + `「错的」= 判出来的终点事后不是任何线段的终点。事后 = 用全部K线划的线段（就是普通模式下的那一套）。</p>`;
}

function renderReport(st) {
  const ev = st.events.length ? `<div class="rp-ev">${st.events.map(escH).join("<br>")}</div>` : "";
  $("info").innerHTML = `<div class="rp-head">${st.ts.slice(0, 16)}　收 ${st.close.toFixed(2)}</div>${ev}`
    + levelHtml(st["5min"], "5 分钟", "5min")
    + levelHtml(st["30min"], `30 分钟（已收盘到 ${hm(M30.ohlc[st.n30 - 1][0])}）`, "30min")
    + `<details id="r_sum"${R.sumOpen ? " open" : ""}><summary><b>事后对账：能不能当下判断</b></summary>`
    + `<div id="r_sum_body">${R.sumOpen ? summaryHtml() : ""}</div></details>`;
  // 每步都重画，记住展开状态，免得一走一根就收起来；收着的时候不算
  $("r_sum").ontoggle = (e) => {
    R.sumOpen = e.target.open;
    if (R.sumOpen) $("r_sum_body").innerHTML = summaryHtml();
  };
  // 点哪条理由，图上就画哪条
  for (const d of $("info").querySelectorAll("details[data-ov]")) {
    d.classList.toggle("ov", d.dataset.ov === R.ov);
    d.querySelector("summary").addEventListener("click", () => {
      R.ov = d.dataset.ov;
      for (const x of $("info").querySelectorAll("details[data-ov]")) x.classList.toggle("ov", x === d);
      drawCharts();
    });
  }
}

// ---- 事件列表 ----------------------------------------------------------------

// from = 从第几步起往后补（新载进一页时只补那一页）
function renderEvents(from = 0) {
  const box = $("plist");
  if (!from || box.querySelector(".rp-none")) box.innerHTML = "";
  for (let k = from; k < RP.steps.length; k++) {
    const st = RP.steps[k];
    for (const e of st.events) {
      const div = document.createElement("div");
      div.dataset.k = k;
      // 跨天了，时刻前面带上日期
      div.innerHTML = `<span class="lv">${hm(st.ts)}</span>${escH(e)}`;
      div.title = st.ts;
      div.onclick = () => goStep(k);
      box.appendChild(div);
    }
  }
  if (!box.children.length) box.innerHTML = `<div class="rp-none dim">已加载的这段里判断一次都没变过</div>`;
}

function markEvents() {
  let first = null;
  for (const div of $("plist").children) {
    const on = +div.dataset.k === R.k;
    div.classList.toggle("sel", on);
    if (on && !first) first = div;
  }
  first?.scrollIntoView({ block: "nearest" });
}

// ---- 启动 ------------------------------------------------------------------

function startReplay() {
  RP = window.REPLAY;
  RP.next = RP.next ?? null;
  document.body.classList.add("replay");
  document.querySelector("#side h4").textContent = "这一刻的判断和理由";
  document.querySelectorAll("#side h4")[1].textContent = "判断变化的时刻（点击跳过去）";
  setTitle();
  document.title = `${D.code} 回放`;
  replayControls();
  stopFollowOnUserZoom(c30);
  stopFollowOnUserZoom(c5);
  $("r_follow").onchange = () => { if ($("r_follow").checked) drawCharts(); };
  renderEvents();
  c30.resize(); c5.resize();
  // ?at=2026-09-30 14:10 直接跳到那一刻
  const q = new URLSearchParams(location.search);
  R.sumOpen = q.get("sum") === "1";
  R.ov = q.get("ov") || R.ov;  // ?ov=5min:m:bi_beichi 指定图上先画哪条理由
  const at = q.get("at");
  const k = at ? RP.steps.findIndex((s) => s.ts >= at) : -1;
  // at 在第一页后面：一页页往后载，直到载到它
  const seek = () => {
    const i = RP.steps.findIndex((s) => s.ts >= at);
    if (i >= 0 || !waitMore(seek)) goStep(i >= 0 ? i : RP.steps.length - 1);
  };
  if (at && k < 0) { goStep(RP.steps.length - 1); seek(); }
  else goStep(Math.max(0, k));
}
