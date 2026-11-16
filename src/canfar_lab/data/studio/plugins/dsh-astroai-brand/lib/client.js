window.__ModuleLoader__.load({
	id: "dsh-astroai-brand",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const { jsx, jsxs } = require("react/jsx-runtime");

		// The CANFAR mark (opencadc/canfar docs/static/logo.png) is white and
		// yellow, so it sits on a dark tile to read on light and dark themes.
		const CANFAR_LOGO = "data:image/png;base64,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";

		function CanfarMark({ size, className }) {
			const px = typeof size === "number" ? size : 24;
			return jsx("span", {
				className,
				role: "img",
				"aria-label": "CANFAR",
				style: {
					display: "inline-flex",
					alignItems: "center",
					justifyContent: "center",
					width: px,
					height: px,
					borderRadius: Math.round(px * 0.22),
					background: "#0f1222",
					flex: "none"
				},
				children: jsx("img", {
					src: CANFAR_LOGO,
					alt: "",
					style: { width: "72%", height: "72%", objectFit: "contain" }
				})
			});
		}

		function CanfarName() {
			return jsxs("span", {
				style: { fontWeight: 650, letterSpacing: "0.01em", whiteSpace: "nowrap" },
				children: [
					"CANFAR",
					jsx("span", { style: { fontWeight: 500, opacity: 0.6, marginLeft: "0.3em" }, children: "Studio" })
				]
			});
		}

		const inject = ["slots"];

		function apply(ctx) {
			document.title = "CANFAR Studio";
			ctx.slots.inject("sidebar.brand.mark", () => ctx.slots.inject("sidebar.brand.name", function* () {
				yield ctx.slots.register({ name: "sidebar.brand.mark" }, CanfarMark);
				yield ctx.slots.register({ name: "sidebar.brand.name" }, CanfarName);
			}));
			ctx.slots.inject("conversation.hero.brand.mark", () => ctx.slots.register({ name: "conversation.hero.brand.mark" }, CanfarMark));
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});
