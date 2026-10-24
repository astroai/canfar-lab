window.__ModuleLoader__.load({
	id: "dsh-astroai-settings",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const { jsx, jsxs } = require("react/jsx-runtime");
		const React = require("react");

		const inject = ["slots"];
		const SOURCE = { studio: "Studio", assistant: "dsh", environment: "shell" };

		function readJson(res) {
			return res.text().then((text) => {
				try {
					return JSON.parse(text);
				} catch {
					return { ok: false, error: (text || res.statusText || "settings unavailable").slice(0, 200) };
				}
			});
		}

		function safeUrl(url) {
			return typeof url === "string" && url.startsWith("https://") ? url : "";
		}

		function rootCopy(data) {
			if (!data.choice) {
				return "This computer keeps each agent's usual install path. Config and keys stay in your home directory.";
			}
			if (data.root === "home") {
				return "Binaries are on home, so they survive the session. That disk is small and slow. Caches stay on scratch.";
			}
			return "Binaries are on scratch for this session. Config and keys stay on home.";
		}

		function Sync() {
			const [status, setStatus] = React.useState(null);
			const [note, setNote] = React.useState("");
			const [busy, setBusy] = React.useState(false);

			const load = React.useCallback(() => {
				fetch("/api/astroai/sync", { headers: { accept: "application/json" } })
					.then(readJson)
					.then((body) => {
						setStatus(body);
						setBusy(false);
					})
					.catch((err) => {
						setNote(String(err));
						setBusy(false);
					});
			}, []);

			React.useEffect(() => {
				load();
			}, [load]);

			function push() {
				setBusy(true);
				setNote("");
				fetch("/api/astroai/sync", { method: "POST" })
					.then(readJson)
					.then((body) => {
						const hint = body && (body.hint || body.error);
						const summary = body && body.published && body.published.summary;
						const counts = summary
							? Object.keys(summary)
									.map((key) => summary[key] + " " + key)
									.join(", ")
							: "";
						setNote(counts || hint || (body && body.ok === false ? "Sync failed." : "Sync finished."));
						setBusy(false);
						load();
					})
					.catch((err) => {
						setNote(String(err));
						setBusy(false);
					});
			}

			const repos = ((status && status.repos) || []).filter(
				(row) => row && row.exists && (row.dirty || row.ahead_origin || row.behind_upstream),
			);
			return jsxs("div", {
				style: { display: "grid", gap: "0.4rem" },
				children: [
					jsx("button", {
						type: "button",
						disabled: busy,
						onClick: push,
						style: {
							font: "inherit",
							justifySelf: "start",
							padding: "0.4rem 0.8rem",
							borderRadius: "0.4rem",
							border: "1px solid #4338ca",
							background: "#4338ca",
							color: "#fff",
							cursor: busy ? "progress" : "pointer",
						},
						children: busy ? "Syncing…" : "Sync to CANFAR",
					}),
					jsx("p", {
						style: { margin: 0, opacity: 0.75 },
						children: "Sends commits from this computer. The session pulls them.",
					}),
					repos.length
						? jsx("ul", {
								style: { margin: 0, paddingLeft: "1.1rem" },
								children: repos.map((row) =>
									jsx(
										"li",
										{
											children:
												row.name +
												(row.dirty ? " has uncommitted edits" : "") +
												(row.ahead_origin ? " is " + row.ahead_origin + " commit(s) ahead" : "") +
												(row.behind_upstream ? " can fast-forward" : ""),
										},
										row.name,
									),
								),
							})
						: null,
					note ? jsx("p", { style: { margin: 0 }, children: note }) : null,
				],
			});
		}

		function Section() {
			const [data, setData] = React.useState(null);
			const [error, setError] = React.useState("");
			const [busy, setBusy] = React.useState(false);

			const applyBody = React.useCallback((body) => {
				if (body && body.root) setData(body);
				setError(body && body.error ? String(body.error) : "");
				setBusy(false);
			}, []);

			const load = React.useCallback(() => {
				fetch("/api/astroai/layout", { headers: { accept: "application/json" } })
					.then(readJson)
					.then(applyBody)
					.catch((err) => {
						setError(String(err));
						setBusy(false);
					});
			}, [applyBody]);

			React.useEffect(() => {
				load();
			}, [load]);

			function choose(root) {
				if (busy || (data && data.root === root)) return;
				setBusy(true);
				setError("");
				fetch("/api/astroai/layout", {
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify({ root }),
				})
					.then(readJson)
					.then(applyBody)
					.catch((err) => {
						setError(String(err));
						setBusy(false);
					});
			}

			const keys = ((data && data.keys) || [])
				.filter((row) => row && row.key)
				.slice()
				.sort((a, b) => Number(Boolean(b.present)) - Number(Boolean(a.present)) || String(a.label || a.key).localeCompare(String(b.label || b.key)));
			const issues = (data && data.issues) || [];
			const warnings = (data && data.warnings) || [];
			return jsxs("section", {
				style: { display: "grid", gap: "0.85rem", maxWidth: "36rem" },
				children: [
					jsx(Sync, {}),
					jsx("p", {
						style: { margin: 0, opacity: 0.75 },
						children: "Install location and which provider keys are on this machine. Model choice stays in Settings → Models.",
					}),
					!data
						? jsx("p", { style: { margin: 0 }, children: error ? "" : "Checking this machine…" })
						: jsxs(React.Fragment, {
								children: [
									data.choice
										? jsxs("div", {
												style: { display: "flex", gap: "0.5rem" },
												children: ["scratch", "home"].map((root) => {
													const on = data.root === root;
													const label = root === "scratch" ? "Scratch" : "Home";
													return jsx(
														"button",
														{
															type: "button",
															disabled: busy,
															onClick: () => choose(root),
															"aria-pressed": on,
															style: {
																font: "inherit",
																padding: "0.35rem 0.75rem",
																borderRadius: "0.4rem",
																cursor: busy ? "progress" : "pointer",
																border: on ? "1px solid #4338ca" : "1px solid rgba(128,128,128,0.45)",
																background: on ? "#4338ca" : "transparent",
																color: on ? "#fff" : "inherit",
															},
															children: label + (data.default_root === root ? " · default" : ""),
														},
														root,
													);
												}),
											})
										: null,
									jsx("p", { style: { margin: 0 }, children: rootCopy(data) }),
									data.choice
										? null
										: jsx("p", {
												style: { margin: 0 },
												children: "Sync to CANFAR is on the top bar. Hide tucks that bar away.",
											}),
									data.bin_dir
										? jsx("p", {
												style: { margin: 0 },
												children: jsxs(React.Fragment, {
													children: [
														"Binaries ",
														jsx("code", { style: { wordBreak: "break-all" }, children: data.bin_dir }),
													],
												}),
											})
										: null,
									keys.length
										? jsx("ul", {
												style: { margin: 0, padding: 0, listStyle: "none", display: "grid", gap: "0.35rem" },
												children: keys.map((row) => {
													const sources = (row.sources || []).map((name) => SOURCE[name] || name).join(", ");
													const href = safeUrl(row.signup_url);
													return jsxs(
														"li",
														{
															style: { display: "flex", justifyContent: "space-between", gap: "1rem" },
															children: [
																jsxs("span", {
																	children: [
																		row.label || row.key,
																		jsx("span", { style: { opacity: 0.55, marginLeft: "0.4rem" }, children: row.key }),
																	],
																}),
																jsxs("span", {
																	style: { opacity: row.present ? 1 : 0.65, textAlign: "right" },
																	children: [
																		row.present ? "Ready" + (sources ? " · " + sources : "") : "Not set",
																		!row.present && href
																			? jsx("a", {
																					href,
																					target: "_blank",
																					rel: "noopener noreferrer",
																					style: { marginLeft: "0.4rem" },
																					children: "get a key",
																				})
																			: null,
																	],
																}),
															],
														},
														row.key,
													);
												}),
											})
										: null,
									warnings.map((line) =>
										jsx("p", { style: { margin: 0, color: "#b45309" }, children: line }, line),
									),
									issues.map((line) =>
										jsx("p", { style: { margin: 0, color: "#b91c1c" }, children: line }, "issue:" + line),
									),
								],
							}),
					error ? jsx("p", { style: { margin: 0, color: "#b91c1c" }, children: error }) : null,
				],
			});
		}

		function apply(ctx) {
			ctx.slots.inject("settings.section", () =>
				ctx.slots.register(
					{
						name: "settings.section",
						id: "astroai",
						order: 40,
						label: "AstroAI",
					},
					Section,
				),
			);
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	},
});
