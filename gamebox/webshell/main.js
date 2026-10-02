// Electron main process for CosmOS's "webshell" - the app-interface
// substrate that replaced Brave for single-URL kiosk content (the main
// menu's quick-launch shortcuts, Gaming News article links). One
// BrowserWindow, one URL, no chrome - there's no tab strip/address bar
// to build because there's none of Brave's own browser UI to replicate
// in the first place; this window IS the content.
//
// Invoked as `electron <this-dir> --url=<url> --user-data-dir=<path>`
// by webshell/kiosk_launcher.py - see that file's own docstring for the
// full launch chain (graphical.run_graphical(), stick_mode bracketing).
//
// --url/--user-data-dir are explicit flags, not positional argv - argv
// layout shifts between "run unpackaged via `electron <dir>`" and "run
// as a packaged app", so a positional URL isn't a stable contract
// across those invocation shapes the way a named flag is.
const { app, BrowserWindow, Menu, session } = require("electron");
const path = require("path");
const fs = require("fs");

// Read once at startup (the main process has full, unsandboxed Node
// access - unlike a preload script, see the real, confirmed-live bug
// this replaced: an earlier version loaded these via a preload.js doing
// its own `require("fs")`, which Electron's default sandboxed preload
// environment rejects outright ("Error: module not found: fs",
// confirmed live via --enable-logging=stderr) - preload scripts only
// get a restricted API surface unless the whole renderer's sandbox is
// disabled, which isn't worth doing just to read two small text files.
// Injecting from here instead sidesteps that restriction entirely.
const CURSOR_CSS = fs.readFileSync(path.join(__dirname, "cursor.css"), "utf8");
const CURSOR_JS = fs.readFileSync(path.join(__dirname, "cursor.js"), "utf8");

function parseFlag(name) {
	const prefix = `--${name}=`;
	const arg = process.argv.find((a) => a.startsWith(prefix));
	return arg ? arg.slice(prefix.length) : null;
}

const targetUrl = parseFlag("url");
const userDataDir = parseFlag("user-data-dir");

if (!targetUrl) {
	console.error("webshell: no --url= given, nothing to load");
	app.exit(1);
}

// Determines the X11 WM_CLASS this window maps under - confirmed live
// (xprop against a real running instance) to produce exactly
// "cosmos-webshell", matching this string verbatim. Unlike Brave,
// Electron's own PID turned out NOT disconnected from its real window
// (confirmed live: `xdotool search --pid <the subprocess.Popen pid>`
// found the mapped window directly, no timeout) - see kiosk_launcher.py's
// own docstring for why window_class=... isn't actually needed as a
// run_graphical() workaround here, unlike it was for Chromium.
app.setName("cosmos-webshell");

// Must be set before whenReady(), and paths.DATA_ROOT-relative (passed
// in from the Python launcher) rather than __file__-relative - this
// directory lives inside the swappable APP_ROOT tree (see packaging/
// cosmos_install.py), replaced wholesale on every update, so
// profile/session data living there would be lost on the next update.
// paths.DATA_ROOT-relative survives that, the same reasoning
// brave_launcher.py's own BRAVE_HOME used. Keeps quick-launch/news-
// article login state persisting across separate opens, same as
// Brave's shared profile did.
if (userDataDir) {
	app.setPath("userData", userDataDir);
}

function injectCursor(webContents) {
	// Neither insertCSS nor executeJavaScript survives a full page
	// navigation, so this must fire on every dom-ready, not just once at
	// load - covers clicking a link within a quick-launch site/article
	// re-hiding the cursor and redrawing the dot on the new page too.
	// executeJavaScript runs CURSOR_JS in the page's own main world (not
	// an isolated context), exactly like a real <script> tag would -
	// it needs real DOM access to listen for the page's own mousemove
	// events and append its dot element, same as when this ran as a
	// loaded Chromium extension's content script.
	webContents.insertCSS(CURSOR_CSS).catch(() => {});
	webContents.executeJavaScript(CURSOR_JS).catch(() => {});
}

function createWindow() {
	const win = new BrowserWindow({
		kiosk: true,
		frame: false,
		autoHideMenuBar: true,
		backgroundColor: "#000000",
		webPreferences: {
			contextIsolation: true,
			nodeIntegration: false,
		},
	});

	// Electron ships a default File/Edit/View menu bar unless explicitly
	// removed - autoHideMenuBar only hides it from view, not from the
	// object model (Alt still reveals it) - remove it outright so
	// there's no chrome surface a stray Alt keypress or gamepad menu
	// button could summon.
	Menu.setApplicationMenu(null);

	// A bare BrowserWindow has no browser chrome to fall back into for
	// a stray target=_blank link the way Brave-in-kiosk-mode still did -
	// deny outright rather than let it silently fail or spawn a second,
	// unmanaged, chromeless window with no way back. Revisit as a
	// same-window redirect instead only if real quick-launch/news
	// testing shows outbound links are actually expected to work.
	win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));

	win.webContents.on("dom-ready", () => {
		injectCursor(win.webContents);
		flushCookies();
	});

	win.loadURL(targetUrl);
}

// A real, confirmed-live finding, not a hypothetical: Chromium's own
// cookie store batches/delays writes to its on-disk SQLite backing file
// rather than flushing per-set - directly queried a real test profile's
// own Cookies db immediately after setting a cookie and found zero rows
// still written. CosmOS exits every graphical program via an
// immediate process-tree SIGKILL, never a clean shutdown (see
// kiosk_launcher.py's own stick_mode comment), which never gives
// Chromium's own flush-on-clean-exit path a chance to run either - the
// exact same class of bug that drove Brave's own shared-profile design
// in the first place (an async background operation, there Widevine's
// component-updater download, getting killed mid-flight by this
// project's own exit model). Without this, a login session on a
// quick-launch site could silently fail to persist across separate
// opens despite `userData` being wired up correctly - the code would
// look right and still lose data.
//
// Flushed on every dom-ready (covers a real page load/navigation
// setting a login cookie) PLUS a periodic timer (covers a cookie set by
// page JS/an AJAX login well after dom-ready, with no further
// navigation event to hang a flush off of) - between the two, whatever
// cookie state exists at the moment of an abrupt kill should already be
// on disk far more often than leaving Chromium's own default batching
// to chance. Not `webRequest.onCompleted` (fires per sub-resource -
// images, scripts, XHRs - far too often on a busy page for what this
// needs).
function flushCookies() {
	session.defaultSession.cookies.flushStore().catch(() => {});
}

const COOKIE_FLUSH_INTERVAL_MS = 10000;

app.whenReady().then(() => {
	createWindow();
	setInterval(flushCookies, COOKIE_FLUSH_INTERVAL_MS);
});

// CosmOS exits every graphical program via a process-tree kill, not
// a clean shutdown (see kiosk_launcher.py's own stick_mode comment) -
// this handler exists only so run_graphical() doesn't hang waiting on a
// dead window if something manages to close the window without the
// process itself being killed externally.
app.on("window-all-closed", () => app.quit());
