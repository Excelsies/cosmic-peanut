import { describe, expect, it } from "vitest";
import { MAX_QUEUE, MAX_TEXT, parseHostMessage } from "./validate.js";

const np = (o = {}) => ({
  artist: "Boards of Canada", album: "Geogaddi", title: "Music Is Math", duration: 321.5,
  position: 12.25, playing: true, index: 2, count: 5, hostTime: 1000.5, serial: 7, ...o,
});
const entry = (o = {}) => ({ title: "T", artist: "A", album: "B", duration: 60, playable: true, ...o });

/** @param {Record<string, unknown>} m */
const parse = (m) => parseHostMessage(JSON.stringify(m));

describe("parseHostMessage: nowPlaying", () => {
  it("parses a full message and strips unknown fields", () => {
    expect(parse({ type: "nowPlaying", ...np(), path: "/etc/passwd" })).toEqual({ type: "nowPlaying", ...np() });
  });

  it("accepts a null duration and a null index for an empty queue", () => {
    expect(parse({ type: "nowPlaying", ...np({ duration: null, index: null, count: 0, title: "" }) })).toEqual({
      type: "nowPlaying", ...np({ duration: null, index: null, count: 0, title: "" }),
    });
  });

  it("accepts empty strings for unknown tags", () => {
    const m = parse({ type: "nowPlaying", ...np({ artist: "", album: "" }) });
    expect(m).toMatchObject({ artist: "", album: "" });
  });

  it.each([
    ["missing title", { title: undefined }],
    ["title not a string", { title: 5 }],
    ["artist not a string", { artist: null }],
    ["album not a string", { album: {} }],
    ["negative position", { position: -1 }],
    ["non-numeric position", { position: "3" }],
    ["negative duration", { duration: -5 }],
    ["string duration", { duration: "60" }],
    ["undefined duration", { duration: undefined }],
    ["playing not boolean", { playing: 1 }],
    ["fractional index", { index: 1.5 }],
    ["negative index", { index: -1 }],
    ["index equal to count", { index: 5, count: 5 }],
    ["index beyond count", { index: 9, count: 5 }],
    ["index with an empty queue", { index: 0, count: 0 }],
    ["count over the cap", { count: MAX_QUEUE + 1, index: 0 }],
    ["fractional count", { count: 2.5, index: 0 }],
    ["missing hostTime", { hostTime: undefined }],
    ["missing serial", { serial: undefined }],
    ["negative serial", { serial: -1 }],
    ["fractional serial", { serial: 1.5 }],
    ["string serial", { serial: "7" }],
  ])("drops %s", (_what, patch) => {
    expect(parse({ type: "nowPlaying", ...np(), ...patch })).toBeNull();
  });

  it("truncates over-long strings to the documented bound", () => {
    const m = parse({ type: "nowPlaying", ...np({ title: "x".repeat(MAX_TEXT + 50) }) });
    expect(m && m.type === "nowPlaying" && m.title).toBe("x".repeat(MAX_TEXT));
  });

  it("rejects JSON numbers that are not finite (they cannot even be encoded)", () => {
    expect(parseHostMessage('{"type":"nowPlaying","artist":"","album":"","title":"t","duration":1e999,"position":0,"playing":true,"index":0,"count":1,"hostTime":1}')).toBeNull();
  });
});

describe("parseHostMessage: queue", () => {
  it("parses entries in order, without extra fields", () => {
    const m = parse({ type: "queue", entries: [entry({ title: "one", path: "/x" }), entry({ title: "two", duration: null, playable: false })] });
    expect(m).toEqual({
      type: "queue",
      entries: [
        { title: "one", artist: "A", album: "B", duration: 60, playable: true },
        { title: "two", artist: "A", album: "B", duration: null, playable: false },
      ],
    });
  });

  it("accepts an empty queue and exactly MAX_QUEUE entries", () => {
    expect(parse({ type: "queue", entries: [] })).toEqual({ type: "queue", entries: [] });
    const m = parse({ type: "queue", entries: Array.from({ length: MAX_QUEUE }, () => entry()) });
    expect(m && m.type === "queue" && m.entries.length).toBe(MAX_QUEUE);
  });

  it("drops the whole message for more than MAX_QUEUE entries, a non-array, or any malformed entry", () => {
    expect(parse({ type: "queue", entries: Array.from({ length: MAX_QUEUE + 1 }, () => entry()) })).toBeNull();
    expect(parse({ type: "queue", entries: "nope" })).toBeNull();
    expect(parse({ type: "queue" })).toBeNull();
    // An entry can't be skipped: every later entry's index would shift away from the host's.
    expect(parse({ type: "queue", entries: [entry(), entry({ title: 3 })] })).toBeNull();
    expect(parse({ type: "queue", entries: [entry({ playable: "yes" })] })).toBeNull();
    expect(parse({ type: "queue", entries: [entry({ duration: -1 })] })).toBeNull();
    expect(parse({ type: "queue", entries: [null] })).toBeNull();
  });

  it("truncates over-long entry strings", () => {
    const m = parse({ type: "queue", entries: [entry({ album: "y".repeat(MAX_TEXT * 2) })] });
    expect(m && m.type === "queue" && m.entries[0].album).toBe("y".repeat(MAX_TEXT));
  });
});

const hello = (o = {}) => ({
  type: "hello", version: 1, pluginOrigin: "http://127.0.0.1:2", visualizers: [], repos: [], settings: {},
  sources: [{ id: "player", name: "Files" }], activeSource: "player", active: null, dev: false, refreshHz: 0, ...o,
});

describe("parseHostMessage: hello and sources carry the player state", () => {
  it("a host without transport fields (macOS, Linux without GStreamer) parses as no transport", () => {
    expect(parse(hello())).toMatchObject({ transport: false, nowPlaying: null, queue: null });
  });

  it("parses transport, nowPlaying and queue", () => {
    const m = parse(hello({ transport: true, nowPlaying: np(), queue: { entries: [entry()] } }));
    expect(m).toMatchObject({
      transport: true,
      nowPlaying: { ...np() },
      queue: { entries: [{ title: "T", artist: "A", album: "B", duration: 60, playable: true }] },
    });
  });

  it("a malformed nowPlaying or queue in hello becomes null instead of failing the hello", () => {
    const m = parse(hello({ transport: true, nowPlaying: { title: 1 }, queue: { entries: [1] } }));
    expect(m).toMatchObject({ type: "hello", transport: true, nowPlaying: null, queue: null });
  });

  it("only a real boolean true counts as transport", () => {
    expect(parse(hello({ transport: "true" }))).toMatchObject({ transport: false });
    expect(parse(hello({ transport: 1 }))).toMatchObject({ transport: false });
  });

  it("sources carries transport, false when absent", () => {
    const s = [{ id: "system", name: "All audio" }];
    expect(parse({ type: "sources", sources: s, active: "system" })).toEqual({ type: "sources", sources: s, active: "system", transport: false });
    expect(parse({ type: "sources", sources: s, active: "system", transport: true })).toMatchObject({ transport: true });
  });
});
