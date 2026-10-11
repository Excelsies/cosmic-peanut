// Perf HUD (toggle with P). Values arrive at 1 Hz; the DOM is written when they do, at most 4×/s
// and only while visible, never on the frame path.

import { h } from "./dom.js";

/** @typedef {import("./validate.js").PluginPerf} PluginPerf */
/** @typedef {import("./validate.js").Stats} Stats */

const RENDER_MS = 250;

/** @param {number | undefined} v @param {number} d */
const f = (v, d = 1) => (v === undefined || !Number.isFinite(v) ? "–" : v.toFixed(d));

/** @param {HTMLElement} el */
export function createHud(el) {
  /** @type {PluginPerf | null} */ let perf = null;
  /** @type {Stats | null} */ let stats = null;
  /** @type {number | undefined} */ let shellMs;
  /** @type {ReturnType<typeof setTimeout> | undefined} */ let timer;
  let last = -Infinity;

  /** @type {Record<string, HTMLElement>} */
  const cells = {};
  /** @param {string} id @param {string} label */
  const row = (id, label) => {
    cells[id] = h("dd", {}, "–");
    return [h("dt", {}, label), cells[id]];
  };
  el.replaceChildren(
    h(
      "dl",
      {},
      row("fps", "Render"),
      row("frame", "Frame p50 / p99"),
      row("plugin", "Plugin"),
      row("shell", "Shell"),
      row("latency", "Audio latency p95"),
      row("dropped", "Dropped audio frames"),
      row("cpu", "Host CPU"),
    ),
  );
  el.hidden = true;

  /** @param {string} id @param {string} text */
  const set = (id, text) => {
    if (cells[id].textContent !== text) cells[id].textContent = text;
  };

  const flush = () => {
    timer = undefined;
    last = Date.now();
    api.render();
  };
  const touch = () => {
    if (!el.hidden && timer === undefined) timer = setTimeout(flush, Math.max(0, last + RENDER_MS - Date.now()));
  };

  const api = {
    get visible() {
      return !el.hidden;
    },
    /** @param {boolean} v */
    setVisible(v) {
      el.hidden = !v;
      clearTimeout(timer);
      timer = undefined;
      touch();
    },
    /** @param {PluginPerf} p */
    setPerf(p) {
      perf = p;
      touch();
    },
    /** @param {Stats} s */
    setStats(s) {
      stats = s;
      touch();
    },
    /** @param {number} ms */
    setShellMs(ms) {
      shellMs = ms;
      touch();
    },
    render() {
      set("fps", perf ? `${Math.round(perf.fps)} fps${perf.renderScale < 1 ? ` @ ${Math.round(perf.renderScale * 100)}%` : ""}` : "–");
      set("frame", perf ? `${f(perf.frameMsP50)} / ${f(perf.frameMsP99)} ms` : "–");
      set("plugin", perf ? `${f(perf.pluginMsP50)} ms` : "–");
      set("shell", shellMs === undefined ? "–" : `${f(shellMs, 2)} ms`);
      set("latency", stats?.latencyMsP95 !== undefined ? `${f(stats.latencyMsP95, 0)} ms` : "–");
      set("dropped", stats ? String(stats.droppedFrames) : "–");
      set("cpu", stats ? `${f(stats.hostCpu)}%` : "–");
    },
  };
  return api;
}
