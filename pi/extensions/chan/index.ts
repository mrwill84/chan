/**
 * 缠论划线工具。把 chan 仓库的 Python 引擎接进 Pi。
 *
 * 为什么是 shell out 而不是用 TypeScript 重写一遍：划线算法有 215 条测试、
 * 一套不变量断言和一个多空镜像自检压着，再实现一份就等于再养一份 bug。
 * 这里只做三件事——拼命令、解析 JSON、把结果压成模型读得完的样子。
 *
 * **`content` 和 `structuredContent` 故意给的是两样东西。** 模型看 `content`，
 * 那是一段人话摘要；codemode 脚本拿 `structuredContent`，那是完整结构。
 * 全市场 5400 只的扫描结果塞不进上下文，但脚本可以 filter 完再交给模型。
 */

import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
	DEFAULT_MAX_BYTES,
	DEFAULT_MAX_LINES,
	defineTool,
	type ExtensionAPI,
	truncateHead,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

/**
 * 引擎仓库位置。优先级：环境变量 → 安装时落在本目录的 `chan-home.json` → 报错。
 *
 * 不把路径写死在源码里：这个文件是**仓库里的**，`tools/install_pi.py` 会把它
 * 拷到 `~/.pi/agent/extensions/`，顺带写一份 `chan-home.json` 指回仓库。
 * 写死的话换台机器或改个目录名，报错会是「python 起不来」这种完全误导的样子。
 */
function resolveHome(): string {
	if (process.env.CHAN_HOME) return process.env.CHAN_HOME;
	try {
		const here = dirname(fileURLToPath(import.meta.url));
		return JSON.parse(readFileSync(join(here, "chan-home.json"), "utf8")).home;
	} catch {
		throw new Error(
			"找不到缠论引擎。设 CHAN_HOME 环境变量，或在仓库里跑 `python -m tools.install_pi` 重装。",
		);
	}
}

const PYTHON = process.env.CHAN_PYTHON ?? "python";

/** 一次划线最多跑这么久。全市场扫描走 `chan_scan` 自己的更长的超时。 */
const TIMEOUT_MS = 120_000;
const SCAN_TIMEOUT_MS = 30 * 60_000;

interface RunResult {
	stdout: string;
	stderr: string;
	code: number | null;
}

/**
 * 跑 `python -m scan ...`，只收 stdout 当 JSON。
 *
 * stderr 单独留着——进度和 akshare 的警告都往那儿走，混进 stdout 会让
 * `JSON.parse` 在一个和真正原因毫无关系的位置炸掉。
 *
 * `PYTHONIOENCODING` 必须显式给：Windows 的默认 stdout 编码是 cp936，
 * 中文理由字段会变成乱码或直接抛 `UnicodeEncodeError`。
 */
function run(args: string[], timeoutMs: number, signal?: AbortSignal): Promise<RunResult> {
	const home = resolveHome();
	return new Promise((resolve, reject) => {
		const child = spawn(PYTHON, ["-m", "scan", ...args], {
			cwd: home,
			env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONPATH: home },
			windowsHide: true,
		});
		let stdout = "";
		let stderr = "";
		child.stdout.setEncoding("utf8");
		child.stderr.setEncoding("utf8");
		child.stdout.on("data", (d) => {
			stdout += d;
		});
		child.stderr.on("data", (d) => {
			stderr += d;
		});

		const timer = setTimeout(() => {
			child.kill();
			reject(new Error(`划线超时（${timeoutMs / 1000}s）。命令：scan ${args.join(" ")}`));
		}, timeoutMs);
		const onAbort = () => {
			child.kill();
			reject(new Error("已取消"));
		};
		signal?.addEventListener("abort", onAbort, { once: true });

		child.on("error", (e) => {
			clearTimeout(timer);
			signal?.removeEventListener("abort", onAbort);
			reject(new Error(`起不来 ${PYTHON}：${e.message}。检查 CHAN_PYTHON 和 CHAN_HOME=${home}`));
		});
		child.on("close", (code) => {
			clearTimeout(timer);
			signal?.removeEventListener("abort", onAbort);
			resolve({ stdout, stderr, code });
		});
	});
}

/** 解析引擎输出。失败时把 stderr 带上——那里才有真正的原因。 */
function parse(r: RunResult, what: string): any {
	if (r.code !== 0) {
		throw new Error(`${what} 失败（退出码 ${r.code}）：\n${r.stderr.trim() || r.stdout.trim()}`);
	}
	try {
		return JSON.parse(r.stdout);
	} catch {
		const head = r.stdout.slice(0, 400);
		throw new Error(`${what} 的输出不是 JSON。前 400 字：\n${head}\n\nstderr：\n${r.stderr.trim()}`);
	}
}

/** 结果过大时落盘，告诉模型去哪读完整的。 */
async function spill(text: string, tag: string): Promise<{ text: string; path?: string }> {
	const t = truncateHead(text, { maxLines: DEFAULT_MAX_LINES, maxBytes: DEFAULT_MAX_BYTES });
	if (!t.truncated) return { text };
	const dir = await mkdtemp(join(tmpdir(), `pi-chan-${tag}-`));
	const file = join(dir, "full.txt");
	await writeFile(file, text, "utf8");
	return {
		text: `${t.content}\n\n[已截断：${t.outputLines}/${t.totalLines} 行。完整结果在 ${file}]`,
		path: file,
	};
}

// --------------------------------------------------------------------------
// 摘要。模型看到的就是这些字，所以密度要够、术语要用原文的
// --------------------------------------------------------------------------

const LEVEL_CN: Record<string, string> = { day: "日线", "30min": "30分钟", "5min": "5分钟" };
// 键必须和 `chan/zhongshu.py` 的 TREND_UP/TREND_DOWN/PANZHENG 逐字对上。
// 这里曾经写的是 `trend_up`/`trend_down`，引擎从来没发过这两个值——没人发现，
// 是因为那时 `trend` 恒为 `panzheng`（见 C-trendnow）。查不到就原样印代号。
const TREND_CN: Record<string, string> = {
	up: "上涨趋势",
	down: "下跌趋势",
	panzheng: "盘整",
	// `no_zhongshu` 是引擎发的（`chan/tree.py` 的 NO_ZHONGSHU 在 JSON 出口
	// 归一过）：这一段本级别连三段重叠都凑不出，要降一个级别看。
	// `none` 是另一回事——`multilevel.analyze_level` 给**算不出来的那一层**
	// 的哨兵值。两者别合并。
	no_zhongshu: "本级别无中枢（该降级别看）",
	none: "无中枢",
};

// 和 `scan/scanner.py` 的 `MISS_REASONS` 一一对应。那边加了新的原因这里没跟上，
// 下面会原样印出内部 key——难看，但不会把原因吞掉。
const MISS_CN: Record<string, string> = {
	stale: "有合格的买卖点，只是比 within 要求的旧（调大 within 就能看到）",
	quasi: "最近的点全是类买卖点，被默认条件滤掉了（传 include_quasi: true 看）",
	confirmed: "最近的点都落在未完成的笔/段上，被 confirmed_only 滤掉了",
	kind: "有买卖点，但不是 kinds 要的那几类",
	side: "有买卖点，但不是 sides 要的方向",
	none: "这只票上一个买卖点都没划出来",
};

function fmtZs(z: any): string {
	const flags = [
		z.confirmed ? "已确认" : "未确认",
		z.departed ? "已离开" : "未离开",
		z.level_up ? "⚠已到9段上限，该升级别看" : "",
	].filter(Boolean);
	return (
		`  ZS${z.i} ${z.dir === "up" ? "↑" : "↓"}  ZD ${z.zd} ZG ${z.zg}` +
		`  │ DD ${z.dd} GG ${z.gg}  │ ${z.start_ts}~${z.end_ts} ${z.legs}段 ${flags.join(" ")}`
	);
}

function fmtBs(p: any, withReasons: boolean): string {
	const flags = [
		p.confirmed ? "" : "未确认",
		p.quasi ? "类买卖点(盘整背驰)" : "",
		p.ambiguous ? "原文有歧义" : "",
		p.macd_law_ok === false ? "不合MACD定律" : "",
	].filter(Boolean);
	const head = `  ${p.ts}  ${p.label}  ${p.price}  @ZS${p.zs}${flags.length ? "  [" + flags.join(" / ") + "]" : ""}`;
	if (!withReasons || !p.reasons?.length) return head;
	return head + "\n" + p.reasons.map((r: string) => `      · ${r}`).join("\n");
}

// 中枢之间的关系（`chan/zhongshu.py::relation`）。20课 p43 中心定理二：
// 无重叠是趋势，有重叠是升级——「再来一个中枢」在两种情况下含义完全不同。
const REL_CN: Record<string, string> = {
	up: "依次升高（上涨趋势）",
	down: "依次降低（下跌趋势）",
	higher_level: "有重叠（升级为更大级别中枢）",
};
const LAYER_CN = ["本级别", "上一级", "上两级", "上三级", "上四级"];

/** 一支推演。顺序由调用方定，这里只管渲染。
 *
 * **标记故意不分主次。** 从前 `in_progress` 的那一支印 `▶`、其余印 `○`，
 * 还被排到第一位——两个记号在终端里读起来就是「这支最可能」。它不是：
 * `in_progress` 只回答「现价在中枢的哪一侧」，是**位置读数**，不是概率。
 * 实测 30 分钟级别它和最终走成的走势类型吻合率 ≈ 50%（600009 38.6%/n=176，
 * 600519 52.0%/n=177，两者无统计差别，也都和掷硬币无差别）。所以这里一律
 * 用同一个 `·`，位置信息放进方括号里当事实陈述。
 */
function fmtScenario(s: any, n: number): string {
	const flags = [
		s.in_progress ? "现价位置落在这一支（位置读数，不是概率）" : "",
		s.evidence_now ? "力度证据已具备" : "",
	].filter(Boolean);
	const L = [
		`  · option${n} ${s.name}` + (flags.length ? `　[${flags.join(" / ")}]` : ""),
	];
	if (s.trigger) L.push(`       触发：${s.trigger.what}（${s.trigger.op} ${s.trigger.price}）`);
	L.push(`       走成：${s.becomes}` + (s.forms ? `，形成 ${s.forms}` : ""));
	if (s.kills) L.push(`       作废：${s.kills.what}（${s.kills.op} ${s.kills.price}）`);
	for (const need of s.needs ?? []) L.push(`       · ${need}`);
	return L.join("\n");
}

/** 推演表：当前走势类型 + 完备的后续选项。
 *
 * 这一块在 `structuredContent` 里躺了很久没人印——于是不写 codemode 的调用
 * 看不到任何「下面可能怎么走」，而那正是这个工具唯一有操作价值的输出。
 */
function fmtForward(f: any, head: string): string[] {
	if (!f) return [];
	if (!f.scenarios?.length) return f.note ? [`${head}推演：${f.note}`] : [];
	// **保持引擎给的顺序，不做任何提前。** 从前这里把 `in_progress` 那一支
	// 排到第一位，理由是「它在走」。但列表的第一行在终端里就是「最可能」，
	// 而 `in_progress` 是位置读数不是概率（见 `fmtScenario` 的文档串）。
	// 引擎不出概率，排序和权重是模型的活（见 chan/forward.py）。
	const L = [
		`${head}推演：${TREND_CN[f.trend] ?? f.trend}，含 ${f.run} 个中枢，` +
			`现价 ${f.last_close} 在${f.where}` +
			(f.relation ? `；最后两枢 ${REL_CN[f.relation] ?? f.relation}` : ""),
	];
	if (f.scenarios.some((s: any) => s.in_progress)) {
		L.push("       （下面各支**不分先后、不带概率**；标了「现价位置落在这一支」的那支只是说现价在中枢的那一侧，实测与最终走成的类型吻合率 ≈ 50%）");
	}
	f.scenarios.forEach((s: any, i: number) => L.push(fmtScenario(s, i + 1)));
	return L;
}

/** 级别树一览。每层一行，后面各层的推演表另起。 */
function fmtTree(tree: any[], alts: any[]): string[] {
	if (!tree?.length) return [];
	const L = ["级别树（同一串K线自己递归上去的，和 30分/5分那套级别不是一回事）："];
	for (const t of tree) {
		const name = LAYER_CN[t.level] ?? `上${t.level}级`;
		const c = t.current;
		if (!c) {
			L.push(`  ${name}  没划出走势类型`);
			continue;
		}
		L.push(
			`  ${name}  ${TREND_CN[c.kind] ?? c.kind}(${c.run}枢)  ${c.start_ts}~${c.end_ts}` +
				`  ${c.complete ? "已完成" : "未完成，末枢还没走出去"}` +
				(c.zd == null ? "" : `　末枢 ZD ${c.zd} ZG ${c.zg}`),
		);
		L.push(
			`        本层共 ${t.trend_count} 段走势 / ${t.zhongshu_count} 个中枢` +
				// 拆出来的子中枢（33课 p79 三等分）。不说明的话，这里的中枢数
				// 比上面那张平表多，看着像算重了。
				(t.split_count ? `（其中 ${t.split_count} 个是 9 段以上的中枢三等分拆出来的，33课 p79）` : ""),
		);
	}
	// 走势类型分组的多义性（33课 p79）。只在**当下所在的那一段**读法不同时
	// 才会有内容——「有没有备选切法」几乎恒为真，印出来等于噪音。
	if (alts?.length) {
		L.push(
			`  ⚠ 当下这一段走势类型还有 ${alts.length} 种合法读法（33课 p79 走势多义性）。` +
				"全部摆给用户，不要替原文选一种：",
		);
		for (const a of alts) {
			L.push(
				`     · ${TREND_CN[a.trend] ?? a.trend}（${a.run}枢，中枢 ${a.zhongshu_range?.[0]}..${a.zhongshu_range?.[1]}）` +
					`  ${a.start_ts}~${a.end_ts}`,
			);
			for (const n of a.notes ?? []) L.push(`       ${n}`);
		}
	}
	return L;
}

/** 一个级别的报告 → 一段人话。 */
function digestLevel(r: any): string {
	if (r.error) return `${LEVEL_CN[r.level] ?? r.level}：${r.error}`;
	const L: string[] = [];
	L.push(
		`【${LEVEL_CN[r.level] ?? r.level}】${r.bars.count}根 ${r.bars.first_ts}~${r.bars.last_ts}` +
			`  收 ${r.bars.last_close}  中枢建在${r.zs_from === "seg" ? "线段" : "笔"}上`,
	);
	L.push(
		`走势：${TREND_CN[r.trend] ?? r.trend}　笔 ${r.bi.count}　` +
			`线段 ${r.seg.count}(确认 ${r.seg.confirmed})　中枢 ${r.zhongshu_count}　买卖点 ${r.bspoint_count}`,
	);
	if (r.zhongshus.length) {
		L.push(`中枢（最近 ${r.zhongshus.length} 个${r.truncated ? "，共 " + r.zhongshu_count : ""}）：`);
		L.push(...r.zhongshus.map(fmtZs));
	}
	if (r.bspoints.length) {
		L.push(`买卖点（最近 ${r.bspoints.length} 个${r.truncated ? "，共 " + r.bspoint_count : ""}）：`);
		L.push(...r.bspoints.map((p: any) => fmtBs(p, true)));
	} else {
		L.push("买卖点：无");
	}
	L.push(...fmtTree(r.tree, r.alt_readings));
	// 每一层各一份推演。`tree[0].forward` 和顶层 `r.forward` 是同一次计算，
	// 所以有树的时候不要再单独印一遍顶层那份。
	if (r.tree?.length) {
		for (const t of r.tree) L.push(...fmtForward(t.forward, LAYER_CN[t.level] ?? `上${t.level}级`));
	} else {
		L.push(...fmtForward(r.forward, ""));
	}
	for (const w of Object.values(r.warnings ?? {})) L.push(`⚠ ${w}`);
	return L.join("\n");
}

// --------------------------------------------------------------------------
// outputSchema。声明了它，codemode 脚本拿到的才是结构而不是上面那段人话
// --------------------------------------------------------------------------

const ZsSchema = Type.Object(
	{
		i: Type.Number(),
		dir: Type.String(),
		zg: Type.Number({ description: "中枢上沿，三卖的基准" }),
		zd: Type.Number({ description: "中枢下沿，三买的基准" }),
		gg: Type.Number({ description: "震荡包络上沿，趋势判定用" }),
		dd: Type.Number({ description: "震荡包络下沿，趋势判定用" }),
		start_ts: Type.String(),
		end_ts: Type.String(),
		legs: Type.Number(),
		confirmed: Type.Boolean(),
		departed: Type.Boolean(),
		level_up: Type.Boolean({ description: "延伸到9段上限，该升一个级别看" }),
	},
	{ additionalProperties: true },
);

const BsSchema = Type.Object(
	{
		kind: Type.Number({ description: "1 / 2 / 3 类" }),
		side: Type.String({ description: "buy | sell" }),
		label: Type.String({ description: "1买 / 3卖 这样的中文短标签" }),
		ts: Type.String(),
		bar: Type.Number(),
		price: Type.Number(),
		zs: Type.Number({ description: "锚定的中枢序号" }),
		confirmed: Type.Boolean({ description: "false = 落在未完成笔/段上，是线索不是信号" }),
		quasi: Type.Boolean({ description: "类买卖点，由盘整背驰构成" }),
		ambiguous: Type.Boolean({ description: "落在原文自相矛盾的形态上" }),
		macd_law_ok: Type.Union([Type.Boolean(), Type.Null()]),
		source: Type.String(),
		reasons: Type.Array(Type.String()),
	},
	{ additionalProperties: true },
);

const LevelReport = Type.Object(
	{
		code: Type.String(),
		level: Type.String(),
		zs_from: Type.String(),
		error: Type.Optional(Type.String()),
		trend: Type.Optional(Type.String()),
		zhongshu_count: Type.Optional(Type.Number()),
		bspoint_count: Type.Optional(Type.Number()),
		zhongshus: Type.Optional(Type.Array(ZsSchema)),
		bspoints: Type.Optional(Type.Array(BsSchema)),
		latest: Type.Optional(Type.Union([BsSchema, Type.Null()])),
		warnings: Type.Optional(Type.Record(Type.String(), Type.String())),
		// 下面三块是 `digestLevel` 真的要读的东西，所以显式声明（其余字段靠
		// additionalProperties 透传就够了）。`forward` 的形状在
		// `chan/forward.py::project` 里，`tree` 在 `scan/report.py::_layer_json`。
		forward: Type.Optional(Type.Object({}, { additionalProperties: true })),
		tree: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
		alt_readings: Type.Optional(
			Type.Array(Type.Object({}, { additionalProperties: true })),
		),
	},
	{ additionalProperties: true },
);

// --------------------------------------------------------------------------
// chan_analyze
// --------------------------------------------------------------------------

const analyzeTool = defineTool({
	name: "chan_analyze",
	label: "缠论划线",
	description:
		"对单只A股做缠论划线，返回笔/线段/中枢/123类买卖点。划线是确定性几何算法，" +
		"由 Python 引擎完成——不要自己数K线画笔。可同时跑日线、30分钟、5分钟三个级别。",
	promptSnippet: "用缠论引擎给一只股票划线",
	promptGuidelines: [
		"要判断买卖点、看结构、找背驰，先调 chan_analyze，不要凭K线自己推。",
		"结果里 confirmed=false 的点落在未完成的笔或线段上，后面的K线可能改掉它，不是已确认信号。",
		"三买比 ZG、三卖比 ZD；趋势关系看 GG/DD。这两对不要混。",
	],
	parameters: Type.Object({
		code: Type.String({ description: "股票代码，600519 / sh600519 / 600519.SH 都认" }),
		levels: Type.Optional(
			Type.Array(Type.String(), {
				description: "级别，day / 30min / 5min。默认只跑 day",
			}),
		),
		zs_from: Type.Optional(
			Type.String({ description: "中枢建在哪层：seg（线段，默认）或 bi（笔，看小结构时用）" }),
		),
		recent: Type.Optional(
			Type.Number({ description: "只回最近 N 个中枢和买卖点，默认 5。给 0 表示全要" }),
		),
		include_quasi: Type.Optional(
			Type.Boolean({ description: "是否包含类买卖点（盘整背驰构成），默认包含" }),
		),
		cached_only: Type.Optional(Type.Boolean({ description: "只读本地缓存，不发网络请求" })),
	}),
	outputSchema: Type.Object({
		code: Type.String(),
		levels: Type.Record(Type.String(), LevelReport),
	}),

	async execute(_id, params, signal) {
		const args = ["analyze", params.code];
		args.push("--levels", (params.levels ?? ["day"]).join(","));
		if (params.zs_from) args.push("--zs", params.zs_from);
		args.push("--recent", String(params.recent ?? 5));
		if (params.include_quasi === false) args.push("--no-quasi");
		if (params.cached_only) args.push("--cached-only");

		const data = parse(await run(args, TIMEOUT_MS, signal), "chan_analyze");
		const body = Object.values(data.levels).map(digestLevel).join("\n\n");
		const { text } = await spill(
			`${data.code} 缠论划线（价位是 ${data.source} 的后复权，跨源不可比）\n\n${body}`,
			"analyze",
		);
		return {
			content: [{ type: "text", text }],
			structuredContent: data,
			details: { code: data.code, levels: Object.keys(data.levels) },
		};
	},
});

// --------------------------------------------------------------------------
// chan_scan
// --------------------------------------------------------------------------

const HitSchema = Type.Object(
	{
		code: Type.String(),
		trend: Type.String(),
		last_close: Type.Number(),
		last_ts: Type.String(),
		bars_ago: Type.Number({ description: "最后一个买卖点距今多少根K线" }),
		point: Type.Object({}, { additionalProperties: true }),
		warnings: Type.Record(Type.String(), Type.String()),
	},
	{ additionalProperties: true },
);

function digestScan(d: any, shown: number): string {
	const L: string[] = [];
	L.push(
		`扫了 ${d.scanned} 只，命中 ${d.hit_count}，失败 ${d.failed_count}` +
			`（${LEVEL_CN[d.level] ?? d.level}，中枢建在${d.zs_from === "seg" ? "线段" : "笔"}上）`,
	);
	const f = d.filter;
	L.push(
		`条件：最后一个买卖点在 ${f.within} 根K线以内` +
			`${f.kinds ? "，只要 " + f.kinds.join("/") + " 类" : ""}` +
			`${f.sides ? "，只要 " + f.sides.join("/") : ""}` +
			`${f.include_quasi ? "，含类买卖点" : "，不含类买卖点"}` +
			`${f.confirmed_only ? "，只要已确认的" : ""}`,
	);
	if (!d.hits.length) {
		// 「没有命中」和「这些票上没有买卖点」是两件事。实测遇到过：391 只扫出
		// 0 命中，其中 97 只在 5 根K线内都有点，只是全是 quasi，被默认条件滤掉。
		// 只说「放宽 within 再试」会把人引向错的那个旋钮。
		L.push("没有命中。**这不等于这些票上没有买卖点**——看看是被哪道筛子挡的：");
		const why = d.missed_because ?? {};
		for (const [k, n] of Object.entries(why)) {
			L.push(`  ${n} 只：${MISS_CN[k] ?? k}`);
		}
		if (!Object.keys(why).length) {
			L.push("  （这一轮没有「算过但没中」的记录，多半是全都失败了）");
		}
	} else {
		L.push("");
		L.push("代码      走势      现价        信号日期      类型  价位        距今  标记");
		for (const h of d.hits.slice(0, shown)) {
			const p = h.point;
			const flags = [
				p.confirmed ? "" : "未确认",
				p.quasi ? "类" : "",
				p.ambiguous ? "歧义" : "",
				h.warnings.invariant ? "⚠划线异常" : "",
				h.warnings.level_up ? "该升级别" : "",
			].filter(Boolean);
			L.push(
				`${h.code.padEnd(9)} ${(TREND_CN[h.trend] ?? h.trend).padEnd(8)} ` +
					`${String(h.last_close).padEnd(11)} ${p.ts}  ${p.label}  ` +
					`${String(p.price).padEnd(11)} ${String(h.bars_ago).padStart(4)}  ${flags.join(" ")}`,
			);
		}
		if (d.hits.length > shown) {
			L.push(`…… 还有 ${d.hits.length - shown} 只。要全部就用 codemode 读 structuredContent.hits。`);
		}
	}
	if (d.failed_count) {
		L.push("");
		L.push(
			`失败 ${d.failed_count} 只（多半是缓存没有或K线太短）：` +
				d.failed.slice(0, 10).map((x: any) => x.code).join(" ") +
				(d.failed_count > 10 ? " …" : ""),
		);
	}
	L.push("");
	L.push("想看某只的完整结构和判定理由，对它单独调 chan_analyze。");
	return L.join("\n");
}

const scanTool = defineTool({
	name: "chan_scan",
	label: "缠论批量扫描",
	description:
		"批量跑缠论划线，只回最近出过123类买卖点的股票。摘要里每只一行；" +
		"完整清单在 structuredContent.hits，用 codemode 脚本过滤比让我逐条读快得多。" +
		"默认只读本地缓存（不联网），所以要先把K线拉下来。",
	promptSnippet: "批量扫描股票池里最近出现买卖点的票",
	promptGuidelines: [
		"扫描结果多于几十条时，用 codemode 读 structuredContent.hits 自己过滤，不要让我逐条念。",
		"扫出来的只是候选。要下判断得对单只再调 chan_analyze 看完整结构。",
	],
	parameters: Type.Object({
		codes: Type.Optional(Type.Array(Type.String(), { description: "股票代码列表" })),
		file: Type.Optional(Type.String({ description: "每行一个代码的文本文件路径" })),
		level: Type.Optional(Type.String({ description: "day（默认）/ 30min / 5min" })),
		zs_from: Type.Optional(Type.String({ description: "seg（默认）或 bi" })),
		within: Type.Optional(
			Type.Number({ description: "最后一个买卖点距今不超过几根K线，默认 5（日线上约一周）" }),
		),
		kinds: Type.Optional(Type.Array(Type.Number(), { description: "只要哪几类，如 [1,3]" })),
		sides: Type.Optional(Type.Array(Type.String(), { description: "buy / sell" })),
		include_quasi: Type.Optional(
			Type.Boolean({ description: "把类买卖点也算进来，默认不算（全市场扫它是噪音）" }),
		),
		confirmed_only: Type.Optional(Type.Boolean({ description: "丢掉落在未完成笔/段上的点" })),
		limit: Type.Optional(Type.Number({ description: "最多回几条" })),
		fetch: Type.Optional(
			Type.Boolean({ description: "缓存缺失时现拉。几千只别开，会跑几小时" }),
		),
	}),
	outputSchema: Type.Object(
		{
			level: Type.String(),
			scanned: Type.Number(),
			hit_count: Type.Number(),
			failed_count: Type.Number(),
			// 没中的票按原因分桶。`hit_count: 0` 时这是唯一能分辨
			// 「没有买卖点」和「被筛条件挡了」的东西。
			missed_because: Type.Optional(
				Type.Object({}, { additionalProperties: Type.Number() }),
			),
			filter: Type.Object({}, { additionalProperties: true }),
			hits: Type.Array(HitSchema),
			failed: Type.Array(Type.Object({}, { additionalProperties: true })),
		},
		{ additionalProperties: true },
	),

	async execute(_id, params, signal) {
		if (!params.codes?.length && !params.file) {
			throw new Error("要么给 codes，要么给 file。扫描不会自己猜股票池。");
		}
		const args = ["scan", ...(params.codes ?? [])];
		if (params.file) args.push("--file", params.file);
		if (params.level) args.push("--level", params.level);
		if (params.zs_from) args.push("--zs", params.zs_from);
		if (params.within !== undefined) args.push("--within", String(params.within));
		if (params.kinds?.length) args.push("--kinds", params.kinds.join(","));
		if (params.sides?.length) args.push("--sides", params.sides.join(","));
		if (params.include_quasi) args.push("--quasi");
		if (params.confirmed_only) args.push("--confirmed-only");
		if (params.limit !== undefined) args.push("--limit", String(params.limit));
		if (params.fetch) args.push("--fetch");

		const data = parse(await run(args, SCAN_TIMEOUT_MS, signal), "chan_scan");
		// 摘要里最多列 40 行。再多模型也读不出名堂，让它走 codemode。
		const { text } = await spill(digestScan(data, 40), "scan");
		return {
			content: [{ type: "text", text }],
			structuredContent: data,
			details: { scanned: data.scanned, hits: data.hit_count, failed: data.failed_count },
		};
	},
});

// --------------------------------------------------------------------------
// chan_nest —— 区间套（27课 p67）
// --------------------------------------------------------------------------

const COVER_CN: Record<string, string> = {
	ok: "数据完整",
	partial: "窗口左段数据缺失",
	none: "窗口内没有K线",
};
const AGREE_CN: Record<string, string> = {
	agree: "各级别方向一致",
	conflict: "次级别有反向信号",
	incomplete: "没能收缩到最小级别",
};

function digestNest(d: any): string {
	const L: string[] = [];
	L.push(
		`${d.code} 区间套（27课 p67）：${AGREE_CN[d.agreement] ?? d.agreement}` +
			`　价位是 ${d.source} 的后复权`,
	);
	L.push("");
	for (const [name, v] of Object.entries<any>(d.levels)) {
		L.push(
			v.error
				? `  ${LEVEL_CN[name] ?? name}：${v.error}`
				: `  ${LEVEL_CN[name] ?? name}：${v.bars}根 ${v.first_ts}~${v.last_ts}` +
					`  ${TREND_CN[v.trend] ?? v.trend}  中枢 ${v.zhongshu_count}  买卖点 ${v.bspoint_count}`,
		);
	}
	L.push("");
	L.push("收缩过程（大 → 小，大级别定方向，小级别只定时机）：");
	for (const s of d.steps) {
		const head = `  [${LEVEL_CN[s.level] ?? s.level}] 窗口 ${s.window[0]} ~ ${s.window[1]}  ${COVER_CN[s.coverage] ?? s.coverage}`;
		L.push(head);
		if (s.point) L.push(`      → ${s.point.ts}  ${s.point.label}  ${s.point.price}`);
		if (s.against?.length) {
			L.push(`      ⚠ 窗口内另有 ${s.against.length} 个反向点：` +
				s.against.map((p: any) => `${p.ts} ${p.label}`).join("、"));
		}
		L.push(`      ${s.note}`);
	}
	for (const n of d.notes) L.push(`⚠ ${n}`);
	L.push("");
	L.push(
		d.precise
			? `精确转折点落在${LEVEL_CN[d.precise.level] ?? d.precise.level}的 ${d.precise.ts}。`
			: "没有可用的转折点。",
	);
	if (d.agreement !== "agree") {
		L.push(
			"注意：**没有收缩完整**。次级别没数据和次级别没买卖点是两回事，" +
				"看上面每一层的 coverage，不要当成「这段时间很安全」。",
		);
	}
	return L.join("\n");
}

const nestTool = defineTool({
	name: "chan_nest",
	label: "缠论区间套",
	description:
		"三级别联立，用区间套把大级别的买卖点收缩到精确的转折时点（27课 p67）。" +
		"每个级别都在自己的完整历史上划线，区间套只负责挑点——不是把K线切段重划。" +
		"方向以大级别为准，小级别只决定时机。",
	promptSnippet: "用区间套把买卖点收缩到精确时点",
	promptGuidelines: [
		"用户问「什么时候买」「还能不能追」这类**时机**问题时用 chan_nest；只问结构用 chan_analyze。",
		"agreement 不是 agree 时，不要给确定的时点。先看每一层的 coverage——30分钟/5分钟常常根本没有那段历史（C-min）。",
		"次级别出现反向点时以大级别为准，但必须把反向点一并告诉用户，不要只报对自己结论有利的那个。",
	],
	parameters: Type.Object({
		code: Type.String({ description: "股票代码" }),
		levels: Type.Optional(
			Type.Array(Type.String(), {
				description: "**从大到小**排，默认 ['day','30min','5min']",
			}),
		),
		zs_from: Type.Optional(Type.String({ description: "seg（默认）或 bi" })),
		side: Type.Optional(Type.String({ description: "buy / sell，只从买点或卖点出发" })),
		kinds: Type.Optional(Type.Array(Type.Number(), { description: "出发点只要某几类" })),
		cached_only: Type.Optional(Type.Boolean()),
	}),
	outputSchema: Type.Object(
		{
			code: Type.String(),
			agreement: Type.String({ description: "agree | conflict | incomplete" }),
			levels: Type.Record(Type.String(), Type.Object({}, { additionalProperties: true })),
			steps: Type.Array(Type.Object({}, { additionalProperties: true })),
			precise: Type.Union([Type.Object({}, { additionalProperties: true }), Type.Null()]),
			notes: Type.Array(Type.String()),
		},
		{ additionalProperties: true },
	),

	async execute(_id, params, signal) {
		const args = ["nest", params.code];
		args.push("--levels", (params.levels ?? ["day", "30min", "5min"]).join(","));
		if (params.zs_from) args.push("--zs", params.zs_from);
		if (params.side) args.push("--side", params.side);
		if (params.kinds?.length) args.push("--kinds", params.kinds.join(","));
		if (params.cached_only) args.push("--cached-only");

		const data = parse(await run(args, TIMEOUT_MS, signal), "chan_nest");
		const { text } = await spill(digestNest(data), "nest");
		return {
			content: [{ type: "text", text }],
			structuredContent: data,
			details: { code: data.code, agreement: data.agreement },
		};
	},
});

export default function (pi: ExtensionAPI) {
	pi.registerTool(analyzeTool);
	pi.registerTool(scanTool);
	pi.registerTool(nestTool);
}
