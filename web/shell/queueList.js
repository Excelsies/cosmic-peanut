// The queue panel's list. Up to 2000 entries, re-sent whole several times a second while tags
// are scanned, so an update never rebuilds: rows are reused by position and only a cell whose
// text changed is written. A row under the pointer stays the same element.

import { h } from "./dom.js";
import { formatTime } from "./playerClock.js";

/** @typedef {import("./validate.js").QueueEntry} QueueEntry */
/** @typedef {{ li: HTMLElement; button: HTMLButtonElement; title: HTMLElement; artist: HTMLElement;
 *   album: HTMLElement; time: HTMLElement; shown: QueueEntry | null; current: boolean }} Row */

/** One text cell: empty means hidden and no text. The full text rides along as the tooltip. @param {HTMLElement} el @param {string} value */
function setText(el, value) {
  el.textContent = value;
  el.hidden = value === "";
  if (value === "") el.removeAttribute("title");
  else el.setAttribute("title", value);
}

/** @param {number | null} duration */
const clock = (duration) => (duration === null ? "" : formatTime(duration));

/** @param {number} index @returns {Row} */
function makeRow(index) {
  const title = h("span", { class: "q-title" });
  const artist = h("span", { class: "q-artist", hidden: true });
  const album = h("span", { class: "q-album", hidden: true });
  const time = h("span", { class: "q-time", hidden: true });
  const button = /** @type {HTMLButtonElement} */ (
    h("button", { type: "button", class: "q-main", "data-index": index },
      h("span", { class: "q-text" }, title, h("span", { class: "q-sub" }, artist, album)), time)
  );
  return { li: h("li", { class: "q-row" }, button), button, title, artist, album, time, shown: null, current: false };
}

/** @param {Row} row @param {QueueEntry} e */
function writeEntry(row, e) {
  const was = row.shown;
  if (was?.title !== e.title) setText(row.title, e.title);
  if (was?.artist !== e.artist) setText(row.artist, e.artist);
  if (was?.album !== e.album) setText(row.album, e.album);
  if (was?.duration !== e.duration) setText(row.time, clock(e.duration));
  if (was?.playable !== e.playable) {
    // aria-disabled, not disabled: the row stays in the tab order and keeps focus if it turns unplayable.
    row.li.classList.toggle("is-unplayable", !e.playable);
    if (e.playable) {
      row.button.removeAttribute("aria-disabled");
      row.button.removeAttribute("title");
    } else {
      row.button.setAttribute("aria-disabled", "true");
      row.button.setAttribute("title", "This file can't be played");
    }
  }
  row.shown = e;
}

/** @param {{ onSelect: (index: number) => void }} opts */
export function createQueueList({ onSelect }) {
  const el = h("ul", { class: "queue-list", "aria-label": "Queue" });
  /** @type {Row[]} */
  const rows = [];
  /** @type {number | null} */
  let marked = null;

  el.addEventListener("click", (ev) => {
    const button = /** @type {Element | null} */ (ev.target)?.closest?.("button");
    if (!button || button.getAttribute("aria-disabled") === "true") return;
    onSelect(Number(/** @type {HTMLElement} */ (button).dataset.index));
  });

  /** @param {number | null} index @param {boolean} on */
  function mark(index, on) {
    const row = index === null ? undefined : rows[index];
    if (!row || row.current === on) return;
    row.current = on;
    if (on) row.button.setAttribute("aria-current", "true");
    else row.button.removeAttribute("aria-current");
  }

  return {
    el,
    get length() {
      return rows.length;
    },
    /** @param {QueueEntry[]} entries @param {number | null} current */
    update(entries, current) {
      while (rows.length > entries.length) /** @type {Row} */ (rows.pop()).li.remove();
      const fresh = [];
      for (let i = 0; i < entries.length; i++) {
        let row = rows[i];
        if (!row) {
          row = makeRow(i);
          rows.push(row);
          fresh.push(row.li);
        }
        const e = entries[i];
        if (row.shown !== e) writeEntry(row, e);
      }
      el.append(...fresh);
      if (marked !== current) {
        mark(marked, false);
        marked = current;
      }
      mark(marked, true);
    },
    /** The row of entry `index`, e.g. to scroll it into view. @param {number} index */
    row: (index) => rows[index]?.li ?? null,
  };
}
