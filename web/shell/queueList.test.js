// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";
import { createQueueList } from "./queueList.js";

/** @param {Record<string, any>} [o] */
const entry = (o = {}) => ({ title: "Song", artist: "Band", album: "Record", duration: 185, playable: true, ...o });
/** @param {number} n */
const many = (n) => Array.from({ length: n }, (_, i) => entry({ title: `Song ${i}`, duration: 60 + i }));

function setup() {
  const onSelect = vi.fn();
  const list = createQueueList({ onSelect });
  document.body.replaceChildren(list.el);
  const rows = () => /** @type {HTMLElement[]} */ ([...list.el.querySelectorAll("li")]);
  const mutations = () => {
    const obs = new MutationObserver(() => {});
    obs.observe(list.el, { subtree: true, childList: true, attributes: true, characterData: true });
    return { count: () => obs.takeRecords().length };
  };
  return { list, onSelect, rows, mutations };
}

describe("queue list", () => {
  it("renders title, band, album and duration per entry", () => {
    const { list, rows } = setup();
    list.update([entry({ title: "One", artist: "A", album: "B", duration: 65 })], null);
    expect(rows()).toHaveLength(1);
    expect(rows()[0].querySelector(".q-title")?.textContent).toBe("One");
    expect(rows()[0].querySelector(".q-artist")?.textContent).toBe("A");
    expect(rows()[0].querySelector(".q-album")?.textContent).toBe("B");
    expect(rows()[0].querySelector(".q-time")?.textContent).toBe("1:05");
  });

  it("renders nothing for missing parts: no placeholders, no empty separators", () => {
    const { list, rows } = setup();
    list.update([entry({ artist: "", album: "", duration: null })], null);
    const r = rows()[0];
    expect(r.querySelector(".q-artist")?.textContent).toBe("");
    expect(r.querySelector(".q-album")?.textContent).toBe("");
    expect(r.querySelector(".q-time")?.textContent).toBe("");
    expect(r.textContent).toBe("Song");
    expect(r.querySelector(".q-artist")?.hasAttribute("hidden")).toBe(true);
  });

  it("puts the full text in title attributes for truncated cells", () => {
    const { list, rows } = setup();
    list.update([entry({ title: "A very long title" })], null);
    expect(rows()[0].querySelector(".q-title")?.getAttribute("title")).toBe("A very long title");
  });

  it("marks the current entry", () => {
    const { list, rows } = setup();
    list.update(many(3), 1);
    expect(rows().map((r) => r.querySelector("button")?.getAttribute("aria-current") ?? null)).toEqual([null, "true", null]);
    list.update(many(3), 2);
    expect(rows().map((r) => r.querySelector("button")?.getAttribute("aria-current") ?? null)).toEqual([null, null, "true"]);
    list.update(many(3), null);
    expect(rows().some((r) => r.querySelector("[aria-current]"))).toBe(false);
  });

  it("an identical update touches nothing", async () => {
    const { list, rows, mutations } = setup();
    list.update(many(50), 3);
    const before = rows();
    const m = mutations();
    list.update(many(50), 3);
    expect(rows()).toEqual(before);
    expect(rows().every((r, i) => r === before[i])).toBe(true);
    expect(m.count()).toBe(0);
  });

  it("filling in tags rewrites only the rows that changed, keeping every row element", () => {
    const { list, rows, mutations } = setup();
    const q = many(100);
    list.update(q, 0);
    const before = rows();
    const m = mutations();
    const next = q.map((e, i) => (i === 40 ? { ...e, artist: "New band" } : e));
    list.update(next, 0);
    expect(rows().every((r, i) => r === before[i])).toBe(true);
    expect(rows()[40].querySelector(".q-artist")?.textContent).toBe("New band");
    expect(m.count()).toBeGreaterThan(0);
    expect(m.count()).toBeLessThan(6);
  });

  it("grows by appending and shrinks by removing from the end, reusing the rows it keeps", () => {
    const { list, rows } = setup();
    list.update(many(5), null);
    const kept = rows();
    list.update(many(8), null);
    expect(rows()).toHaveLength(8);
    expect(rows().slice(0, 5).every((r, i) => r === kept[i])).toBe(true);
    list.update(many(2), null);
    expect(rows()).toHaveLength(2);
    expect(rows()[1]).toBe(kept[1]);
    list.update([], null);
    expect(rows()).toHaveLength(0);
  });

  it("a row that becomes unplayable is disabled in place", () => {
    const { list, rows } = setup();
    list.update(many(3), null);
    const row = rows()[1];
    list.update(many(3).map((e, i) => (i === 1 ? { ...e, playable: false } : e)), null);
    expect(rows()[1]).toBe(row);
    const b = /** @type {HTMLButtonElement} */ (row.querySelector("button"));
    expect(b.getAttribute("aria-disabled")).toBe("true");
    expect(b.disabled).toBe(false); // still in the tab order
    expect(row.classList.contains("is-unplayable")).toBe(true);
  });

  it("a focused row that turns unplayable keeps focus; one that turns playable again is live", () => {
    const { list, rows, onSelect } = setup();
    list.update(many(3), null);
    const b = /** @type {HTMLButtonElement} */ (rows()[1].querySelector("button"));
    b.focus();
    list.update(many(3).map((e, i) => (i === 1 ? { ...e, playable: false } : e)), null);
    expect(document.activeElement).toBe(b);
    list.update(many(3), null);
    expect(b.hasAttribute("aria-disabled")).toBe(false);
    b.click();
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("clicking a playable entry selects its index", () => {
    const { list, rows, onSelect } = setup();
    list.update(many(4), 0);
    /** @type {HTMLElement} */ (rows()[2].querySelector(".q-title")).click(); // a click on a child
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(2);
  });

  it("clicking an unplayable entry does nothing", () => {
    const { list, rows, onSelect } = setup();
    list.update([entry(), entry({ playable: false })], 0);
    /** @type {HTMLElement} */ (rows()[1].querySelector("button")).click();
    rows()[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("a click in progress survives an update: the row is the same element", () => {
    const { list, rows } = setup();
    list.update(many(10), 0);
    const target = rows()[5].querySelector("button");
    list.update(many(10).map((e, i) => (i === 5 ? { ...e, album: "tags arrived" } : e)), 0);
    expect(rows()[5].querySelector("button")).toBe(target);
    expect(target?.isConnected).toBe(true);
  });

  it("renders markup in tags as literal text", () => {
    const { list, rows } = setup();
    list.update([entry({ title: "<img src=x onerror=alert(1)>", artist: "<b>b</b>", album: "&lt;i&gt;" })], null);
    expect(rows()[0].querySelector("img, b, i")).toBeNull();
    expect(rows()[0].querySelector(".q-title")?.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(rows()[0].querySelector(".q-artist")?.textContent).toBe("<b>b</b>");
    expect(rows()[0].querySelector(".q-album")?.textContent).toBe("&lt;i&gt;");
  });
});
