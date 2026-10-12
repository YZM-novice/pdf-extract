/**
 * Behaviour tests for the `pdf-extract` pi extension (tickets 06 + 07).
 *
 * The extension imports `typebox` and `@earendil-works/pi-coding-agent`, which
 * only exist inside the pi installation. To test the real extension file
 * standalone we copy it into a temp dir with tiny stub packages next to it, then
 * import it with Node's built-in TypeScript support.
 *
 * The spawned Python interpreter is replaced with a fake script, so no MinerU
 * model is touched.
 *
 * Run directly:  node --test tests/js
 */

import assert from "node:assert/strict";
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, afterEach, before, test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const EXTENSION = join(REPO, "extensions", "mineru-pdf-extract.ts");

const MD_PATH = "/tmp/pdf-extract-test/paper_md/paper.md";
const IMG_PATH = "/tmp/pdf-extract-test/paper_md/images";
const SECRET = "TOP SECRET BODY TEXT";

function writeStubPackages(root) {
	const pkgs = {
		"@earendil-works/pi-coding-agent": `
			export function defineTool(tool) { return tool; }
			export function isToolCallEventType(toolName, event) { return event.toolName === toolName; }
		`,
		typebox: `
			export const Type = {
				Object: (props) => ({
					type: "object",
					properties: props,
					required: Object.entries(props).filter(([, v]) => !v.optional).map(([k]) => k),
				}),
				String: (options = {}) => ({ type: "string", ...options }),
				Optional: (schema) => ({ ...schema, optional: true }),
			};
		`,
	};
	for (const [name, source] of Object.entries(pkgs)) {
		const dir = join(root, "node_modules", name);
		mkdirSync(dir, { recursive: true });
		writeFileSync(join(dir, "package.json"), JSON.stringify({ name, version: "0.0.0", type: "module", main: "index.js" }));
		writeFileSync(join(dir, "index.js"), source);
	}
}

const FAKE_SCRIPT = `#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_ARGS_FILE"
printf '%s' "$TMPDIR" > "$FAKE_TMP_FILE"
case "$FAKE_MODE" in
  ok|lowconf|hold)
    lc=false; [ "$FAKE_MODE" = lowconf ] && lc=true
    [ "$FAKE_MODE" = hold ] && while [ ! -f "$FAKE_RELEASE_FILE" ]; do sleep 0.02; done
    printf '{"results":[{"pdf":"%s","markdown":"%s","images":"%s","tier":"flash","ocr_mode":"txt","routing_reason":"text layer without formula signals -> flash+txt","low_confidence":%s,"stats":{"lines_before":120,"lines_after":40,"lines_removed":80,"chars_before":9000,"chars_after":3000,"chars_removed":6000}}],"errors":[],"any_low_confidence":%s,"exit_code":0}\\n' "$PDF_PATH" "$MD_PATH" "$IMG_PATH" "$lc" "$lc"
    ;;
  fail)
    printf '{"results":[],"errors":[{"message":"mineru exploded","exit_code":2,"tmp_dir":"/tmp/kept-dir"}],"any_low_confidence":false,"exit_code":2}\\n'
    exit 2
    ;;
  *)
    echo "unexpected traceback" >&2
    exit 1
    ;;
esac
`;

let harnessRoot;
let tool;
const handlers = {};

async function waitFor(predicate, timeoutMs = 3000) {
	const start = Date.now();
	while (!predicate()) {
		if (Date.now() - start > timeoutMs) throw new Error("timed out waiting for condition");
		await new Promise((resolve) => setTimeout(resolve, 10));
	}
}

function emit(event, eventObject, ctx) {
	for (const handler of handlers[event] ?? []) {
		const result = handler(eventObject, ctx);
		if (result !== undefined) return result;
	}
	return undefined;
}

function emitToolCall(toolName, input, cwd) {
	return emit("tool_call", { type: "tool_call", toolCallId: "test-call", toolName, input }, { cwd });
}

function setEnv({ mode, argsFile, tmpFile, releaseFile, pdfPath = "/tmp/paper.pdf", mdPath = MD_PATH, imgPath = IMG_PATH }) {
	process.env.PDF_EXTRACT_PYTHON = fakePy;
	process.env.PDF_EXTRACT_SCRIPT = "/irrelevant/pdf_extract.py";
	process.env.FAKE_MODE = mode;
	process.env.FAKE_ARGS_FILE = argsFile;
	process.env.FAKE_TMP_FILE = tmpFile ?? join(harnessRoot, "tmp-unused.txt");
	process.env.FAKE_RELEASE_FILE = releaseFile ?? join(harnessRoot, "release-unused");
	process.env.PDF_PATH = pdfPath;
	process.env.MD_PATH = mdPath;
	process.env.IMG_PATH = imgPath;
}

let fakePy;

before(async () => {
	harnessRoot = mkdtempSync(join(tmpdir(), "pdf-extract-extension-"));
	writeStubPackages(harnessRoot);
	fakePy = join(harnessRoot, "fake-mineru.sh");
	writeFileSync(fakePy, FAKE_SCRIPT);
	chmodSync(fakePy, 0o755);
	copyFileSync(EXTENSION, join(harnessRoot, "mineru-pdf-extract.ts"));

	const factory = (await import(pathToFileURL(join(harnessRoot, "mineru-pdf-extract.ts")).href)).default;
	const registered = [];
	factory({
		registerTool: (registeredTool) => registered.push(registeredTool),
		on: (event, handler) => {
			(handlers[event] ??= []).push(handler);
		},
	});
	tool = registered.find((candidate) => candidate.name === "pdf-extract");
});

afterEach(() => {
	// Mirror the end of an agent run and a session: drop registrations, then
	// delete any retained temp roots so tests never leak /tmp dirs.
	emit("agent_end", { type: "agent_end", messages: [] });
	emit("session_shutdown", { type: "session_shutdown" });
});

after(() => {
	rmSync(harnessRoot, { recursive: true, force: true });
});

test("blocks nothing while idle", () => {
	assert.equal(emitToolCall("read", { path: "/work/paper.pdf" }, "/work"), undefined);
	assert.equal(emitToolCall("bash", { command: "cat /work/paper.pdf" }, "/work"), undefined);
	assert.equal(emitToolCall("grep", { pattern: "x", path: "/work" }, "/work"), undefined);
});

test("registers pdf-extract with a required path and optional tier/ocr_mode/name", () => {
	assert.ok(tool, "pdf-extract tool was registered");
	assert.equal(tool.parameters.properties.pdf_path.type, "string");
	assert.deepEqual(tool.parameters.required, ["pdf_path"]);
	assert.equal(tool.parameters.properties.tier.optional, true);
	assert.equal(tool.parameters.properties.ocr_mode.optional, true);
	assert.equal(tool.parameters.properties.name.optional, true);
});

test("returns only artifact paths and stats, never file contents", async () => {
	setEnv({ mode: "ok", argsFile: join(harnessRoot, "args-ok.txt") });
	const result = await tool.execute("call-1", { pdf_path: "/tmp/paper.pdf" }, undefined);

	assert.ok(result.content[0].text.includes(MD_PATH));
	assert.ok(result.content[0].text.includes(IMG_PATH));
	assert.ok(result.content[0].text.includes("80 lines / 6000 chars"));
	assert.ok(result.content[0].text.includes("Low confidence: no"));
	assert.ok(!result.content[0].text.includes(SECRET), "must not leak file contents");
	assert.equal(result.details.low_confidence, false);
	assert.equal(result.details.stats.lines_removed, 80);
});

test("forwards tier and ocr_mode to the script", async () => {
	const argsFile = join(harnessRoot, "args-flags.txt");
	setEnv({ mode: "ok", argsFile });
	await tool.execute("call-flags", { pdf_path: "/tmp/paper.pdf", tier: "standard", ocr_mode: "ocr" }, undefined);

	const args = readFileSync(argsFile, "utf-8").trim().split("\n");
	assert.deepEqual(args, [
		"/irrelevant/pdf_extract.py",
		"/tmp/paper.pdf",
		"--json",
		"--tier",
		"standard",
		"--ocr-mode",
		"ocr",
	]);
});

test("forwards name to the script", async () => {
	const argsFile = join(harnessRoot, "args-name.txt");
	setEnv({ mode: "ok", argsFile });
	await tool.execute("call-name", { pdf_path: "/tmp/paper.pdf", name: "2" }, undefined);

	const args = readFileSync(argsFile, "utf-8").trim().split("\n");
	assert.deepEqual(args, [
		"/irrelevant/pdf_extract.py",
		"/tmp/paper.pdf",
		"--json",
		"--name",
		"2",
	]);
});

test("resolves the interpreter from the pinned config when no env is set", async () => {
	const argsFile = join(harnessRoot, "args-config.txt");
	const configFile = join(harnessRoot, "pdf-extract.env");
	writeFileSync(configFile, `MINERU_PYTHON=${fakePy}\n`);
	setEnv({ mode: "ok", argsFile });
	delete process.env.PDF_EXTRACT_PYTHON;
	process.env.PDF_EXTRACT_CONFIG = configFile;
	try {
		await tool.execute("call-config", { pdf_path: "/tmp/paper.pdf" }, undefined);
		assert.ok(existsSync(argsFile), "the config interpreter ran");
	} finally {
		delete process.env.PDF_EXTRACT_CONFIG;
	}
});

test("surfaces low confidence without deciding for the user", async () => {
	setEnv({ mode: "lowconf", argsFile: join(harnessRoot, "args-lowconf.txt") });
	const result = await tool.execute("call-lowconf", { pdf_path: "/tmp/paper.pdf" }, undefined);
	assert.equal(result.details.low_confidence, true);
	assert.ok(result.content[0].text.includes("Low confidence: yes"));
});

test("throws with message and kept temp dir on failure", async () => {
	setEnv({ mode: "fail", argsFile: join(harnessRoot, "args-fail.txt") });
	await assert.rejects(tool.execute("call-fail", { pdf_path: "/tmp/paper.pdf" }, undefined), (error) => {
		assert.ok(error.message.includes("mineru exploded"));
		assert.ok(error.message.includes("/tmp/kept-dir"));
		return true;
	});
});

test("throws with stderr when the script crashes without JSON", async () => {
	setEnv({ mode: "crash", argsFile: join(harnessRoot, "args-crash.txt") });
	await assert.rejects(tool.execute("call-crash", { pdf_path: "/tmp/paper.pdf" }, undefined), (error) => {
		assert.ok(error.message.includes("unexpected traceback"));
		return true;
	});
});

test("blocks the source PDF and temp files while a conversion runs", async () => {
	const argsFile = join(harnessRoot, "args-active.txt");
	const tmpFile = join(harnessRoot, "tmp-active.txt");
	const releaseFile = join(harnessRoot, "release-active");
	rmSync(releaseFile, { force: true });
	setEnv({ mode: "hold", argsFile, tmpFile, releaseFile });

	const pending = tool.execute(
		"call-active",
		{ pdf_path: "/work/paper.pdf" },
		undefined,
		undefined,
		{ cwd: "/work" },
	);
	await waitFor(() => existsSync(tmpFile));
	const tempRoot = readFileSync(tmpFile, "utf-8").trim();

	assert.equal(emitToolCall("read", { path: "/work/paper.pdf" }, "/work").block, true);
	assert.equal(emitToolCall("read", { path: "/work/../work/paper.pdf" }, "/work").block, true);
	assert.equal(emitToolCall("read", { path: join(tempRoot, "mineru-pdf-extract-x", "markdown.md") }, "/work").block, true);
	assert.equal(emitToolCall("grep", { pattern: "secret", path: tempRoot }, "/work").block, true);
	assert.equal(emitToolCall("bash", { command: "cat /work/paper.pdf" }, "/work").block, true);
	assert.equal(emitToolCall("bash", { command: "head -c 100 paper.pdf" }, "/work").block, true);

	// The cleaned artifacts are never protected.
	assert.equal(emitToolCall("read", { path: "/work/paper_md/paper.md" }, "/work"), undefined);
	assert.equal(emitToolCall("read", { path: "/work/paper_md/images/fig.jpg" }, "/work"), undefined);

	writeFileSync(releaseFile, "");
	await pending;

	assert.equal(existsSync(tempRoot), false, "temp root removed on success");
	assert.equal(emitToolCall("read", { path: "/work/paper.pdf" }, "/work"), undefined);
});

test("keeps blocking after a failure until agent_end", async () => {
	setEnv({ mode: "fail", argsFile: join(harnessRoot, "args-fail-active.txt") });
	await assert.rejects(
		tool.execute("call-fail-active", { pdf_path: "/work/paper.pdf" }, undefined, undefined, { cwd: "/work" }),
	);
	assert.equal(emitToolCall("read", { path: "/work/paper.pdf" }, "/work").block, true);

	emit("agent_end", { type: "agent_end", messages: [] });
	assert.equal(emitToolCall("read", { path: "/work/paper.pdf" }, "/work"), undefined);
});

test("agent_end clears registrations from every task", async () => {
	const argsFile = join(harnessRoot, "args-multi.txt");
	const tmpFile = join(harnessRoot, "tmp-multi.txt");
	const releaseFile = join(harnessRoot, "release-multi");
	rmSync(releaseFile, { force: true });
	rmSync(tmpFile, { force: true });
	setEnv({ mode: "hold", argsFile, tmpFile, releaseFile });
	const pending = tool.execute("call-multi", { pdf_path: "/work/a.pdf" }, undefined, undefined, { cwd: "/work" });
	await waitFor(() => existsSync(tmpFile));
	assert.equal(emitToolCall("read", { path: "/work/a.pdf" }, "/work").block, true);

	emit("agent_end", { type: "agent_end", messages: [] });
	assert.equal(emitToolCall("read", { path: "/work/a.pdf" }, "/work"), undefined);

	writeFileSync(releaseFile, "");
	await pending;
});
