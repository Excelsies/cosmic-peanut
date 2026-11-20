// Keyboard shortcuts (PRD "Host UI → Keys").

/** @typedef {"next" | "prev" | "fullscreen" | "floatOnTop" | "hideOverlays" | "library" | "hud" | "escape"
 *   | "playPause" | "seekBack" | "seekForward" | "trackPrev" | "trackNext" | "queue"} KeyAction */

const TEXT_TAGS = new Set(["TEXTAREA", "SELECT"]);
const NON_TEXT_INPUTS = new Set(["range", "checkbox", "radio", "color", "button", "submit", "reset"]);

/** @type {Record<string, KeyAction>} */
const MAP = { n: "next", f: "fullscreen", t: "floatOnTop", h: "hideOverlays", l: "library", p: "hud" };

// Player keys (the app acts on them only while the source has a transport). Space and the arrows
// belong to a focused slider, checkbox or select. Space belongs to any focused interactive control
// (a button is pressed, not play/pause); it toggles playback only with focus elsewhere.
/** @type {Record<string, KeyAction>} */
const PLAYER_MAP = { " ": "playPause", ArrowLeft: "seekBack", ArrowRight: "seekForward", ",": "trackPrev", ".": "trackNext", q: "queue" };
const CONTROLS = "button, a[href], [role=button], input, select, textarea, summary, [contenteditable]";
const CONTROL_TAGS = new Set(["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"]);
const OWN_KEYS = new Set([" ", "ArrowLeft", "ArrowRight"]);

/**
 * @param {{ key: string; shiftKey: boolean; metaKey: boolean; ctrlKey: boolean; altKey: boolean;
 *   target: EventTarget | { tagName?: string; type?: string } | null }} e
 * @returns {KeyAction | null}
 */
export function keyAction(e) {
  if (e.key === "Escape") return "escape";
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  const t = /** @type {{ tagName?: string; type?: string; closest?: (sel: string) => unknown } | null} */ (e.target);
  const tag = t?.tagName ?? "";
  if (TEXT_TAGS.has(tag)) return null;
  if (tag === "INPUT" && !NON_TEXT_INPUTS.has(t?.type ?? "text")) return null;
  const k = e.key.toLowerCase();
  const player = PLAYER_MAP[e.key] ?? PLAYER_MAP[k];
  if (player) {
    if (e.shiftKey) return null;
    if (OWN_KEYS.has(e.key) && (tag === "INPUT" || tag === "SELECT")) return null;
    if (e.key === " " && (CONTROL_TAGS.has(tag) || t?.closest?.(CONTROLS))) return null;
    return player;
  }
  if (k === "n" && e.shiftKey) return "prev";
  return MAP[k] ?? null;
}
