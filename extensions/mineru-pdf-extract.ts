/**
 * pdf-extract — run the local MinerU conversion pipeline as a model-callable tool,
 * and fence off the inputs/intermediates while it runs.
 *
 * Tool surface: the tool spawns the deterministic Python script, which does the
 * MinerU parse, the markdown cleaning and the temp-directory lifecycle. The tool
 * only returns artifact paths and trim stats — never file contents.
 *
 * Read interception: a small state machine (`idle -> active(task) -> idle`) is
 * armed when the tool starts and disarmed when it succeeds. While armed, reads
 * of the source PDF and the MinerU temp tree are blocked. It is *never* on for a
 * whole session: it only covers one conversion, and `agent_end` clears every
 * registration unconditionally.
 *
 * Installed by copying this file into `~/.pi/agent/extensions/` (see install.sh);
 * the script it calls is the copy under `~/.agents/skills/pdf-extract/scripts/`.
 */

import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, isAbsolute, join, relative, resolve, sep } from "node:path";
import { Type } from "typebox";
import {
	defineTool,
	isToolCallEventType,
	type ExtensionAPI,
	type ExtensionContext,
	type ToolCallEvent,
} from "@earendil-works/pi-coding-agent";

const DEFAULT_SKILL_DIR = join(homedir(), ".agents", "skills", "pdf-extract");
const DEFAULT_SCRIPT = join(DEFAULT_SKILL_DIR, "scripts", "pdf_extract.py");

/**
 * Pinned install config written by install.sh. It lets an installed copy run
 * with no environment set; nothing here is hard-coded to a machine path.
 */
const configPath = () =>
	process.env.PDF_EXTRACT_CONFIG || join(DEFAULT_SKILL_DIR, "pdf-extract.env");

function configValue(key: string): string | undefined {
	try {
		for (const line of readFileSync(configPath(), "utf-8").split("\n")) {
			const trimmed = line.trim();
			if (!trimmed || trimmed.startsWith("#")) continue;
			const eq = trimmed.indexOf("=");
			if (eq === -1) continue;
			if (trimmed.slice(0, eq).trim() === key) return trimmed.slice(eq + 1).trim();
		}
	} catch {
		// No pinned config (source checkout): fall through to env / PATH.
	}
	return undefined;
}

/** Overridable so tests/dev can point at another interpreter or script copy. */
const pythonPath = () =>
	process.env.PDF_EXTRACT_PYTHON || configValue("MINERU_PYTHON") || "python3";
const scriptPath = () => process.env.PDF_EXTRACT_SCRIPT || DEFAULT_SCRIPT;

export interface PdfExtractStats {
	lines_before: number;
	lines_after: number;
	lines_removed: number;
	chars_before: number;
	chars_after: number;
	chars_removed: number;
}

export interface PdfExtractDetails {
	pdf: string;
	markdown: string;
	images: string;
	tier: string;
	ocr_mode: string;
	routing_reason: string;
	low_confidence: boolean;
	stats: PdfExtractStats;
}

interface JsonError {
	message?: string;
	exit_code?: number;
	tmp_dir?: string | null;
}

interface JsonPayload {
	results?: Partial<PdfExtractDetails>[];
	errors?: JsonError[];
	any_low_confidence?: boolean;
	exit_code?: number;
}

interface RunResult {
	code: number;
	stdout: string;
	stderr: string;
}

interface ToolBlock {
	block: true;
	reason: string;
}

// --------------------------------------------------------------------------- //
// Read-interception state machine
// --------------------------------------------------------------------------- //

/** One in-flight conversion: its source PDF(s) and the temp tree it owns. */
interface ActiveTask {
	pdfPaths: string[];
	protectedPaths: string[];
	tempRoot: string;
}

const activeTasks = new Set<ActiveTask>();
/** Temp roots kept after a failure for troubleshooting; cleaned at session end. */
const retainedTempRoots = new Set<string>();

const BLOCK_REASON =
	"pdf-extract is converting this PDF: the source PDF and MinerU temp files are " +
	"off-limits for now. Only the cleaned <name>.md and its images/ may be read.";

function pathIsInside(candidate: string, root: string): boolean {
	const normalized = root.endsWith(sep) ? root.slice(0, -1) : root;
	return candidate === normalized || candidate.startsWith(normalized + sep);
}

function removeDir(dir: string): void {
	try {
		rmSync(dir, { recursive: true, force: true });
	} catch {
		// Best-effort: a failure here must not fail the conversion or the cleanup.
	}
}

function registerTask(task: ActiveTask): void {
	activeTasks.add(task);
	retainedTempRoots.add(task.tempRoot);
}

function completeTask(task: ActiveTask): void {
	activeTasks.delete(task);
	retainedTempRoots.delete(task.tempRoot);
	removeDir(task.tempRoot);
}

/** agent_end: stop blocking, but keep failed temp roots on disk for troubleshooting. */
function clearRegistrations(): void {
	activeTasks.clear();
}

/** Session end: drop registrations and remove every temp root we still track. */
function cleanupRetainedTempRoots(): void {
	for (const dir of retainedTempRoots) removeDir(dir);
	retainedTempRoots.clear();
	activeTasks.clear();
}

function blockOnPath(candidate: string, cwd: string): ToolBlock | undefined {
	if (activeTasks.size === 0) return undefined;
	const absolute = resolve(cwd, candidate);
	for (const task of activeTasks) {
		for (const protectedPath of task.protectedPaths) {
			if (pathIsInside(absolute, protectedPath)) {
				return { block: true, reason: BLOCK_REASON };
			}
		}
	}
	return undefined;
}

function blockOnCommand(command: string, cwd: string): ToolBlock | undefined {
	if (activeTasks.size === 0) return undefined;
	for (const task of activeTasks) {
		for (const protectedPath of task.protectedPaths) {
			if (command.includes(protectedPath)) {
				return { block: true, reason: BLOCK_REASON };
			}
			const rel = relative(cwd, protectedPath);
			if (rel && !rel.startsWith("..") && !isAbsolute(rel) && command.includes(rel)) {
				return { block: true, reason: BLOCK_REASON };
			}
		}
		for (const pdf of task.pdfPaths) {
			if (command.includes(basename(pdf))) {
				return { block: true, reason: BLOCK_REASON };
			}
		}
	}
	return undefined;
}

function interceptToolCall(event: ToolCallEvent, ctx: ExtensionContext): ToolBlock | undefined {
	if (activeTasks.size === 0) return undefined;
	if (isToolCallEventType("read", event)) {
		return blockOnPath(event.input.path, ctx.cwd);
	}
	if (isToolCallEventType("grep", event)) {
		return event.input.path ? blockOnPath(event.input.path, ctx.cwd) : undefined;
	}
	if (isToolCallEventType("bash", event)) {
		return blockOnCommand(event.input.command, ctx.cwd);
	}
	return undefined;
}

// --------------------------------------------------------------------------- //
// Script invocation + tool
// --------------------------------------------------------------------------- //

function buildArgs(params: {
	pdf_path: string;
	tier?: string;
	ocr_mode?: string;
	name?: string;
}): string[] {
	const args = [scriptPath(), params.pdf_path, "--json"];
	if (params.tier) args.push("--tier", params.tier);
	if (params.ocr_mode) args.push("--ocr-mode", params.ocr_mode);
	if (params.name) args.push("--name", params.name);
	return args;
}

function runScript(
	args: string[],
	signal: AbortSignal | undefined,
	env: NodeJS.ProcessEnv,
): Promise<RunResult> {
	return new Promise((resolve, reject) => {
		const child = spawn(pythonPath(), args, { signal, env });
		let stdout = "";
		let stderr = "";
		child.stdout?.on("data", (chunk) => {
			stdout += chunk;
		});
		child.stderr?.on("data", (chunk) => {
			stderr += chunk;
		});
		child.on("error", (error) => reject(error));
		child.on("close", (code) => resolve({ code: code ?? -1, stdout, stderr }));
	});
}

function parsePayload(stdout: string): JsonPayload | undefined {
	const trimmed = stdout.trim();
	if (!trimmed) return undefined;
	try {
		return JSON.parse(trimmed) as JsonPayload;
	} catch {
		return undefined;
	}
}

function failureMessage(payload: JsonPayload | undefined, run: RunResult): string {
	if (payload?.errors?.length) {
		return payload.errors
			.map((error) => {
				const tmp = error.tmp_dir ? ` (temp dir kept: ${error.tmp_dir})` : "";
				return `${error.message ?? "conversion failed"}${tmp}`;
			})
			.join("; ");
	}
	return run.stderr.trim() || `pdf-extract exited with code ${run.code}`;
}

function formatResult(details: PdfExtractDetails): string {
	const stats = details.stats;
	return [
		`Converted: ${details.pdf}`,
		`Markdown: ${details.markdown}`,
		`Images: ${details.images}`,
		`Tier: ${details.tier} (ocr ${details.ocr_mode}) - ${details.routing_reason}`,
		`Trimmed: ${stats.lines_removed} lines / ${stats.chars_removed} chars ` +
			`(kept ${stats.lines_after} lines / ${stats.chars_after} chars)`,
		`Low confidence: ${details.low_confidence ? "yes" : "no"}`,
	].join("\n");
}

const pdfExtractTool = defineTool({
	name: "pdf-extract",
	label: "PDF Extract",
	description:
		"Convert one local PDF into cleaned Markdown via the local MinerU pipeline. " +
		"Returns only the output path, images dir and trim stats; it never returns file contents. " +
		"The script decides the tier automatically unless tier/ocr_mode are given. " +
		"The container is <name>_md/ when name is given, else <pdf>_md/; it holds cleaned.md and image/.",
	parameters: Type.Object({
		pdf_path: Type.String({ description: "Path to the PDF to convert" }),
		tier: Type.Optional(
			Type.String({ description: "Force a MinerU tier: auto|flash|standard" }),
		),
		ocr_mode: Type.Optional(
			Type.String({ description: "Force an OCR mode: auto|txt|ocr" }),
		),
		name: Type.Optional(
			Type.String({
				description:
					"Container basename; default is the PDF filename",
			}),
		),
	}),

	async execute(_toolCallId, params, signal, _onUpdate, ctx) {
		const cwd = ctx?.cwd ?? process.cwd();
		const pdfPath = resolve(cwd, params.pdf_path);
		// Owning the parent temp dir lets us know (and fence) the temp tree before
		// the script exists; the script's mkdtemp child lives inside it.
		const tempRoot = mkdtempSync(join(tmpdir(), "mineru-pdf-extract-task-"));
		const task: ActiveTask = {
			pdfPaths: [pdfPath],
			protectedPaths: [pdfPath, tempRoot],
			tempRoot,
		};
		registerTask(task);

		const run = await runScript(buildArgs(params), signal, {
			...process.env,
			TMPDIR: tempRoot,
		});
		const payload = parsePayload(run.stdout);
		if (run.code !== 0 || payload?.errors?.length) {
			throw new Error(failureMessage(payload, run));
		}
		const result = payload?.results?.[0];
		if (!result || !result.markdown || !result.images || !result.stats) {
			throw new Error(failureMessage(payload, run));
		}

		const details: PdfExtractDetails = {
			pdf: result.pdf ?? params.pdf_path,
			markdown: result.markdown,
			images: result.images,
			tier: result.tier ?? "unknown",
			ocr_mode: result.ocr_mode ?? "unknown",
			routing_reason: result.routing_reason ?? "",
			low_confidence: result.low_confidence ?? false,
			stats: result.stats,
		};

		// Success disarms immediately. Any throw above leaves the task registered
		// (raw md may remain) until `agent_end` clears every registration.
		completeTask(task);
		return {
			content: [{ type: "text", text: formatResult(details) }],
			details,
		};
	},
});

export default function (pi: ExtensionAPI) {
	pi.registerTool(pdfExtractTool);
	pi.on("tool_call", (event, ctx) => interceptToolCall(event, ctx));
	pi.on("agent_end", () => clearRegistrations());
	pi.on("session_shutdown", () => cleanupRetainedTempRoots());
}
