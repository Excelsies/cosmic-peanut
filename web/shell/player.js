// The file player's UI: a transport row in the bottom overlay and a queue panel, both driven by
// the host's `nowPlaying` / `queue` messages (docs/protocols.md §4). Present only while the active
// source reports `transport: true`; otherwise nothing of it is in the DOM. All host text goes into
// the DOM as text. The app only wires it in: see the `player` lines in app.js.

import { h } from "./dom.js";
import { collapseLevel, fitLevel, formatTime, msToNextSecond, positionAt } from "./playerClock.js";
import { createQueueList } from "./queueList.js";

/** @typedef {import("./validate.js").NowPlaying} NowPlaying */
/** @typedef {import("./validate.js").Queue} Queue */
/** @typedef {import("./validate.js").HostMessage} HostMessage */
/** @typedef {import("./keys.js").KeyAction} KeyAction */
/**
 * @typedef {object} PlayerDeps
 * @property {HTMLElement} root the shell root: its `idle` / `overlays-hidden` classes say whether the overlays show
 * @property {() => HTMLElement} bar the overlay the transport row is put into (looked up when needed)
 * @property {(msg: object) => boolean} send
 * @property {() => void} toggleQueue open or close the queue panel
 * @property {() => void} closePanels
 * @property {() => boolean} visible whether the window is on screen
 * @property {() => number} [now] ms on any steady clock (default performance.now)
 * @property {(fn: () => void, ms: number) => unknown} [setTimer]
 * @property {(id: any) => void} [clearTimer]
 * @property {(fn: () => void) => unknown} [raf]
 * @property {(id: any) => void} [caf]
 * @property {() => { width: number; room: number; rem: number; lift: number }} [measure] the layout's inputs, px:
 *   the row's width, the free height between the top bar's controls and the existing bottom controls,
 *   px per rem, and how far the existing controls' top edge sits above the bar's bottom edge. Default: read from the DOM.
 * @property {() => number} [reserved] how much of the window's height the row and the controls under it take
 * @property {(el: HTMLElement, fn: () => void) => () => void} [observe] calls `fn` when `el` is resized; returns a stop function
 */
/** @typedef {Omit<NowPlaying, "hostTime"> & { hostTime?: number }} Playing */

const SEEK_STEP_S = 5;
const OWN_ACTIONS = new Set(["playPause", "seekBack", "seekForward", "trackPrev", "trackNext", "queue"]);

/** Text in a span: empty means hidden, the full text is the tooltip. @param {HTMLElement} el @param {string} value */
function setText(el, value) {
  el.textContent = value;
  el.hidden = value === "";
  if (value === "") el.removeAttribute("title");
  else el.setAttribute("title", value);
}

/** @param {string} cls @param {string} label @param {string} glyph @param {string} [key] */
const iconButton = (cls, label, glyph, key) =>
  h("button", { type: "button", class: `icon-btn ${cls}`, "aria-label": label, title: key ? `${label} (${key})` : label }, glyph);

/** @param {PlayerDeps} deps */
export function createPlayer(deps) {
  const { root, bar, send, toggleQueue, closePanels, visible } = deps;
  const doc = root.ownerDocument;
  const win = doc.defaultView;
  const now = deps.now ?? (() => performance.now());
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((id) => clearTimeout(id));
  const raf = deps.raf ?? ((fn) => requestAnimationFrame(fn));
  const caf = deps.caf ?? ((id) => cancelAnimationFrame(id));
  const observe =
    deps.observe ??
    ((el, fn) => {
      const ro = new ResizeObserver(fn);
      ro.observe(el);
      return () => ro.disconnect();
    });

  // ---- state ---------------------------------------------------------------------------------
  let transport = false;
  /** @type {Playing | null} */ let np = null;
  let recvAt = 0; // shell time of `np`; the host's clock is never used
  /** @type {Queue | null} */ let queue = null;
  let dragging = false; // the slider holds a value the user is choosing
  let pressed = false; // pointer is down on the slider
  /** @type {unknown} */ let tick = null;
  /** @type {unknown} */ let frame = null;
  /** What the DOM shows, so an unchanged value is never written again. @type {Record<string, unknown>} */
  const shown = {};
  /** @param {string} key @param {unknown} value @param {(v: any) => void} apply */
  const put = (key, value, apply) => {
    if (shown[key] === value) return;
    shown[key] = value;
    apply(value);
  };

  const present = () => transport && np !== null;
  const faded = () => root.classList.contains("idle") || root.classList.contains("overlays-hidden");
  let noRoom = false; // not even the tight row fits: the row is not shown (fit level 3)
  const active = () => present() && visible() && !faded() && !noRoom;
  const position = () => (np ? positionAt(np, recvAt, now()) : 0);

  // ---- transport row ---------------------------------------------------------------------------
  const title = h("span", { class: "player-title" });
  const band = h("span", { class: "player-band", hidden: true });
  const album = h("span", { class: "player-album", hidden: true });
  const nowEl = h("div", { class: "player-now" }, title, band, album);
  const play = iconButton("player-play", "Play", "▶", "Space");
  const elapsed = h("span", { class: "player-time player-elapsed" }, "0:00");
  const total = h("span", { class: "player-time player-total", hidden: true });
  const seek = /** @type {HTMLInputElement} */ (
    h("input", { type: "range", class: "player-seek", "aria-label": "Seek", min: "0", max: "0", step: "any", value: "0", disabled: true })
  );
  const queueBtn = h("button", { type: "button", class: "icon-btn player-queue", "aria-label": "Queue", title: "Queue (Q)", onclick: () => toggleQueue() }, "Queue");
  const controls = h(
    "div",
    { class: "player-controls" },
    iconButton("player-prev", "Previous track", "|◀", ","),
    play,
    iconButton("player-next", "Next track", "▶|", "."),
    elapsed,
    seek,
    total,
    queueBtn,
  );
  const openBtn = h("button", { type: "button", class: "primary player-open", hidden: true }, "Open files…");
  const openFolderBtn = h("button", { type: "button", class: "chip player-open-folder", hidden: true }, "Open folder…");
  const openEl = h("div", { class: "player-empty", hidden: true }, openBtn, openFolderBtn);
  const row = h("div", { class: "player", role: "group", "aria-label": "Player", "data-collapse": "0", "data-fit": "0" }, nowEl, controls, openEl);
  const measure =
    deps.measure ??
    (() => {
      const rems = parseFloat(win?.getComputedStyle(root).fontSize ?? "") || 16;
      const own = [...bar().children].filter((c) => c !== row && !/** @type {HTMLElement} */ (c).hidden);
      const mainTop = Math.min(...own.map((c) => c.getBoundingClientRect().top));
      const top = root.querySelector(".topbar");
      const topBottom = top ? Math.max(0, ...[...top.children].map((c) => c.getBoundingClientRect().bottom)) : 0;
      return {
        width: row.clientWidth,
        room: mainTop - topBottom,
        rem: rems,
        lift: bar().getBoundingClientRect().bottom - mainTop,
      };
    });
  const reserved = deps.reserved ?? (() => root.clientHeight - row.getBoundingClientRect().top);

  /** @param {object} msg */
  const command = (msg) => send(msg);
  row.querySelector(".player-prev")?.addEventListener("click", () => command({ type: "transport", action: "previous" }));
  row.querySelector(".player-next")?.addEventListener("click", () => command({ type: "transport", action: "next" }));
  play.addEventListener("click", () => command({ type: "transport", action: "toggle" }));
  openBtn.addEventListener("click", () => command({ type: "playerOpen", kind: "files", replace: true }));
  openFolderBtn.addEventListener("click", () => command({ type: "playerOpen", kind: "folder", replace: true }));

  // ---- seeking ---------------------------------------------------------------------------------
  /** @param {number} sec the whole second the slider and the elapsed label show */
  function showSecond(sec) {
    put("sec", sec, (s) => {
      elapsed.textContent = formatTime(s);
      if (!dragging) seek.value = String(s);
      seek.setAttribute("aria-valuetext", total.hidden ? formatTime(s) : `${formatTime(s)} of ${total.textContent}`);
    });
  }

  /** A seek the user chose: sent, and shown at once (a paused host reports no new position). @param {number} to */
  function seekTo(to) {
    if (!np) return;
    if (command({ type: "transport", action: "seek", value: to, serial: np.serial })) {
      np = { ...np, position: to };
      recvAt = now();
    }
    refresh();
  }

  function cancelScrub() {
    pressed = false;
    if (!dragging) return;
    dragging = false;
    refresh();
  }

  seek.addEventListener("pointerdown", () => (pressed = true));
  seek.addEventListener("blur", cancelScrub);
  // A release outside the slider or the window never reaches the slider: listen everywhere, or
  // the overlays would be held up for good.
  for (const t of ["pointerup", "pointercancel"]) doc.addEventListener(t, () => (pressed = false));
  win?.addEventListener("blur", cancelScrub);
  seek.addEventListener("input", () => {
    dragging = true;
    schedule();
    showSecond(Math.floor(Number(seek.value)));
  });
  seek.addEventListener("change", () => {
    dragging = false;
    seekTo(Number(seek.value));
  });

  // ---- drawing ---------------------------------------------------------------------------------
  /** The track's text and name for assistive technology: not time-driven and cheap, so written on every change, faded or not. */
  function renderText() {
    if (!present() || !np) return;
    const n = np;
    const isEmpty = queue !== null && n.count === 0;
    put("empty", isEmpty, (v) => {
      openEl.hidden = !v;
      openBtn.hidden = openFolderBtn.hidden = !v;
      controls.hidden = v;
      nowEl.hidden = v;
    });
    put("name", n.title === "" ? "Player" : `Player: ${n.title}${n.artist === "" ? "" : `, ${n.artist}`}`, (v) => row.setAttribute("aria-label", v));
    put("title", n.title, (v) => setText(title, v));
    put("band", n.artist, (v) => setText(band, v));
    put("album", n.album, (v) => setText(album, v));
  }

  /** The play state, the slider and the clock: only while someone can see them. */
  function render() {
    if (!active() || !np) return;
    const n = np;
    put("playing", n.playing, (v) => {
      play.textContent = v ? "❚❚" : "▶";
      play.setAttribute("aria-label", v ? "Pause" : "Play");
      play.setAttribute("title", `${v ? "Pause" : "Play"} (Space)`);
    });
    put("queueBtn", queue !== null, (v) => (queueBtn.hidden = !v));
    put("duration", n.duration, (d) => {
      const known = d !== null && d > 0;
      seek.max = String(known ? d : 0);
      seek.disabled = !known;
      total.hidden = !known;
      total.textContent = known ? formatTime(d) : "";
      shown.sec = undefined; // the slider's range moved: write the position again
    });
    if (!dragging) showSecond(Math.floor(position()));
  }

  /** Focus inside a row that is about to vanish goes to the bar's own first control, not to the page. */
  function releaseFocus() {
    if (!row.contains(doc.activeElement)) return;
    for (const c of bar().children) {
      if (c === row) continue;
      const button = /** @type {HTMLElement | null} */ (c.matches("button") ? c : c.querySelector("button"));
      if (button) return button.focus();
    }
  }

  /** What fits, where the row sits, and how much of the window it takes (the queue drawer stays clear). */
  function updateLayout() {
    const m = measure();
    put("collapse", collapseLevel(m.width), (v) => row.setAttribute("data-collapse", String(v)));
    const fit = fitLevel(m.room, m.rem);
    put("fit", fit, (v) => row.setAttribute("data-fit", String(v)));
    if (noRoom !== (fit === 3)) {
      noRoom = fit === 3;
      if (noRoom) releaseFocus();
      render();
      schedule();
    }
    // A hair of air above the existing controls, none when every pixel counts.
    put("lift", Math.round(m.lift + (fit === 2 ? 0 : 0.25 * m.rem)), (v) => (row.style.bottom = `${v}px`));
    // Hidden, the row takes nothing: the queue drawer goes back to its own offset.
    if (noRoom) root.style.removeProperty("--player-bar");
    else root.style.setProperty("--player-bar", `${Math.round(reserved())}px`);
  }
  /** @type {(() => void) | null} */ let stopObserving = null;

  /** The label ticks only on a changed second, and only while someone can see it. */
  function schedule() {
    if (tick !== null) clearTimer(tick);
    tick = null;
    if (!np?.playing || dragging || !active()) return;
    const ms = msToNextSecond(position(), np.duration);
    if (ms === null) return; // sitting on the duration: the host's next message decides
    tick = setTimer(() => {
      tick = null;
      render();
      schedule();
    }, ms);
  }

  function refresh() {
    if (present()) {
      if (!row.isConnected) {
        bar().prepend(row);
        root.classList.add("has-player");
        // The window (its height), the bars and the row: any of them changing moves the layout.
        const stops = [root, bar(), row].map((el) => observe(el, updateLayout));
        stopObserving = () => stops.forEach((stop) => stop());
        updateLayout();
      }
    } else if (row.isConnected) {
      row.remove();
      root.classList.remove("has-player");
      root.style.removeProperty("--player-bar");
      stopObserving?.();
      stopObserving = null;
    }
    renderText();
    render();
    schedule();
  }

  // ---- queue panel -----------------------------------------------------------------------------
  const queueBody = h("div", { class: "panel-body queue-body" });
  /** @type {ReturnType<typeof createQueueList> | null} */ let list = null;
  /** @type {HTMLElement | null} */ let queueNote = null;
  /** @type {HTMLButtonElement | null} */ let clearBtn = null;
  /** The entry the list marks as current. @type {number | null} */ let listCurrent = null;

  const panelOpen = () => {
    const p = /** @type {HTMLElement | null} */ (queueBody.closest(".panel"));
    return p !== null && !p.hidden;
  };

  /** @param {string} cls @param {string} label @param {object} msg */
  const queueButton = (cls, label, msg) => h("button", { type: "button", class: `chip ${cls}`, onclick: () => command(msg) }, label);

  function buildQueue() {
    list = createQueueList({ onSelect: (index) => command({ type: "playerSelect", index }) });
    queueNote = h("p", { class: "queue-empty muted" }, "The queue is empty.");
    const openFiles = queueButton("q-open-files", "Open files…", { type: "playerOpen", kind: "files", replace: true });
    clearBtn = /** @type {HTMLButtonElement} */ (queueButton("q-clear danger", "Clear", { type: "playerClear" }));
    // Clear turns itself off once the queue is empty: keep focus in the panel, not on <body>.
    clearBtn.addEventListener("click", () => openFiles.focus());
    queueBody.replaceChildren(
      h(
        "div",
        { class: "queue-tools" },
        openFiles,
        queueButton("q-open-folder", "Open folder…", { type: "playerOpen", kind: "folder", replace: true }),
        queueButton("q-add-files", "Add files…", { type: "playerOpen", kind: "files", replace: false }),
        queueButton("q-add-folder", "Add folder…", { type: "playerOpen", kind: "folder", replace: false }),
        clearBtn,
      ),
      queueNote,
      list.el,
    );
  }

  /** @returns {boolean} whether it drew */
  function flushQueue() {
    if (!panelOpen()) return false;
    if (!list) buildQueue();
    const entries = queue?.entries ?? [];
    listCurrent = np?.index ?? null;
    list?.update(entries, listCurrent);
    if (queueNote) queueNote.hidden = entries.length > 0;
    if (clearBtn) clearBtn.disabled = entries.length === 0;
    return true;
  }

  /** One draw per animation frame however many messages arrive; none while the panel is closed. */
  function scheduleQueue() {
    if (frame !== null || !panelOpen() || !transport) return;
    frame = raf(() => {
      frame = null;
      flushQueue();
    });
  }

  // ---- keys --------------------------------------------------------------------------------------
  /** @param {number} delta */
  const seekBy = (delta) => {
    if (!np) return;
    const top = np.duration !== null && np.duration > 0 ? np.duration : Infinity;
    const now = position();
    if (delta > 0 && now >= top) return; // already at the end: the host's rewind is not ours to race
    seekTo(Math.min(top, Math.max(0, now + delta)));
  };

  // ---- host messages -----------------------------------------------------------------------------
  /** @param {Playing | null} next */
  function setNowPlaying(next) {
    // While the user drags, the host's position is not theirs to move; everything else is.
    if (dragging && np && next && next.serial !== np.serial) dragging = false; // another track: the drag is void
    if (dragging && np && next) np = { ...next, position: np.position, hostTime: np.hostTime };
    else {
      np = next;
      recvAt = now();
    }
  }

  /** @param {boolean} on */
  function setTransport(on) {
    if (!on && transport) {
      if (panelOpen()) closePanels();
      np = null;
      queue = null;
      dragging = pressed = false;
    }
    transport = on;
  }

  return {
    /** The queue panel's content; the app puts it in a panel. */
    queueBody,
    /** @param {HostMessage} msg */
    handle(msg) {
      switch (msg.type) {
        case "hello":
          setTransport(msg.transport);
          if (transport) {
            setNowPlaying(msg.nowPlaying);
            queue = msg.queue;
          }
          refresh();
          scheduleQueue();
          break;
        case "sources":
          setTransport(msg.transport);
          refresh();
          break;
        case "nowPlaying": {
          if (!transport) break;
          const { type: _t, ...fields } = msg;
          setNowPlaying(fields);
          refresh();
          if (np?.index !== listCurrent) scheduleQueue(); // the marker moves; the per-second tick doesn't
          break;
        }
        case "queue":
          if (!transport) break;
          queue = { entries: msg.entries };
          refresh();
          scheduleQueue();
          break;
      }
    },
    /**
     * A key action from keys.js. True for the player's own actions (the app has nothing more to do
     * with them), which act only while the source has a transport; the event is stopped only then.
     * @param {KeyAction} action @param {KeyboardEvent} ev
     */
    key(action, ev) {
      if (!OWN_ACTIONS.has(action)) return false;
      if (!transport || !np) return true;
      if (action === "queue") {
        if (queue !== null) {
          ev.preventDefault();
          toggleQueue();
        }
        return true;
      }
      if (np.count === 0) return true;
      ev.preventDefault();
      switch (action) {
        case "playPause":
          command({ type: "transport", action: "toggle" });
          break;
        case "seekBack":
          seekBy(-SEEK_STEP_S);
          break;
        case "seekForward":
          seekBy(SEEK_STEP_S);
          break;
        case "trackPrev":
          command({ type: "transport", action: "previous" });
          break;
        case "trackNext":
          command({ type: "transport", action: "next" });
          break;
      }
      return true;
    },
    /** The queue panel was just opened. */
    renderQueue() {
      if (!flushQueue()) return;
      if (frame !== null) caf(frame);
      frame = null;
      if (listCurrent !== null) list?.row(listCurrent)?.scrollIntoView({ block: "nearest" });
    },
    /** The window or overlays were shown or hidden: start or stop the clock. */
    sync() {
      render();
      schedule();
    },
    /** True while the user holds the slider: the overlay must not fade under their hand. */
    holdsOverlay: () => dragging || pressed,
  };
}
