// Times a 2000-entry `queue` message end to end in the shell's own code: JSON parse and
// validation, then the player applying it to an open queue panel (happy-dom, so the DOM part is
// a floor, not WebKit's number). Dev-only.   cd web && node shell/dev/queue_bench.mjs
import { Window } from "happy-dom";

const win = new Window({ width: 1280, height: 720 });
Object.assign(globalThis, { window: win, document: win.document, KeyboardEvent: win.KeyboardEvent, Event: win.Event, MutationObserver: win.MutationObserver });

const { parseHostMessage } = await import("../validate.js");
const { createPlayer } = await import("../player.js");

const N = 2000;
const entries = (round) =>
  Array.from({ length: N }, (_, i) => ({
    title: `Track number ${i} with a middling title`, artist: i < round * 50 ? `Band ${i % 40}` : "", album: i < round * 50 ? `Album ${i % 90}` : "",
    duration: 120 + (i % 200), playable: i % 97 !== 0,
  }));
const wire = (round) => JSON.stringify({ type: "queue", entries: entries(round) });

document.body.innerHTML = '<div id="app" class="shell"><footer class="bottombar"></footer></div><section class="panel"></section>';
const root = document.getElementById("app");
let frame = null;
const player = createPlayer({
  root, bar: () => root.firstElementChild, send: () => true, toggleQueue() {}, closePanels() {}, visible: () => true,
  raf: (fn) => (frame = fn), caf: () => (frame = null), observe: () => () => {},
});
document.querySelector(".panel").append(player.queueBody);
const hello = parseHostMessage(JSON.stringify({
  type: "hello", version: 1, pluginOrigin: "x", visualizers: [], repos: [], settings: {}, sources: [], activeSource: "player", active: null, dev: false,
  transport: true, nowPlaying: { artist: "", album: "", title: "t", duration: 100, position: 0, playing: false, index: 5, count: N, hostTime: 1, serial: 1 }, queue: { entries: entries(0) },
}));
player.handle(hello);
document.querySelector(".panel").hidden = false;
let t0 = performance.now();
player.renderQueue();
const firstOpen = performance.now() - t0;

/** @param {() => void} fn */
const ms = (fn) => { const t = performance.now(); fn(); return performance.now() - t; };
const rows = [];
for (let round = 0; round < 12; round++) {
  const text = wire(round);
  let msg;
  const parse = ms(() => (msg = parseHostMessage(text)));
  const apply = ms(() => { player.handle(msg); frame?.(); });
  rows.push({ round, bytes: text.length, parse, apply });
}
rows.sort((a, b) => a.parse + a.apply - (b.parse + b.apply));
const med = rows[Math.floor(rows.length / 2)];
console.log(`open (build ${N} rows): ${firstOpen.toFixed(1)} ms`);
console.log(`message: ${(med.bytes / 1024).toFixed(0)} KiB; parse+validate ${med.parse.toFixed(2)} ms; apply to open panel ${med.apply.toFixed(2)} ms (median of ${rows.length}; tags filling in)`);
console.log(`at 4 messages/s: ${((med.parse + med.apply) * 4).toFixed(1)} ms of main-thread time per second`);
const same = wire(11);
let m2; const p2 = ms(() => (m2 = parseHostMessage(same)));
const a2 = ms(() => { player.handle(m2); frame?.(); });
const a3 = ms(() => { player.handle(parseHostMessage(same)); frame?.(); });
console.log(`identical re-send: parse ${p2.toFixed(2)} ms, apply ${a2.toFixed(2)} ms / ${a3.toFixed(2)} ms`);
console.log(`rows in the list: ${document.querySelectorAll(".queue-list li").length}`);
