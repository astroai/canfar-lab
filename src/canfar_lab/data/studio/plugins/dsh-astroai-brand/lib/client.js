window.__ModuleLoader__.load({
	id: "dsh-astroai-brand",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const { jsx, jsxs } = require("react/jsx-runtime");

		function AstroAIMark({ size, className }) {
			return jsxs("svg", {
				width: size,
				height: size,
				viewBox: "0 0 32 32",
				className,
				role: "img",
				"aria-label": "AstroAI",
				children: [
					jsx("defs", {
						children: jsxs("linearGradient", {
							id: "astroai-mark-gradient",
							x1: "0",
							y1: "0",
							x2: "1",
							y2: "1",
							children: [
								jsx("stop", { offset: "0", stopColor: "#38bdf8" }),
								jsx("stop", { offset: "0.5", stopColor: "#6366f1" }),
								jsx("stop", { offset: "1", stopColor: "#a855f7" })
							]
						})
					}),
					jsx("path", {
						d: "M9.5 26V14.5a6.5 6.5 0 0 1 13 0V26h-3.2l-3.3-3.4-3.3 3.4z",
						fill: "url(#astroai-mark-gradient)"
					}),
					jsx("path", {
						d: "M16 10.6l1.25 3.15 3.15 1.25-3.15 1.25L16 19.4l-1.25-3.15L11.6 15l3.15-1.25z",
						fill: "#0b1026"
					}),
					jsx("ellipse", {
						cx: "16",
						cy: "19.5",
						rx: "13.5",
						ry: "4",
						transform: "rotate(-16 16 19.5)",
						fill: "none",
						stroke: "url(#astroai-mark-gradient)",
						strokeWidth: "1.7",
						strokeDasharray: "30 6 44"
					}),
					jsx("circle", { cx: "25.5", cy: "5.5", r: "1.1", fill: "#c4b5fd" })
				]
			});
		}

		function AstroAIName() {
			return jsxs("span", {
				style: { fontWeight: 650, letterSpacing: "0.01em", whiteSpace: "nowrap" },
				children: [
					"AstroAI",
					jsx("span", { style: { fontWeight: 500, opacity: 0.6, marginLeft: "0.3em" }, children: "Studio" })
				]
			});
		}

		const inject = ["slots"];

		function apply(ctx) {
			ctx.slots.inject("sidebar.brand.mark", () => ctx.slots.inject("sidebar.brand.name", function* () {
				yield ctx.slots.register({ name: "sidebar.brand.mark" }, AstroAIMark);
				yield ctx.slots.register({ name: "sidebar.brand.name" }, AstroAIName);
			}));
			ctx.slots.inject("conversation.hero.brand.mark", () => ctx.slots.register({ name: "conversation.hero.brand.mark" }, AstroAIMark));
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});
