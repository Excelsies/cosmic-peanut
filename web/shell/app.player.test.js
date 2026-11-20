// @vitest-environment happy-dom
// The player as the app wires it: keys, panels, idle, source switches.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp } from "./app.js";

const viz = {
  key: "builtin/bars", repo: "builtin", id: "bars", name: "Bars", description: "", author: "", renderer: "2d",
  thumbnailUrl: null, params: [{ id: "hue", type: "number", label: "Hue", min: 0, max: 360, default: 200 }],
  values: {}, disabled: false, dev: false, entryUrl: "", pageUrl: "about:blank",
};
const np = (/** @type {Record<string, any>} */ o = {}) => ({
  type: "nowPlaying", artist: "Band", album: "Record", title: "Song", duration: 200, position: 10, playing: true,
  index: 0, count: 2, hostTime: 1, serial: 4, ...o,
});
const entries = [
  { title: "Song", artist: "Band", album: "Record", duration: 200, playable: true },
  { title: "Other", artist: "", album: "", duration: null, playable: true },
];
const hello = (/** @type {Record<string, any>} */ o = {}) => /** @type {any} */ ({
  type: "hello", version: 1, pluginOrigin: "http://127.0.0.1:2", visualizers: [viz], repos: [],
  settings: { photosensitivityNoticeSeen: true }, sources: [{ id: "system", name: "All audio" }, { id: "player", name: "Files" }],
  activeSource: "player", active: "builtin/bars", dev: false, transport: true, nowPlaying: np(), queue: { entries }, ...o,
});

function setup() {
  document.body.innerHTML = '<div id="app"></div>';
  const root = /** @type {HTMLElement} */ (document.getElementById("app"));
  const sent = /** @type {any[]} */ ([]);
  const host = { activeKey: null, pendingKey: null, show: vi.fn(), reload: vi.fn(), frame: vi.fn(), setParams: vi.fn(), setSettings: vi.fn(), setVisible: vi.fn(), pointer: vi.fn(), shellStats: vi.fn(() => ({ p50: 0 })), dispose: vi.fn() };
  const app = createApp({ root, send: (m) => (sent.push(m), true), createPluginHost: /** @type {any} */ (() => host) });
  /** @param {string} key @param {KeyboardEventInit} [o] @param {EventTarget} [on] */
  const press = (key, o = {}, on = document) => {
    const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...o });
    on.dispatchEvent(ev);
    return ev;
  };
  const transportMsgs = () => sent.filter((m) => m.type === "transport" || m.type.startsWith("player"));
  return { app, root, sent, press, transportMsgs };
}

// Every createApp adds listeners to the shared document and has no dispose: take them off again,
// or an earlier test's app answers this test's keys.
/** @type {[string, any, any][]} */
let added = [];
beforeEach(() => {
  vi.useFakeTimers();
  // happy-dom has no layout: give the bars a plausible one (top controls end at 56, the bottom ones start at 400).
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(/** @this {Element} */ function () {
    const inTop = this.closest(".topbar") !== null;
    const r = inTop ? { top: 14, bottom: 56 } : { top: 400, bottom: 440 };
    return /** @type {DOMRect} */ ({ ...r, left: 0, right: 100, width: 100, height: r.bottom - r.top, x: 0, y: r.top, toJSON: () => ({}) });
  });
  const add = document.addEventListener.bind(document);
  vi.spyOn(document, "addEventListener").mockImplementation((/** @type {any} */ t, /** @type {any} */ fn, /** @type {any} */ o) => (added.push([t, fn, o]), add(t, fn, o)));
});
afterEach(() => {
  vi.restoreAllMocks();
  for (const [t, fn, o] of added) document.removeEventListener(t, fn, o);
  added = [];
  vi.useRealTimers();
});

describe("app + player: no transport, no player", () => {
  it("a host without the fields shows no player and handles none of its keys", () => {
    const { app, root, press, sent, transportMsgs } = setup();
    app.handle(hello({ transport: false, nowPlaying: null, queue: null }));
    expect(root.querySelector(".player")).toBeNull();
    expect(root.querySelector(".player-open, .queue-list")).toBeNull();
    for (const key of [" ", "ArrowLeft", "ArrowRight", ",", ".", "q"]) {
      expect(press(key).defaultPrevented).toBe(false);
    }
    expect(transportMsgs()).toEqual([]);
    expect(root.querySelector("#queue")?.hasAttribute("hidden")).toBe(true);
    expect(sent.find((m) => m.type === "settings")).toBeUndefined();
  });

  it("macOS-style hello (no transport key at all) is the same", () => {
    const { app, root, press } = setup();
    const m = hello();
    delete m.transport;
    delete m.nowPlaying;
    delete m.queue;
    app.handle(m);
    expect(root.querySelector(".player")).toBeNull();
    expect(press(" ").defaultPrevented).toBe(false);
  });

  it("existing keys keep working with the player absent", () => {
    const { app, root, press } = setup();
    app.handle(hello({ transport: false, nowPlaying: null, queue: null }));
    press("l");
    expect(root.querySelector("#library")?.hasAttribute("hidden")).toBe(false);
  });
});

describe("app + player: with a transport", () => {
  it("the row sits in the bottom overlay", () => {
    const { app, root } = setup();
    app.handle(hello());
    expect(root.querySelector(".bottombar > .player")).not.toBeNull();
    expect(root.querySelector(".player-title")?.textContent).toBe("Song");
  });

  it("Space toggles and does not scroll or press anything", () => {
    const { app, press, transportMsgs } = setup();
    app.handle(hello());
    expect(press(" ").defaultPrevented).toBe(true);
    expect(transportMsgs()).toEqual([{ type: "transport", action: "toggle" }]);
  });

  it("the other keys send their messages", () => {
    const { app, press, transportMsgs } = setup();
    app.handle(hello());
    press(",");
    press(".");
    press("ArrowRight");
    expect(transportMsgs()).toEqual([
      { type: "transport", action: "previous" },
      { type: "transport", action: "next" },
      { type: "transport", action: "seek", value: 15, serial: 4 },
    ]);
  });

  it("n and Shift+N still change the visualizer, not the track", () => {
    const { app, press, transportMsgs } = setup();
    app.handle(hello());
    press("n");
    press("N", { shiftKey: true });
    expect(transportMsgs()).toEqual([]);
  });

  it("while a panel button has focus, Space presses that button instead", () => {
    const { app, root, press, transportMsgs } = setup();
    app.handle(hello());
    press("q");
    const btn = /** @type {HTMLElement} */ (root.querySelector("#queue .q-open-files"));
    btn.focus();
    expect(press(" ", {}, btn).defaultPrevented).toBe(false);
    expect(transportMsgs()).toEqual([]);
  });

  it("Space on any focused button is that button's, anywhere in the shell", () => {
    const { app, root, press, transportMsgs } = setup();
    app.handle(hello());
    for (const sel of [".topbar button", ".bottombar .player-play", ".bottombar .player-queue"]) {
      const btn = /** @type {HTMLElement} */ (root.querySelector(sel));
      btn.focus();
      expect(press(" ", {}, btn).defaultPrevented).toBe(false);
    }
    expect(transportMsgs()).toEqual([]);
    expect(press(" ", {}, document.body).defaultPrevented).toBe(true); // nothing focused: play/pause
    expect(transportMsgs()).toEqual([{ type: "transport", action: "toggle" }]);
  });

  it("a held arrow key seeks once: key repeats never reach the player", () => {
    const { app, press, transportMsgs } = setup();
    app.handle(hello());
    press("ArrowRight");
    for (let i = 0; i < 5; i++) press("ArrowRight", { repeat: true });
    expect(transportMsgs()).toEqual([{ type: "transport", action: "seek", value: 15, serial: 4 }]);
  });

  it("a focused slider keeps Space and the arrows", () => {
    const { app, root, press, transportMsgs } = setup();
    app.handle(hello());
    const slider = /** @type {HTMLElement} */ (root.querySelector(".player-seek"));
    expect(press("ArrowLeft", {}, slider).defaultPrevented).toBe(false);
    expect(press(" ", {}, slider).defaultPrevented).toBe(false);
    expect(transportMsgs()).toEqual([]);
  });

  it("no player keys while a modal is open", () => {
    const { app, press, transportMsgs } = setup();
    app.handle(hello({ settings: {} })); // first launch: the photosensitivity notice
    press(" ");
    expect(transportMsgs()).toEqual([]);
  });

  it("q opens the queue panel with the list; Escape closes it", () => {
    const { app, root, press } = setup();
    app.handle(hello());
    expect(root.querySelector("#queue .queue-list")).toBeNull(); // not rendered before it is opened
    press("q");
    const panel = /** @type {HTMLElement} */ (root.querySelector("#queue"));
    expect(panel.hidden).toBe(false);
    expect(root.classList.contains("panel-open")).toBe(true);
    expect(panel.querySelectorAll(".queue-list li")).toHaveLength(2);
    expect(panel.querySelector('button[aria-current="true"] .q-title')?.textContent).toBe("Song");
    press("Escape");
    expect(panel.hidden).toBe(true);
    press("q");
    press("q");
    expect(panel.hidden).toBe(true);
  });

  it("the Queue button and the other panels share the one-panel-at-a-time rule", () => {
    const { app, root, press } = setup();
    app.handle(hello());
    press("q");
    press("l");
    expect(/** @type {HTMLElement} */ (root.querySelector("#queue")).hidden).toBe(true);
    expect(/** @type {HTMLElement} */ (root.querySelector("#library")).hidden).toBe(false);
  });

  it("switching to a source without a transport removes the row and closes the queue", () => {
    const { app, root, press } = setup();
    app.handle(hello());
    press("q");
    app.handle({ type: "sources", sources: [{ id: "system", name: "All audio" }], active: "system", transport: false });
    expect(root.querySelector(".player")).toBeNull();
    expect(/** @type {HTMLElement} */ (root.querySelector("#queue")).hidden).toBe(true);
    expect(root.classList.contains("panel-open")).toBe(false);
    expect(press(" ").defaultPrevented).toBe(false);
  });

  it("host skip notices still reach the status line", () => {
    const { app, root } = setup();
    app.handle(hello());
    app.handle({ type: "status", level: "warn", text: "Skipped <b>x</b>: unreadable" });
    expect(root.querySelector("#status")?.textContent).toBe("Skipped <b>x</b>: unreadable");
    expect(root.querySelector("#status b")).toBeNull();
  });
});

describe("app + player: overlays", () => {
  const second = () => document.querySelector(".player-elapsed")?.textContent;

  it("the clock runs while the overlays show and stops when they fade", () => {
    const { app, root } = setup();
    app.handle(hello());
    vi.advanceTimersByTime(1100);
    expect(second()).toBe("0:11");
    vi.advanceTimersByTime(2500); // idle at 3 s
    expect(root.classList.contains("idle")).toBe(true);
    const frozen = second();
    vi.advanceTimersByTime(20000);
    expect(second()).toBe(frozen);
    document.dispatchEvent(new Event("mousemove"));
    expect(Number(second()?.split(":")[1])).toBeGreaterThan(Number(frozen?.split(":")[1]));
  });

  it("hiding the overlays with H also stops it", () => {
    const { app, root, press } = setup();
    app.handle(hello());
    press("h");
    expect(root.classList.contains("overlays-hidden")).toBe(true);
    const frozen = second();
    vi.advanceTimersByTime(5000);
    expect(second()).toBe(frozen);
    press("h");
    expect(second()).not.toBe(frozen);
  });

  it("a slider held down keeps the overlay from fading", () => {
    const { app, root } = setup();
    app.handle(hello());
    const slider = /** @type {HTMLElement} */ (root.querySelector(".player-seek"));
    slider.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    vi.advanceTimersByTime(10000);
    expect(root.classList.contains("idle")).toBe(false);
    slider.dispatchEvent(new Event("pointerup", { bubbles: true }));
    vi.advanceTimersByTime(3100);
    expect(root.classList.contains("idle")).toBe(true);
  });

  it("an open queue panel keeps the overlay up like the other panels", () => {
    const { app, root, press } = setup();
    app.handle(hello());
    press("q");
    vi.advanceTimersByTime(10000);
    expect(root.classList.contains("idle")).toBe(false);
  });

  it("a queue panel stays open across the host's repeated queue messages", () => {
    const { app, root, press } = setup();
    app.handle(hello());
    press("q");
    const rows = [...root.querySelectorAll("#queue .queue-list li")];
    for (let i = 0; i < 20; i++) app.handle({ type: "queue", entries: entries.map((e) => ({ ...e })) });
    vi.advanceTimersByTime(100); // an animation frame
    const after = [...root.querySelectorAll("#queue .queue-list li")];
    expect(after.every((r, i) => r === rows[i])).toBe(true);
  });
});
