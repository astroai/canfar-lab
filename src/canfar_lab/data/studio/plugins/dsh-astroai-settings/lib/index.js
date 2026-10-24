/**
 * Host half: GET/POST /api/astroai/layout on the dsh web server.
 *
 * The body is `canfar-lab agent settings --json` (`--json` is on this verb).
 * Secret values stay in the Python key store and are not part of that payload.
 */

import { spawnSync } from "node:child_process";

export const name = "astroai-settings";
export const inject = ["webServer", "connection"];

const LAYOUT_PATH = "/api/astroai/layout";
const SYNC_PATH = "/api/astroai/sync";

function labBin() {
	const pin = process.env.ASTROAI_STUDIO_MCP_BIN;
	return pin && pin.trim() ? pin.trim() : "canfar-lab";
}

function runSettings(args, timeout = 20000) {
	const proc = spawnSync(labBin(), args, { encoding: "utf8", timeout });
	const text = (proc.stdout || "").trim();
	try {
		return JSON.parse(text);
	} catch {
		const detail = (proc.stderr || text || "settings failed").slice(0, 400);
		return { ok: false, error: detail };
	}
}

function sendJson(res, status, payload) {
	res.statusCode = status;
	res.setHeader("content-type", "application/json; charset=utf-8");
	res.setHeader("cache-control", "no-store");
	res.end(JSON.stringify(payload));
}

function rejected(ctx, req, res) {
	const connection = Reflect.get(ctx, "connection");
	if (!connection || typeof connection.requestRejection !== "function") return false;
	const code = connection.requestRejection(req);
	if (code === undefined) return false;
	res.statusCode = code;
	res.end();
	return true;
}

async function readBody(req) {
	const chunks = [];
	let size = 0;
	for await (const chunk of req) {
		size += chunk.byteLength;
		if (size > 4096) return null;
		chunks.push(chunk);
	}
	return Buffer.concat(chunks, size).toString("utf8");
}

function register(ctx, path, handler) {
	return ctx.webServer.register({ kind: "exact", path, handler });
}

export function apply(ctx) {
	ctx.effect(() =>
		register(ctx, SYNC_PATH, async (req, res) => {
			if (rejected(ctx, req, res)) return;
			if (req.method === "GET") {
				sendJson(res, 200, runSettings(["sync", "status", "--json"]));
				return;
			}
			if (req.method !== "POST") {
				res.statusCode = 405;
				res.setHeader("allow", "GET, POST");
				res.end();
				return;
			}
			sendJson(res, 200, runSettings(["--json", "sync", "exchange", "--to", "canfar"], 120000));
		}),
	);
	ctx.effect(() =>
		register(ctx, LAYOUT_PATH, async (req, res) => {
			if (rejected(ctx, req, res)) return;
			if (req.method === "GET") {
				sendJson(res, 200, runSettings(["agent", "settings", "--json"]));
				return;
			}
			if (req.method !== "POST") {
				res.statusCode = 405;
				res.setHeader("allow", "GET, POST");
				res.end();
				return;
			}
			const text = await readBody(req);
			let body;
			try {
				body = JSON.parse(text || "");
			} catch {
				sendJson(res, 400, { ok: false, error: "expected JSON" });
				return;
			}
			const root = body && body.root;
			if (root !== "home" && root !== "scratch") {
				sendJson(res, 400, { ok: false, error: "root must be home or scratch" });
				return;
			}
			const payload = runSettings(["agent", "settings", "--root", root, "--json"]);
			sendJson(res, payload && payload.ok === false && payload.error ? 400 : 200, payload);
		}),
	);
}
