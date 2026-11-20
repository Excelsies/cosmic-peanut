// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPlayer } from "./player.js";

/** @param {Record<string, any>} [o] @returns {any} */
const np = (o = {}) => ({
  type: "nowPlaying", artist: "Boards of Canada", album: "Geogaddi", title: "Music Is Math", duration: 200,
  position: 10, playing: true, index: 1, count: 3, hostTime: 5, serial: 4, ...o,
});
/** @param {Record<string, any>} [o] */
const entry = (o = {}) => ({ title: "Song", artist: "", album: "", duration: 100, playable: true, ...o });
/** @param {number} [n] */
const entries = (n = 3) => Array.from({ length: n }, (_, i) => entry({ title: `Song ${i}` }));
/** @param {Record<string, any>} [o] @returns {any} */
const hello = (o = {}) => ({
  type: "hello", version: 1, pluginOrigin: "", visualizers: [], repos: [], settings: {}, sources: [],
  activeSource: "player", active: null, dev: false, refreshHz: 0, transport: true, nowPlaying: np(),
  queue: { entries: entries() }, ...o,
});
/** @param {boolean} transport @returns {any} */
const sources = (transport) => ({ type: "sources", sources: [], active: "player", transport });

/** A clock, timers and animation frames that only move when the test says so. */
function fakes() {
  let t = 1000, id = 0;
  /** @type {{ id: number; at: number; fn: () => void }[]} */
  let timers = [];
  /** @type {(() => void)[]} */
  let frames = [];
  return {
    now: () => t,
    setTimer: (/** @type {() => void} */ fn, /** @type {number} */ ms) => (timers.push({ id: ++id, at: t + ms, fn }), id),
    clearTimer: (/** @type {number} */ i) => void (timers = timers.filter((x) => x.id !== i)),
    raf: (/** @type {() => void} */ fn) => (frames.push(fn), frames.length),
    caf: () => void (frames = []),
    pending: () => timers.length,
    advance(/** @type {number} */ ms) {
      const end = t + ms;
      for (;;) {
        const due = timers.filter((x) => x.at <= end).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        timers = timers.filter((x) => x !== due);
        t = Math.max(t, due.at);
        due.fn();
      }
      t = end;
    },
    frame() {
      const run = frames;
      frames = [];
      for (const f of run) f();
    },
    frames: () => frames.length,
  };
}

/** @param {Record<string, any>} [over] */
function setup(over = {}) {
  document.body.innerHTML = '<div id="app" class="shell"><footer class="bottombar"></footer></div>';
  const root = /** @type {HTMLElement} */ (document.getElementById("app"));
  const bar = /** @type {HTMLElement} */ (root.querySelector(".bottombar"));
  const f = fakes();
  const send = vi.fn((/** @type {any} */ _msg) => true);
  const toggleQueue = vi.fn();
  const closePanels = vi.fn();
  const state = { visible: true, width: 1024, room: 400, lift: 6, reserved: 96 };
  /** @type {Set<() => void>} */
  const resizeFns = new Set();
  const watch = { stopped: 0, watching: () => resizeFns.size > 0 };
  const observe = (/** @type {HTMLElement} */ _el, /** @type {() => void} */ fn) => {
    resizeFns.add(fn);
    return () => {
      resizeFns.delete(fn);
      watch.stopped++;
    };
  };
  const resize = () => [...resizeFns].forEach((fn) => fn());
  const player = createPlayer({
    root, bar: () => bar, send, toggleQueue, closePanels, visible: () => state.visible, measure: () => ({ width: state.width, room: state.room, rem: 16, lift: state.lift }), reserved: () => state.reserved, observe, ...f, ...over,
  });
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.hidden = true;
  panel.append(player.queueBody);
  root.append(panel);
  const q = (/** @type {string} */ sel) => /** @type {HTMLElement} */ (root.querySelector(sel));
  const text = (/** @type {string} */ sel) => q(sel)?.textContent;
  const openPanel = () => {
    panel.hidden = false;
    player.renderQueue();
  };
  /** @param {string} key @param {Partial<KeyboardEventInit>} [o] */
  const keyEvent = (key, o = {}) => new KeyboardEvent("keydown", { key, cancelable: true, bubbles: true, ...o });
  return { resize, watch, root, bar, f, send, toggleQueue, closePanels, state, player, panel, q, text, openPanel, keyEvent };
}


describe("player: absent unless the source has a transport", () => {
  it("leaves the DOM alone for a host without transport fields", () => {
    const { player, bar, root, send, keyEvent } = setup();
    player.handle(hello({ transport: false, nowPlaying: null, queue: null }));
    expect(bar.children).toHaveLength(0);
    expect(root.querySelector(".player")).toBeNull();
    for (const action of ["playPause", "seekBack", "seekForward", "trackPrev", "trackNext", "queue"]) {
      const ev = keyEvent("x");
      const spy = vi.spyOn(ev, "preventDefault");
      expect(player.key(/** @type {any} */ (action), ev)).toBe(true); // the app has nothing to do either
      expect(spy).not.toHaveBeenCalled();
    }
    expect(send).not.toHaveBeenCalled();
  });

  it("ignores nowPlaying and queue that arrive without transport", () => {
    const { player, bar, openPanel, q } = setup();
    player.handle(hello({ transport: false, nowPlaying: null, queue: null }));
    player.handle(/** @type {any} */ (np()));
    player.handle({ type: "queue", entries: entries() });
    expect(bar.children).toHaveLength(0);
    openPanel();
    expect(q(".queue-list")?.children.length ?? 0).toBe(0);
  });

  it("shows the row once a transport source reports what is playing, and removes it again", () => {
    const { player, bar, closePanels, openPanel } = setup();
    player.handle(sources(true));
    expect(bar.children).toHaveLength(0); // nothing known yet
    player.handle(/** @type {any} */ (np()));
    expect(bar.querySelector(".player")).not.toBeNull();
    openPanel();
    player.handle(sources(false));
    expect(bar.children).toHaveLength(0);
    expect(closePanels).toHaveBeenCalledTimes(1);
  });

  it("does not close an unrelated panel when the transport goes away", () => {
    const { player, closePanels } = setup();
    player.handle(hello());
    player.handle(sources(false));
    expect(closePanels).not.toHaveBeenCalled();
  });

  it("keeps its state across a sources refresh that still has transport", () => {
    const { player, text } = setup();
    player.handle(hello());
    player.handle(sources(true));
    expect(text(".player-title")).toBe("Music Is Math");
  });

  it("a reloaded shell gets everything from hello", () => {
    const { player, text, q } = setup();
    player.handle(hello({ nowPlaying: np({ playing: false, position: 33 }) }));
    expect(text(".player-elapsed")).toBe("0:33");
    expect(q(".player-play").getAttribute("aria-label")).toBe("Play");
  });
});

describe("player: the row's accessible name carries the track", () => {
  const label = (/** @type {HTMLElement} */ el) => el.querySelector(".player")?.getAttribute("aria-label");

  it("names the title and the band, and follows the track", () => {
    const { player, root } = setup();
    player.handle(hello());
    expect(label(root)).toBe("Player: Music Is Math, Boards of Canada");
    player.handle(/** @type {any} */ (np({ title: "Next one", artist: "Other", serial: 5 })));
    expect(label(root)).toBe("Player: Next one, Other");
  });

  it("is correct without an artist: just the title, no stray comma", () => {
    const { player, root } = setup();
    player.handle(hello({ nowPlaying: np({ artist: "", album: "" }) }));
    expect(label(root)).toBe("Player: Music Is Math");
  });

  it("is plain 'Player' for an empty queue", () => {
    const { player, root } = setup();
    player.handle(hello({ nowPlaying: np({ index: null, count: 0, title: "", artist: "", album: "" }), queue: { entries: [] } }));
    expect(label(root)).toBe("Player");
  });

  it("is still there when the visible title line is given up (fit levels 1 and 2)", () => {
    const { player, root, state, resize, q } = setup();
    player.handle(hello());
    for (const [room, fit] of [[60, "1"], [22, "2"]]) {
      state.room = /** @type {number} */ (room);
      resize();
      expect(q(".player").getAttribute("data-fit")).toBe(fit);
      expect(label(root)).toBe("Player: Music Is Math, Boards of Canada");
    }
    player.handle(/** @type {any} */ (np({ title: "Changed while squeezed", artist: "", serial: 6 })));
    expect(label(root)).toBe("Player: Changed while squeezed");
  });

  it("takes markup as text: an attribute, never HTML", () => {
    const { player, root } = setup();
    player.handle(hello({ nowPlaying: np({ title: '"><img src=x onerror=alert(1)>', artist: "<b>" }) }));
    expect(root.querySelector(".player img")).toBeNull();
    expect(label(root)).toBe('Player: "><img src=x onerror=alert(1)>, <b>');
  });
});

describe("player: now-playing text", () => {
  it("shows title, band and album", () => {
    const { player, text } = setup();
    player.handle(hello());
    expect(text(".player-title")).toBe("Music Is Math");
    expect(text(".player-band")).toBe("Boards of Canada");
    expect(text(".player-album")).toBe("Geogaddi");
  });

  it("untagged files have a title only: nothing else, no placeholders", () => {
    const { player, q } = setup();
    player.handle(hello({ nowPlaying: np({ title: "01 - track", artist: "", album: "" }) }));
    expect(q(".player-now").textContent).toBe("01 - track");
    expect(q(".player-band").hidden).toBe(true);
    expect(q(".player-album").hidden).toBe(true);
    expect(q(".player").textContent).not.toMatch(/unknown|—|-{2}|n\/a/i);
  });

  it("carries the full text in title attributes", () => {
    const { player, q } = setup();
    player.handle(hello({ nowPlaying: np({ title: "T".repeat(120) }) }));
    expect(q(".player-title").getAttribute("title")).toBe("T".repeat(120));
    expect(q(".player-band").getAttribute("title")).toBe("Boards of Canada");
  });

  it("renders markup in tags as literal text", () => {
    const { player, q, root } = setup();
    player.handle(hello({ nowPlaying: np({ title: "<img src=x onerror=alert(1)>", artist: "<b>x</b>", album: "<script>1</script>" }) }));
    expect(root.querySelector(".player img, .player b, .player script")).toBeNull();
    expect(q(".player-title").textContent).toBe("<img src=x onerror=alert(1)>");
    expect(q(".player-band").textContent).toBe("<b>x</b>");
    expect(q(".player-album").getAttribute("title")).toBe("<script>1</script>");
  });

  it("follows the next track", () => {
    const { player, text } = setup();
    player.handle(hello());
    player.handle(/** @type {any} */ (np({ title: "Next one", artist: "", index: 2 })));
    expect(text(".player-title")).toBe("Next one");
    expect(text(".player-band")).toBe("");
  });
});

describe("player: buttons", () => {
  it("previous, play/pause and next send transport messages", () => {
    const { player, q, send } = setup();
    player.handle(hello());
    q(".player-prev").click();
    q(".player-play").click();
    q(".player-next").click();
    expect(send.mock.calls.map((c) => c[0])).toEqual([
      { type: "transport", action: "previous" },
      { type: "transport", action: "toggle" },
      { type: "transport", action: "next" },
    ]);
  });

  it("the play button names what a press does", () => {
    const { player, q } = setup();
    player.handle(hello());
    expect(q(".player-play").getAttribute("aria-label")).toBe("Pause");
    player.handle(/** @type {any} */ (np({ playing: false })));
    expect(q(".player-play").getAttribute("aria-label")).toBe("Play");
  });

  it("real, labelled controls", () => {
    const { player, q } = setup();
    player.handle(hello());
    for (const sel of [".player-prev", ".player-play", ".player-next", ".player-queue"]) {
      expect(q(sel).tagName).toBe("BUTTON");
      expect(q(sel).getAttribute("type")).toBe("button");
      expect(q(sel).getAttribute("aria-label")).toBeTruthy();
    }
    const seek = /** @type {HTMLInputElement} */ (q(".player-seek"));
    expect(seek.type).toBe("range");
    expect(seek.getAttribute("aria-label")).toBe("Seek");
  });

  it("the Queue button asks the app to toggle the panel; it is absent when the source has no queue", () => {
    const a = setup();
    a.player.handle(hello());
    a.q(".player-queue").click();
    expect(a.toggleQueue).toHaveBeenCalledTimes(1);
    const b = setup();
    b.player.handle(hello({ queue: null }));
    expect(b.q(".player-queue").hidden).toBe(true);
  });
});

describe("player: position", () => {
  beforeEach(() => vi.useRealTimers());

  it("extrapolates from the shell's own receive time while playing", () => {
    const { player, f, text } = setup();
    player.handle(hello()); // position 10
    expect(text(".player-elapsed")).toBe("0:10");
    f.advance(2500);
    expect(text(".player-elapsed")).toBe("0:12");
    f.advance(1000);
    expect(text(".player-elapsed")).toBe("0:13");
  });

  it("a fresh nowPlaying resets the base", () => {
    const { player, f, text } = setup();
    player.handle(hello());
    f.advance(3000);
    player.handle(/** @type {any} */ (np({ position: 50 })));
    expect(text(".player-elapsed")).toBe("0:50");
    f.advance(1000);
    expect(text(".player-elapsed")).toBe("0:51");
  });

  it("is still while paused, with no timer at all", () => {
    const { player, f, text } = setup();
    player.handle(hello({ nowPlaying: np({ playing: false, position: 42 }) }));
    expect(f.pending()).toBe(0);
    f.advance(60000);
    expect(text(".player-elapsed")).toBe("0:42");
  });

  it("clamps to a known duration and never decides the track is over", () => {
    const { player, f, text, send } = setup();
    player.handle(hello({ nowPlaying: np({ position: 198, duration: 200 }) }));
    f.advance(30000);
    expect(text(".player-elapsed")).toBe("3:20");
    expect(f.pending()).toBe(0); // nothing more to show until the host says something
    expect(send).not.toHaveBeenCalled();
  });

  it("an unknown duration: slider disabled, elapsed time still runs, no total", () => {
    const { player, f, q, text } = setup();
    player.handle(hello({ nowPlaying: np({ duration: null, position: 5 }) }));
    expect(/** @type {HTMLInputElement} */ (q(".player-seek")).disabled).toBe(true);
    expect(q(".player-total").hidden).toBe(true);
    f.advance(3000);
    expect(text(".player-elapsed")).toBe("0:08");
  });

  it("a known duration enables the slider and shows the total", () => {
    const { player, q, text } = setup();
    player.handle(hello());
    const seek = /** @type {HTMLInputElement} */ (q(".player-seek"));
    expect(seek.disabled).toBe(false);
    expect(seek.max).toBe("200");
    expect(Number(seek.value)).toBe(10);
    expect(text(".player-total")).toBe("3:20");
  });

  it("ticks only when the shown second changes", () => {
    const { player, f, q } = setup();
    player.handle(hello({ nowPlaying: np({ position: 10.25 }) }));
    const obs = new MutationObserver(() => {});
    obs.observe(q(".player"), { subtree: true, childList: true, attributes: true, characterData: true });
    f.advance(700);
    expect(obs.takeRecords()).toHaveLength(0);
    f.advance(100); // 10.25 + 0.8 = 11.05
    expect(obs.takeRecords().length).toBeGreaterThan(0);
  });

  it("a host update that changes nothing visible writes nothing", () => {
    const { player, q } = setup();
    player.handle(hello({ nowPlaying: np({ position: 10.1 }) }));
    const obs = new MutationObserver(() => {});
    obs.observe(q(".player"), { subtree: true, childList: true, attributes: true, characterData: true });
    player.handle(/** @type {any} */ (np({ position: 10.3, hostTime: 99 })));
    expect(obs.takeRecords()).toHaveLength(0);
  });
});

describe("player: the display timer stops when nobody can see it", () => {
  it("not while the overlays are faded out; resumes (and catches up) when they return", () => {
    const { player, f, root, text } = setup();
    player.handle(hello());
    expect(f.pending()).toBe(1);
    root.classList.add("idle");
    player.sync();
    expect(f.pending()).toBe(0);
    f.advance(5000);
    expect(text(".player-elapsed")).toBe("0:10"); // nothing was written
    root.classList.remove("idle");
    player.sync();
    expect(text(".player-elapsed")).toBe("0:15");
    expect(f.pending()).toBe(1);
  });

  it("not while the shell is hidden", () => {
    const { player, f, state } = setup();
    player.handle(hello());
    state.visible = false;
    player.sync();
    expect(f.pending()).toBe(0);
    state.visible = true;
    player.sync();
    expect(f.pending()).toBe(1);
  });

  it("not with the overlays hidden by key", () => {
    const { player, f, root } = setup();
    player.handle(hello());
    root.classList.add("overlays-hidden");
    player.sync();
    expect(f.pending()).toBe(0);
  });

  it("a track change while faded or hidden still updates the title text and the accessible name", () => {
    const { player, root, q, state } = setup();
    player.handle(hello());
    root.classList.add("idle");
    player.sync();
    player.handle(/** @type {any} */ (np({ title: "Hidden change", artist: "Someone", serial: 5 })));
    expect(q(".player-title").textContent).toBe("Hidden change");
    expect(q(".player-band").textContent).toBe("Someone");
    expect(q(".player").getAttribute("aria-label")).toBe("Player: Hidden change, Someone");
    state.visible = false;
    player.sync();
    player.handle(/** @type {any} */ (np({ title: "While hidden", artist: "", serial: 6 })));
    expect(q(".player").getAttribute("aria-label")).toBe("Player: While hidden");
  });

  it("while faded the clock, the play state and the slider are still not written", () => {
    const { player, root, q, f } = setup();
    player.handle(hello());
    root.classList.add("idle");
    player.sync();
    player.handle(/** @type {any} */ (np({ playing: false, position: 99, serial: 5 })));
    expect(q(".player-elapsed").textContent).toBe("0:10");
    expect(q(".player-play").getAttribute("aria-label")).toBe("Pause");
    expect(f.pending()).toBe(0);
    root.classList.remove("idle");
    player.sync();
    expect(q(".player-elapsed").textContent).toBe("1:39");
  });

  it("pausing cancels the timer; playing again starts it", () => {
    const { player, f } = setup();
    player.handle(hello());
    player.handle(/** @type {any} */ (np({ playing: false })));
    expect(f.pending()).toBe(0);
    player.handle(/** @type {any} */ (np({ playing: true })));
    expect(f.pending()).toBe(1);
  });
});

describe("player: seeking", () => {
  /** @param {HTMLElement} el @param {number} v */
  const drag = (el, v) => {
    const i = /** @type {HTMLInputElement} */ (el);
    i.value = String(v);
    i.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const release = (/** @type {HTMLElement} */ el) => el.dispatchEvent(new Event("change", { bubbles: true }));

  it("dragging updates the display only; release sends one seek", () => {
    const { player, q, send, text } = setup();
    player.handle(hello());
    const seek = q(".player-seek");
    drag(seek, 60);
    drag(seek, 90);
    expect(text(".player-elapsed")).toBe("1:30");
    expect(send).not.toHaveBeenCalled();
    release(seek);
    expect(send.mock.calls).toEqual([[{ type: "transport", action: "seek", value: 90, serial: 4 }]]);
  });

  it("ignores host position updates while dragging, but not other changes", () => {
    const { player, q, text } = setup();
    player.handle(hello());
    const seek = q(".player-seek");
    drag(seek, 120);
    player.handle(/** @type {any} */ (np({ position: 11, playing: false })));
    expect(text(".player-elapsed")).toBe("2:00");
    expect(Number(/** @type {HTMLInputElement} */ (seek).value)).toBe(120);
    expect(q(".player-play").getAttribute("aria-label")).toBe("Play");
  });

  it("a track change during a drag ends the drag: the seek would be for another track", () => {
    const { player, q, text, send } = setup();
    player.handle(hello());
    drag(q(".player-seek"), 120);
    player.handle(/** @type {any} */ (np({ serial: 5, position: 0, title: "Next" })));
    expect(text(".player-elapsed")).toBe("0:00");
    release(q(".player-seek")); // the browser's change after the slider was reset
    expect(send.mock.calls.every((c) => c[0].serial !== 4)).toBe(true);
  });

  it("shows the target after release, even paused when the host will not report a position", () => {
    const { player, q, text, f } = setup();
    player.handle(hello({ nowPlaying: np({ playing: false, position: 10 }) }));
    drag(q(".player-seek"), 100);
    release(q(".player-seek"));
    f.advance(5000);
    expect(text(".player-elapsed")).toBe("1:40");
    // and the host's reports are followed again
    player.handle(/** @type {any} */ (np({ playing: false, position: 101 })));
    expect(text(".player-elapsed")).toBe("1:41");
  });

  it("a drag that never ends in a change (focus lost) stops suppressing the host", () => {
    const { player, q, text } = setup();
    player.handle(hello());
    drag(q(".player-seek"), 120);
    q(".player-seek").dispatchEvent(new Event("blur"));
    player.handle(/** @type {any} */ (np({ position: 30 })));
    expect(text(".player-elapsed")).toBe("0:30");
  });

  it("holds the overlay while the slider is pressed or dragged", () => {
    const { player, q } = setup();
    player.handle(hello());
    expect(player.holdsOverlay()).toBe(false);
    q(".player-seek").dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(player.holdsOverlay()).toBe(true);
    q(".player-seek").dispatchEvent(new Event("pointerup", { bubbles: true }));
    expect(player.holdsOverlay()).toBe(false);
    drag(q(".player-seek"), 50);
    expect(player.holdsOverlay()).toBe(true);
    release(q(".player-seek"));
    expect(player.holdsOverlay()).toBe(false);
  });

  it("a release outside the slider, or the window losing focus, cannot leave the overlay held", () => {
    const { player, q } = setup();
    player.handle(hello());
    q(".player-seek").dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(player.holdsOverlay()).toBe(true);
    document.body.dispatchEvent(new Event("pointerup", { bubbles: true })); // released elsewhere
    expect(player.holdsOverlay()).toBe(false);
    q(".player-seek").dispatchEvent(new Event("pointerdown", { bubbles: true }));
    document.body.dispatchEvent(new Event("pointercancel", { bubbles: true }));
    expect(player.holdsOverlay()).toBe(false);
    q(".player-seek").dispatchEvent(new Event("pointerdown", { bubbles: true }));
    window.dispatchEvent(new Event("blur"));
    expect(player.holdsOverlay()).toBe(false);
  });

  it("the drag does not run the display timer", () => {
    const { player, q, f } = setup();
    player.handle(hello());
    drag(q(".player-seek"), 50);
    expect(f.pending()).toBe(0);
    release(q(".player-seek"));
    expect(f.pending()).toBe(1);
  });
});

describe("player: keys", () => {
  it("Space toggles and stops the default", () => {
    const { player, send, keyEvent } = setup();
    player.handle(hello());
    const ev = keyEvent(" ");
    expect(player.key("playPause", ev)).toBe(true);
    expect(ev.defaultPrevented).toBe(true);
    expect(send).toHaveBeenCalledWith({ type: "transport", action: "toggle" });
  });

  it("Left and Right seek 5 s from where the display is", () => {
    const { player, send, f, keyEvent } = setup();
    player.handle(hello());
    f.advance(2000); // 12 s
    player.key("seekBack", keyEvent("ArrowLeft"));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(send.mock.calls.map((c) => c[0])).toEqual([
      { type: "transport", action: "seek", value: 7, serial: 4 },
      { type: "transport", action: "seek", value: 12, serial: 4 },
    ]);
  });

  it("every seek names the track load its position came from", () => {
    const { player, send, keyEvent, q } = setup();
    player.handle(hello({ nowPlaying: np({ serial: 9 }) }));
    player.key("seekForward", keyEvent("ArrowRight"));
    const seek = /** @type {HTMLInputElement} */ (q(".player-seek"));
    seek.value = "50";
    seek.dispatchEvent(new Event("input", { bubbles: true }));
    seek.dispatchEvent(new Event("change", { bubbles: true }));
    player.handle(/** @type {any} */ (np({ serial: 10, position: 0 })));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(send.mock.calls.map((c) => c[0].serial)).toEqual([9, 9, 10]);
  });

  it("an optimistic seek keeps the serial, so a second key press in a row is still the same load", () => {
    const { player, send, keyEvent } = setup();
    player.handle(hello({ nowPlaying: np({ serial: 9, position: 10 }) }));
    player.key("seekForward", keyEvent("ArrowRight"));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(send.mock.calls.map((c) => [c[0].value, c[0].serial])).toEqual([[15, 9], [20, 9]]);
  });

  it("a forward seek does nothing when the position is already at the duration", () => {
    const { player, send, keyEvent } = setup();
    player.handle(hello({ nowPlaying: np({ position: 200, duration: 200 }) }));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(send).not.toHaveBeenCalled();
    player.key("seekBack", keyEvent("ArrowLeft")); // back still works
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("seeks clamp at 0 and at a known duration", () => {
    const { player, send, keyEvent } = setup();
    player.handle(hello({ nowPlaying: np({ position: 2 }) }));
    player.key("seekBack", keyEvent("ArrowLeft"));
    expect(send).toHaveBeenLastCalledWith({ type: "transport", action: "seek", value: 0, serial: 4 });
    player.handle(/** @type {any} */ (np({ position: 198 })));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(send).toHaveBeenLastCalledWith({ type: "transport", action: "seek", value: 200, serial: 4 });
  });

  it("seeks without a duration are not capped", () => {
    const { player, send, keyEvent } = setup();
    player.handle(hello({ nowPlaying: np({ duration: null, position: 1000 }) }));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(send).toHaveBeenLastCalledWith({ type: "transport", action: "seek", value: 1005, serial: 4 });
  });

  it("a key seek is shown at once even when paused", () => {
    const { player, text, keyEvent } = setup();
    player.handle(hello({ nowPlaying: np({ playing: false, position: 30 }) }));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(text(".player-elapsed")).toBe("0:35");
  });

  it("comma and period go to the previous and next track", () => {
    const { player, send, keyEvent } = setup();
    player.handle(hello());
    player.key("trackPrev", keyEvent(","));
    player.key("trackNext", keyEvent("."));
    expect(send.mock.calls.map((c) => c[0])).toEqual([
      { type: "transport", action: "previous" },
      { type: "transport", action: "next" },
    ]);
  });

  it("q toggles the queue panel when the source has a queue", () => {
    const a = setup();
    a.player.handle(hello());
    const ev = a.keyEvent("q");
    a.player.key("queue", ev);
    expect(a.toggleQueue).toHaveBeenCalledTimes(1);
    expect(ev.defaultPrevented).toBe(true);
    const b = setup();
    b.player.handle(hello({ queue: null }));
    const ev2 = b.keyEvent("q");
    expect(b.player.key("queue", ev2)).toBe(true);
    expect(b.toggleQueue).not.toHaveBeenCalled();
    expect(ev2.defaultPrevented).toBe(false);
  });

  it("with an empty queue, nothing is sent but q still opens the queue", () => {
    const { player, send, toggleQueue, keyEvent } = setup();
    player.handle(hello({ nowPlaying: np({ index: null, count: 0, title: "" }), queue: { entries: [] } }));
    for (const a of ["playPause", "seekBack", "seekForward", "trackPrev", "trackNext"]) {
      const ev = keyEvent("x");
      expect(player.key(/** @type {any} */ (a), ev)).toBe(true);
      expect(ev.defaultPrevented).toBe(false);
    }
    expect(send).not.toHaveBeenCalled();
    player.key("queue", keyEvent("q"));
    expect(toggleQueue).toHaveBeenCalledTimes(1);
  });

  it("does not claim actions that are not its own", () => {
    const { player, keyEvent } = setup();
    player.handle(hello());
    expect(player.key("next", keyEvent("n"))).toBe(false);
    expect(player.key("library", keyEvent("l"))).toBe(false);
    expect(player.key("escape", keyEvent("Escape"))).toBe(false);
  });
});

describe("player: layout", () => {
  it("collapses album, then band, then the time labels as the row narrows", () => {
    const { player, q, state, resize } = setup();
    player.handle(hello());
    expect(q(".player").getAttribute("data-collapse")).toBe("0");
    for (const [w, level] of [[700, "1"], [500, "2"], [320, "3"]]) {
      state.width = /** @type {number} */ (w);
      resize();
      expect(q(".player").getAttribute("data-collapse")).toBe(level);
    }
  });

  it("is sized for the width it appears in", () => {
    const { player, q, state } = setup();
    state.width = 320;
    player.handle(hello());
    expect(q(".player").getAttribute("data-collapse")).toBe("3");
  });

  it("marks the shell while the row is there, so CSS changes nothing for a host without it", () => {
    const { player, root } = setup();
    expect(root.classList.contains("has-player")).toBe(false);
    player.handle(hello({ transport: false, nowPlaying: null, queue: null }));
    expect(root.classList.contains("has-player")).toBe(false);
    expect(root.style.getPropertyValue("--player-bar")).toBe("");
    player.handle(hello());
    expect(root.classList.contains("has-player")).toBe(true);
    player.handle(sources(false));
    expect(root.classList.contains("has-player")).toBe(false);
    expect(root.style.getPropertyValue("--player-bar")).toBe("");
  });

  it("publishes how much of the window's bottom the row takes, for the queue drawer to clear", () => {
    const { player, root, state, resize, watch } = setup();
    player.handle(hello());
    expect(root.style.getPropertyValue("--player-bar")).toBe("96px");
    state.reserved = 58;
    resize();
    expect(root.style.getPropertyValue("--player-bar")).toBe("58px");
    expect(watch.watching()).toBe(true);
    player.handle(sources(false));
    expect(watch.watching()).toBe(false);
    expect(watch.stopped).toBeGreaterThan(0);
  });

  it("gives up the title line, then the buttons' padding, as the room between the bars shrinks", () => {
    const { player, q, state, resize } = setup();
    player.handle(hello());
    expect(q(".player").getAttribute("data-fit")).toBe("0");
    state.room = 60;
    resize();
    expect(q(".player").getAttribute("data-fit")).toBe("1");
    state.room = 22;
    resize();
    expect(q(".player").getAttribute("data-fit")).toBe("2");
    state.room = 500;
    resize();
    expect(q(".player").getAttribute("data-fit")).toBe("0");
  });

  it("with no room even for the tight row, shows none; it comes back with the room", () => {
    const { player, q, root, state, resize } = setup();
    player.handle(hello());
    state.room = 10;
    resize();
    expect(q(".player").getAttribute("data-fit")).toBe("3");
    expect(root.style.getPropertyValue("--player-bar")).toBe(""); // the drawer's offset is the one without a row
    state.room = 30;
    resize();
    expect(q(".player").getAttribute("data-fit")).toBe("2");
    expect(root.style.getPropertyValue("--player-bar")).toBe("96px");
  });

  it("does not flap at the boundary: the same room gives the same answer however often it is observed", () => {
    const { player, q, state, resize } = setup();
    player.handle(hello());
    state.room = 20.5; // 1.3 rem = 20.8
    const seen = new Set();
    for (let i = 0; i < 6; i++) {
      resize();
      seen.add(q(".player").getAttribute("data-fit"));
      state.reserved = i % 2 ? 0 : 96; // the row's own size is not an input
    }
    expect([...seen]).toEqual(["3"]);
  });

  it("hidden for lack of room, the keys still work and the clock stops", () => {
    const { player, f, state, resize, send, keyEvent } = setup();
    player.handle(hello());
    state.room = 5;
    resize();
    expect(f.pending()).toBe(0);
    player.key("playPause", keyEvent(" "));
    player.key("trackNext", keyEvent("."));
    player.key("seekForward", keyEvent("ArrowRight"));
    expect(send.mock.calls.map((c) => c[0].action)).toEqual(["toggle", "next", "seek"]);
    state.room = 400;
    resize();
    expect(f.pending()).toBe(1); // and the clock runs again
  });

  it("moves focus out of the row when it hides, to the bar's own controls, not to the page", () => {
    const { player, bar, q, state, resize } = setup();
    const own = document.createElement("button");
    bar.append(own);
    player.handle(hello());
    q(".player-play").focus();
    expect(document.activeElement).toBe(q(".player-play"));
    state.room = 5;
    resize();
    expect(document.activeElement).toBe(own);
  });

  it("leaves focus alone when it is elsewhere", () => {
    const { player, bar, q, state, resize } = setup();
    const own = document.createElement("button");
    bar.append(own);
    const other = document.createElement("button");
    document.body.append(other);
    player.handle(hello());
    other.focus();
    state.room = 5;
    resize();
    expect(document.activeElement).toBe(other);
    expect(q(".player").getAttribute("data-fit")).toBe("3");
  });

  it("sits its bottom edge just above the existing controls, wherever they are", () => {
    const { player, q, state, resize } = setup();
    player.handle(hello());
    expect(/** @type {HTMLElement} */ (q(".player")).style.bottom).toBe("10px"); // 6 + a quarter rem
    state.lift = 37.4;
    resize();
    expect(/** @type {HTMLElement} */ (q(".player")).style.bottom).toBe("41px");
    state.room = 22; // every pixel counts: no air
    resize();
    expect(/** @type {HTMLElement} */ (q(".player")).style.bottom).toBe("37px");
  });

  it("watches the window and both bars and the row, so any of them resizing re-lays it out", () => {
    const els = /** @type {HTMLElement[]} */ ([]);
    const { player } = setup({ observe: (/** @type {HTMLElement} */ el) => (els.push(el), () => {}) });
    player.handle(hello());
    expect(els.map((e) => e.className.split(" ")[0])).toEqual(expect.arrayContaining(["shell", "bottombar", "player"]));
  });

  it("observes once however many messages arrive", () => {
    const { player, watch } = setup();
    player.handle(hello());
    for (let i = 0; i < 5; i++) player.handle(/** @type {any} */ (np()));
    expect(watch.stopped).toBe(0);
  });
});

describe("player: empty queue", () => {
  const empty = () => hello({ nowPlaying: np({ index: null, count: 0, title: "", artist: "", album: "", duration: null, position: 0, playing: false }), queue: { entries: [] } });

  it("offers a single Open files… instead of dead controls", () => {
    const { player, q, send } = setup();
    player.handle(empty());
    const open = q(".player-open");
    expect(open.hidden).toBe(false);
    expect(open.textContent).toBe("Open files…");
    expect(q(".player-controls").hidden).toBe(true);
    expect(q(".player-now").hidden).toBe(true);
    open.click();
    expect(send).toHaveBeenCalledExactlyOnceWith({ type: "playerOpen", kind: "files", replace: true });
  });

  it("offers Open folder… next to it, also a replace", () => {
    const { player, q, send } = setup();
    player.handle(empty());
    expect(q(".player-open-folder").hidden).toBe(false);
    expect(q(".player-open-folder").textContent).toBe("Open folder…");
    q(".player-open-folder").click();
    expect(send).toHaveBeenCalledExactlyOnceWith({ type: "playerOpen", kind: "folder", replace: true });
  });

  it("neither open button is on screen while there is a queue", () => {
    const { player, q } = setup();
    player.handle(hello());
    expect(q(".player-empty").hidden).toBe(true);
    expect(q(".player-open").hidden).toBe(true);
    expect(q(".player-open-folder").hidden).toBe(true);
  });

  it("goes back to the controls once the queue fills", () => {
    const { player, q } = setup();
    player.handle(empty());
    player.handle(/** @type {any} */ (np()));
    expect(q(".player-open").hidden).toBe(true);
    expect(q(".player-controls").hidden).toBe(false);
  });
});

describe("player: queue panel", () => {
  it("renders nothing until opened", () => {
    const { player, q } = setup();
    player.handle(hello());
    expect(q(".queue-list")).toBeNull();
  });

  it("opening renders the whole queue, current entry marked", () => {
    const { player, q, openPanel, root } = setup();
    player.handle(hello({ queue: { entries: [entry({ title: "A", artist: "X", album: "Y", duration: 61 }), entry({ title: "B" }), entry({ title: "C" })] } }));
    openPanel();
    const rows = [...root.querySelectorAll(".queue-list li")];
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("A");
    expect(rows[0].textContent).toContain("X");
    expect(rows[0].textContent).toContain("Y");
    expect(rows[0].textContent).toContain("1:01");
    expect(rows[1].querySelector("button")?.getAttribute("aria-current")).toBe("true");
    expect(q(".queue-list")).not.toBeNull();
  });

  it("repeated queue messages while open: one render per animation frame, rows are reused", () => {
    const { player, f, openPanel, root } = setup();
    player.handle(hello());
    openPanel();
    const before = [...root.querySelectorAll(".queue-list li")];
    for (let i = 0; i < 10; i++) player.handle({ type: "queue", entries: entries() });
    expect(f.frames()).toBe(1);
    const obs = new MutationObserver(() => {});
    obs.observe(root.querySelector(".queue-list") ?? root, { subtree: true, childList: true, attributes: true, characterData: true });
    f.frame();
    expect(obs.takeRecords()).toHaveLength(0); // same content: nothing written
    const after = [...root.querySelectorAll(".queue-list li")];
    expect(after.every((r, i) => r === before[i])).toBe(true);
  });

  it("tags filling in update only their rows", () => {
    const { player, f, openPanel, root } = setup();
    player.handle(hello());
    openPanel();
    const before = [...root.querySelectorAll(".queue-list li")];
    player.handle({ type: "queue", entries: entries().map((e, i) => (i === 2 ? { ...e, artist: "Found" } : e)) });
    f.frame();
    const after = [...root.querySelectorAll(".queue-list li")];
    expect(after.every((r, i) => r === before[i])).toBe(true);
    expect(after[2].textContent).toContain("Found");
  });

  it("while closed, messages cost no render; the latest shows on open", () => {
    const { player, f, openPanel, root } = setup();
    player.handle(hello());
    for (let i = 0; i < 5; i++) player.handle({ type: "queue", entries: entries(4 + i) });
    expect(f.frames()).toBe(0);
    openPanel();
    expect(root.querySelectorAll(".queue-list li")).toHaveLength(8);
  });

  it("the current marker follows the playing track", () => {
    const { player, f, openPanel, root } = setup();
    player.handle(hello());
    openPanel();
    player.handle(/** @type {any} */ (np({ index: 2 })));
    f.frame();
    const rows = [...root.querySelectorAll(".queue-list li")];
    expect(rows.map((r) => r.querySelector("button")?.getAttribute("aria-current") ?? null)).toEqual([null, null, "true"]);
  });

  it("the once-a-second position tick does not schedule a render", () => {
    const { player, f, openPanel } = setup();
    player.handle(hello());
    openPanel();
    player.handle(/** @type {any} */ (np({ position: 11 })));
    expect(f.frames()).toBe(0);
  });

  it("clicking a playable entry sends playerSelect with its index", () => {
    const { player, send, openPanel, root } = setup();
    player.handle(hello({ queue: { entries: [entry(), entry(), entry({ playable: false })] } }));
    openPanel();
    /** @type {HTMLElement} */ (root.querySelectorAll(".queue-list button")[1]).click();
    /** @type {HTMLElement} */ (root.querySelectorAll(".queue-list button")[2]).click();
    expect(send.mock.calls).toEqual([[{ type: "playerSelect", index: 1 }]]);
  });

  it("an unplayable entry is visibly disabled", () => {
    const { player, openPanel, root } = setup();
    player.handle(hello({ queue: { entries: [entry({ playable: false })] } }));
    openPanel();
    expect(root.querySelector(".queue-list li")?.classList.contains("is-unplayable")).toBe(true);
    expect(root.querySelector(".queue-list button")?.getAttribute("aria-disabled")).toBe("true");
  });

  it("has open, add and clear buttons with the documented messages", () => {
    const { player, send, openPanel, q } = setup();
    player.handle(hello());
    openPanel();
    for (const sel of [".q-open-files", ".q-open-folder", ".q-add-files", ".q-add-folder", ".q-clear"]) q(sel).click();
    expect(send.mock.calls.map((c) => c[0])).toEqual([
      { type: "playerOpen", kind: "files", replace: true },
      { type: "playerOpen", kind: "folder", replace: true },
      { type: "playerOpen", kind: "files", replace: false },
      { type: "playerOpen", kind: "folder", replace: false },
      { type: "playerClear" },
    ]);
  });

  it("with no entries it says so, and Clear is off", () => {
    const { player, openPanel, q } = setup();
    player.handle(hello({ nowPlaying: np({ index: null, count: 0 }), queue: { entries: [] } }));
    openPanel();
    expect(q(".queue-empty").hidden).toBe(false);
    expect(/** @type {HTMLButtonElement} */ (q(".q-clear")).disabled).toBe(true);
    player.handle({ type: "queue", entries: entries() });
    // (open panel: the render is one frame away)
  });

  it("the empty note goes away and Clear comes on once there are entries", () => {
    const { player, f, openPanel, q } = setup();
    player.handle(hello({ nowPlaying: np({ index: null, count: 0 }), queue: { entries: [] } }));
    openPanel();
    player.handle({ type: "queue", entries: entries() });
    f.frame();
    expect(q(".queue-empty").hidden).toBe(true);
    expect(/** @type {HTMLButtonElement} */ (q(".q-clear")).disabled).toBe(false);
  });

  it("Clear leaves focus in the panel, not on the page", () => {
    const { player, f, openPanel, q } = setup();
    player.handle(hello());
    openPanel();
    q(".q-clear").focus();
    q(".q-clear").click();
    player.handle({ type: "queue", entries: [] });
    player.handle(/** @type {any} */ (np({ index: null, count: 0 })));
    f.frame();
    expect(/** @type {HTMLButtonElement} */ (q(".q-clear")).disabled).toBe(true);
    expect(document.activeElement).toBe(q(".q-open-files"));
  });

  it("a transport without a queue gets a panel without the queue buttons' dead ends", () => {
    const { player, openPanel, q } = setup();
    player.handle(hello({ queue: null }));
    openPanel();
    expect(q(".queue-empty").hidden).toBe(false);
  });

  it("queue text from the host is text", () => {
    const { player, openPanel, root } = setup();
    player.handle(hello({ queue: { entries: [entry({ title: "<img src=x onerror=alert(1)>" })] } }));
    openPanel();
    expect(root.querySelector(".queue-list img")).toBeNull();
    expect(root.querySelector(".q-title")?.textContent).toBe("<img src=x onerror=alert(1)>");
  });
});
