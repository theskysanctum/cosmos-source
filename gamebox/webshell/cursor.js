// Ported verbatim from the old browser/dot_cursor_extension/cursor.js -
// draws the actual visible dot that follows the real mouse position.
// cursor.css just hides the real cursor everywhere; this is what takes
// its place. A plain fixed-position div rather than anything
// canvas/SVG-based since a solid circle is all this needs to be.
// pointer-events: none keeps it from ever intercepting clicks/hovers
// meant for the real page underneath it - it's purely decorative.
(function () {
	const DOT_SIZE = 10;

	const dot = document.createElement("div");
	dot.style.cssText = [
		"position: fixed",
		`width: ${DOT_SIZE}px`,
		`height: ${DOT_SIZE}px`,
		"border-radius: 50%",
		"background: #ff0090",
		"pointer-events: none",
		"z-index: 2147483647",
		"top: 0",
		"left: 0",
		"transform: translate(-50%, -50%)",
		"display: none",
	].join(";");

	function attach() {
		document.documentElement.appendChild(dot);
	}
	if (document.documentElement) {
		attach();
	} else {
		document.addEventListener("DOMContentLoaded", attach);
	}

	document.addEventListener(
		"mousemove",
		(e) => {
			dot.style.display = "block";
			dot.style.left = e.clientX + "px";
			dot.style.top = e.clientY + "px";
		},
		true,
	);

	document.addEventListener("mouseleave", () => {
		dot.style.display = "none";
	});
})();
