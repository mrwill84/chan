/**
 * 不经过模型，直接把 Pi 扩展的三个工具各跑一遍。
 *
 * 为什么需要这个：`pi -p "分析 600519"` 要本地 27B 推理一轮，GPU 被别的进程
 * 占着时根本排不上队（实测卡在 "inference request expired while waiting for
 * admission"）。但那一轮里真正属于**我们**的部分——拼命令、跑 Python、解析
 * JSON、把结果压成摘要——一点模型都不需要。
 *
 * 所以把这段单独跑：模型排不上队的时候，仍然能确认扩展本身是好的；而扩展
 * 出问题时，也不用先排除「是不是模型没调工具」这个干扰项。
 *
 *     node tools/check_ext.mjs                # 跑装好的那份（~/.pi/agent/...）
 *     node tools/check_ext.mjs pi/extensions/chan/index.ts   # 跑仓库里的
 *
 * 退出码非零 = 有工具没跑通。
 */

import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

/** npm 全局目录下的 pi 包。扩展 import 的那两个模块只在它的 node_modules 里。 */
function piPackage() {
	// execSync 而不是 execFileSync+shell:true：Windows 上 npm 是 .cmd，必须过
	// shell，而那个组合会让 node 打一条 DEP0190 警告，把真正的输出顶走。
	const root = execSync("npm root -g", { encoding: "utf8" }).trim();
	const p = join(root, "@earendil-works", "pi-coding-agent");
	if (!existsSync(p)) throw new Error(`找不到 pi 包：${p}。先 npm i -g @earendil-works/pi-coding-agent`);
	return p;
}

const EXT = resolve(process.argv[2] ?? join(homedir(), ".pi", "agent", "extensions", "chan", "index.ts"));
if (!existsSync(EXT)) throw new Error(`找不到扩展：${EXT}`);

const P = piPackage();
const { createJiti } = await import(pathToFileURL(join(P, "node_modules", "jiti", "lib", "jiti.mjs")).href);

// 扩展是裸 import `@earendil-works/pi-coding-agent` 和 `typebox` 的。它放在
// ~/.pi 下面，那里没有 node_modules，平时由 pi 自己的 resolver 兜着。这里自己
// 把这两个名字指过去——**只指这两个**，多指一个就不是在测真实的加载路径了。
const jiti = createJiti(import.meta.url, {
	alias: {
		"@earendil-works/pi-coding-agent": join(P, "dist", "index.js"),
		// 指到文件而不是目录：typebox 的 package.json 只有 `module`/`exports`，
		// 没有 `main`，jiti 的 alias 走的是 `require.resolve`，解不开目录。
		typebox: join(P, "node_modules", "typebox", "build", "index.mjs"),
	},
	interopDefault: true,
});

const tools = new Map();
const mod = await jiti.import(EXT, { default: true });
mod({ registerTool: (t) => tools.set(t.name, t) });

console.log(`扩展：${EXT}`);
console.log(`注册了 ${tools.size} 个工具：${[...tools.keys()].join(", ")}\n`);

/** 每个工具一组最小参数。要的是「跑得通、摘要长得对」，不是覆盖所有分支。 */
const CASES = [
	["chan_analyze", { code: "600519", levels: ["day"], recent: 3, cached_only: true }],
	["chan_nest", { code: "600519", levels: ["day", "30min"], side: "buy", cached_only: true }],
	["chan_scan", { codes: ["600519", "000002", "601398"], within: 100000, cached_only: true }],
];

let bad = 0;
for (const [name, params] of CASES) {
	const tool = tools.get(name);
	if (!tool) {
		console.log(`✗ ${name}：没注册\n`);
		bad++;
		continue;
	}
	const t0 = Date.now();
	try {
		const r = await tool.execute("check", params, new AbortController().signal);
		const text = r.content?.[0]?.text ?? "";
		// structuredContent 是 codemode 脚本读的那一份。它空了的话，模型那边
		// 一切正常，只有「让它写脚本过滤几千条」会静悄悄地拿不到数据。
		if (!r.structuredContent) throw new Error("没有 structuredContent");
		if (!text.trim()) throw new Error("摘要是空的");
		console.log(`✓ ${name}  ${((Date.now() - t0) / 1000).toFixed(1)}s`);
		console.log(
			text
				.split("\n")
				.slice(0, 14)
				.map((l) => `    ${l}`)
				.join("\n"),
		);
		console.log(`    … 摘要共 ${text.split("\n").length} 行\n`);
	} catch (e) {
		console.log(`✗ ${name}  ${((Date.now() - t0) / 1000).toFixed(1)}s\n    ${e.message}\n`);
		bad++;
	}
}

console.log(bad ? `${bad} 个工具没跑通。` : "三个工具全部跑通。");
process.exit(bad ? 1 : 0);
