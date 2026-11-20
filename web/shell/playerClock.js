// Pure helpers for the player's clock and layout (no DOM, no timers).

/** Seconds as m:ss, or h:mm:ss from an hour on. @param {number} s */
export function formatTime(s) {
  const t = Number.isFinite(s) && s > 0 ? Math.floor(s) : 0;
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/**
 * Where the track is at shell time `nowMs`: the host's position plus the shell's own time since
 * the message arrived (`recvAt`, same clock), only while playing. Clamped to a known duration,
 * but the end of a track is the host's call: this never decides it.
 * @param {{ position: number; playing: boolean; duration: number | null }} np
 * @param {number} recvAt @param {number} nowMs
 */
export function positionAt(np, recvAt, nowMs) {
  const p = np.playing ? np.position + Math.max(0, nowMs - recvAt) / 1000 : np.position;
  return np.duration !== null && np.duration > 0 ? Math.min(p, np.duration) : p;
}

/**
 * Milliseconds until the whole second shown for `pos` changes, or null when it never will
 * (sitting on the duration). At least 1.
 * @param {number} pos @param {number | null} duration
 */
export function msToNextSecond(pos, duration) {
  const known = duration !== null && duration > 0;
  if (known && pos >= /** @type {number} */ (duration)) return null;
  const next = known ? Math.min(Math.floor(pos) + 1, /** @type {number} */ (duration)) : Math.floor(pos) + 1;
  return Math.max(1, Math.round((next - pos) * 1000));
}

// Window widths (px) below which a part of the now-playing row is dropped, in this order.
const COLLAPSE_BELOW = [720, 520, 420];

/** 0 = everything, 1 = no album, 2 = no band either, 3 = no time labels either. @param {number} width */
export function collapseLevel(width) {
  return COLLAPSE_BELOW.filter((w) => width < w).length;
}

// The tight row: 1.2 rem buttons plus their borders and a hair (shell.css, `data-fit="2"`).
const TIGHT_ROW_REM = 1.3;

/**
 * How much of the row fits between the top bar's controls and the existing bottom controls
 * (`room`, px; `rem`, px per rem): 0 = title line and controls, 1 = controls only, 2 = controls
 * with tight padding, 3 = no row (not even the tight one fits). Height is what runs out in a small window, and only the title line and the
 * padding give any back (the labels and the slider share the controls' line).
 * @param {number} room @param {number} rem
 */
export function fitLevel(room, rem) {
  if (room >= 4.75 * rem) return 0;
  if (room >= 3 * rem) return 1;
  return room >= TIGHT_ROW_REM * rem ? 2 : 3;
}
