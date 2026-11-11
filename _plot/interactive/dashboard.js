// 看板：一只一行，点一行在右边打开 viewer.html。数据由 `python -m scan dash` 生成。

const D = window.DASH;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

if (!D) {
  document.body.innerHTML = "<p style='padding:20px'>没有 dashboard_data.js。先在仓库根目录跑：<br>"
    + "<code>PYTHONPATH=. python -m scan dash --jobs 16 --progress</code></p>";
  throw new Error("no data");
}

// 「最新」以全体里最晚的那根为准；比它旧的是缓存没更新，不是没行情
const LATEST = D.rows.reduce((m, r) => (r.last_ts > m ? r.last_ts : m), "");
const TREND = { up: "上涨", down: "下跌", panzheng: "盘整" };
const NEST = { agree: "一致", conflict: "冲突", incomplete: "不完整" };
const UNSTABLE = new Set(D.health.unstable);

const state = { sort: "m30_pt", dir: 1, sel: null, lv: "m30" };

// ---- 取点 ------------------------------------------------------------------

// 一个级别上「最新的点」：按顶栏的「含类买卖点」「只要已确认」挑。
// 只在最近 RECENT 个里挑（scan/dashboard.py），挑不到就是 null
function pick(L) {
  const q = $("f_quasi").checked, c = $("f_conf").checked;
  for (let i = L.points.length - 1; i >= 0; i--) {
    const p = L.points[i];
    if (!q && p.quasi) continue;
    if (c && !p.conf) continue;
    return p;
  }
  return null;
}

const ptName = (p) => (p.quasi ? "类" : "") + p.label;

function ptCell(L) {
  const p = pick(L);
  const tip = L.points.map((x) => `${x.ts.slice(5, 16)} ${ptName(x)}${x.conf ? "" : "(未确认)"} ${x.price.toFixed(2)}  ${x.source}`).join("\n");
  if (!p) return `<td class="dim" title="${esc(tip)}">—</td>`;
  return `<td title="${esc(tip)}" class="${p.conf ? "" : "uc"}"><span class="${p.buy ? "buy" : "sell"}">${ptName(p)}</span>`
    + ` ${p.ts.slice(5, 16)} <span class="dim">${p.ago}根</span></td>`;
}

function retCell(L) {
  const p = pick(L);
  if (!p) return "<td></td>";
  const v = p.ret * 100;
  return `<td class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</td>`;
}

function nestCell(r) {
  const n = r.nest;
  const p = n.precise;
  const tip = [...n.steps, ...n.notes].join("\n");
  const where = p ? ` <span class="dim">${p.level === "5min" ? "5分" : "30分"} ${p.quasi ? "类" : ""}${p.label} ${p.ts.slice(5, 16)}</span>` : "";
  return `<td title="${esc(tip)}"><span class="${n.agreement}">${NEST[n.agreement] || n.agreement}</span>${where}</td>`;
}

// ---- 列 --------------------------------------------------------------------

const BIG = 1e12; // 没有点的排在最后
const COLS = [
  { key: "code", title: "代码", val: (r) => r.code, cell: (r) => `<td>${r.code}</td>` },
  { key: "last_ts", title: "最新K线", val: (r) => r.last_ts,
    cell: (r) => `<td class="${r.last_ts < LATEST ? "stale" : ""}">${r.last_ts.slice(5, 16)}</td>` },
  { key: "close", title: "收盘", val: (r) => r.close, cell: (r) => `<td>${r.close.toFixed(2)}</td>` },
  { key: "m30_trend", title: "30分走势", val: (r) => r.m30.trend, cell: (r) => `<td>${TREND[r.m30.trend] || r.m30.trend}</td>` },
  { key: "m30_pt", title: "30分最新点", val: (r) => pick(r.m30)?.ago ?? BIG, cell: (r) => ptCell(r.m30) },
  { key: "m30_ret", title: "至今", val: (r) => pick(r.m30)?.ret ?? -BIG, cell: (r) => retCell(r.m30) },
  { key: "m5_trend", title: "5分走势", val: (r) => r.m5.trend, cell: (r) => `<td>${TREND[r.m5.trend] || r.m5.trend}</td>` },
  { key: "m5_pt", title: "5分最新点", val: (r) => pick(r.m5)?.ago ?? BIG, cell: (r) => ptCell(r.m5) },
  { key: "m5_ret", title: "至今", val: (r) => pick(r.m5)?.ret ?? -BIG, cell: (r) => retCell(r.m5) },
  { key: "nest", title: "区间套", val: (r) => r.nest.agreement, cell: nestCell },
];

// ---- 筛选、排序 ------------------------------------------------------------

function passes(r) {
  const code = $("f_code").value.trim();
  if (code && !r.code.includes(code)) return false;
  if ($("f_fresh").checked && r.last_ts < LATEST) return false;
  const nest = $("f_nest").value;
  if (nest && r.nest.agreement !== nest) return false;

  // 下面这些只看「筛选按」选中的那个级别
  const side = $("f_side").value;
  const kinds = [...document.querySelectorAll(".f_kind:checked")].map((x) => +x.value);
  const ago = $("f_ago").value;
  const filtering = side || kinds.length < 3 || ago !== "";
  if (!filtering) return true;
  const p = pick(r[state.lv]);
  if (!p) return false;
  if (side && (side === "buy") !== p.buy) return false;
  if (!kinds.includes(p.kind)) return false;
  if (ago !== "" && p.ago > +ago) return false;
  return true;
}

function sorted(rows) {
  const col = COLS.find((c) => c.key === state.sort);
  return rows.slice().sort((a, b) => {
    const x = col.val(a), y = col.val(b);
    return (x < y ? -1 : x > y ? 1 : 0) * state.dir || (a.code < b.code ? -1 : 1);
  });
}

// ---- 绘制 ------------------------------------------------------------------

let shown = [];

function renderHead() {
  $("head").innerHTML = COLS.map((c) => {
    const cls = c.key === state.sort ? (state.dir > 0 ? "asc" : "desc") : "";
    return `<th data-k="${c.key}" class="${cls}">${c.title}</th>`;
  }).join("");
  for (const th of $("head").children) {
    th.onclick = () => {
      const k = th.dataset.k;
      state.dir = state.sort === k ? -state.dir : 1;
      state.sort = k;
      render();
    };
  }
}

function render() {
  renderHead();
  shown = sorted(D.rows.filter(passes));
  $("body").innerHTML = shown.map((r, i) => {
    const cls = [r.code === state.sel ? "sel" : "", UNSTABLE.has(r.code) ? "bad" : ""].join(" ");
    return `<tr data-i="${i}" class="${cls}">${COLS.map((c) => c.cell(r)).join("")}</tr>`;
  }).join("");
  $("count").textContent = `${shown.length} / ${D.rows.length} 只`
    + "　·　悬停「最新点」看最近几个点和出处，悬停「区间套」看每一层"
    + "　·　最新K线标红 = 这只的缓存比别的旧";
}

$("body").onclick = (e) => {
  const tr = e.target.closest("tr");
  if (tr) openRow(shown[+tr.dataset.i]);
};

// ---- 打开查看器 -------------------------------------------------------------

function openRow(r) {
  state.sel = r.code;
  for (const tr of $("body").children) tr.classList.toggle("sel", shown[+tr.dataset.i].code === r.code);
  // 定位到筛选级别上那个点所在的 30 分钟；5 分钟的点也一样，查看器会选中它所在那根
  const p = pick(r[state.lv]) || pick(r.m30);
  const at = p ? p.ts : r.last_ts;
  const url = `viewer.html?code=${r.code}&at=${encodeURIComponent(at)}`;
  $("viewer").src = url;
  $("vbar").innerHTML = `${r.code}　定位到 ${at.slice(0, 16)}　<a href="${url}" target="_blank">新窗口打开</a>`
    + (location.protocol === "file:" ? "　<span class='dim'>（直接双击打开的话，没导出过的票要先跑 export_interactive.py；用 serve.py 打开会自动导出）</span>" : "");
}

// ↑/↓ 逐行切换
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || !shown.length) return;
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  let i = shown.findIndex((r) => r.code === state.sel);
  i = Math.max(0, Math.min(shown.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
  openRow(shown[i]);
  $("body").children[i]?.scrollIntoView({ block: "nearest" });
  e.preventDefault();
});

// ---- 初始 ------------------------------------------------------------------

for (const el of document.querySelectorAll("#top input, #top select")) {
  el.addEventListener(el.type === "text" || el.type === "number" ? "input" : "change", () => {
    if (el.name === "lv") state.lv = el.value;
    render();
  });
}

const fails = D.failed.length;
$("meta").textContent = `生成于 ${D.generated.replace("T", " ")} · ${D.source} · 30 分钟最后 ${D.n30} 根`
  + ` · ${D.rows.length} 只${fails ? `，${fails} 只没算出来` : ""} · 最新K线 ${LATEST.slice(0, 16)}`
  + ` · 自检 ${D.verify} 遍${D.health.recomputed ? `，${D.health.recomputed} 只重算过` : ""}`;
if (UNSTABLE.size) {
  $("warn").textContent = `${UNSTABLE.size} 只自检凑不齐多数（标红），这些行不可信`;
}
render();
// ?code=600519 打开时直接选中这一只
const want = new URLSearchParams(location.search).get("code");
const hit = want && D.rows.find((r) => r.code === want); // 被筛掉的也能打开
if (hit) openRow(hit);
