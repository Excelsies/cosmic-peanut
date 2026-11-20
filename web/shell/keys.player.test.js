import { describe, expect, it } from "vitest";
import { keyAction } from "./keys.js";

/** @param {Record<string, any>} e */
const ev = (e) => ({
  key: "", shiftKey: false, metaKey: false, ctrlKey: false, altKey: false,
  target: { tagName: e.tag ?? "BODY", type: e.inputType, closest: () => (e.insideControl ? {} : null) },
  ...e,
});

describe("keyAction: player keys", () => {
  it("maps Space, Left/Right, comma, period and q", () => {
    expect(keyAction(ev({ key: " " }))).toBe("playPause");
    expect(keyAction(ev({ key: "ArrowLeft" }))).toBe("seekBack");
    expect(keyAction(ev({ key: "ArrowRight" }))).toBe("seekForward");
    expect(keyAction(ev({ key: "," }))).toBe("trackPrev");
    expect(keyAction(ev({ key: "." }))).toBe("trackNext");
    expect(keyAction(ev({ key: "q" }))).toBe("queue");
    expect(keyAction(ev({ key: "Q" }))).toBe("queue");
  });

  it("leaves the existing map alone", () => {
    expect(keyAction(ev({ key: "n" }))).toBe("next");
    expect(keyAction(ev({ key: "N", shiftKey: true }))).toBe("prev");
    expect(keyAction(ev({ key: "l" }))).toBe("library");
    expect(keyAction(ev({ key: "x" }))).toBeNull();
  });

  it("ignores modified keys", () => {
    for (const key of [" ", "ArrowLeft", ",", ".", "q"]) {
      expect(keyAction(ev({ key, ctrlKey: true }))).toBeNull();
      expect(keyAction(ev({ key, metaKey: true }))).toBeNull();
      expect(keyAction(ev({ key, altKey: true }))).toBeNull();
      expect(keyAction(ev({ key, shiftKey: true }))).toBeNull();
    }
  });

  it("ignores every player key while typing in a text field", () => {
    for (const key of [" ", "ArrowLeft", "ArrowRight", ",", ".", "q"]) {
      expect(keyAction(ev({ key, tag: "INPUT", inputType: "url" }))).toBeNull();
      expect(keyAction(ev({ key, tag: "TEXTAREA" }))).toBeNull();
    }
  });

  it("leaves Space and the arrows to a focused slider, checkbox or select (they own them)", () => {
    for (const inputType of ["range", "checkbox"]) {
      expect(keyAction(ev({ key: " ", tag: "INPUT", inputType }))).toBeNull();
      expect(keyAction(ev({ key: "ArrowLeft", tag: "INPUT", inputType }))).toBeNull();
      expect(keyAction(ev({ key: "ArrowRight", tag: "INPUT", inputType }))).toBeNull();
    }
    expect(keyAction(ev({ key: " ", tag: "SELECT" }))).toBeNull();
    expect(keyAction(ev({ key: "ArrowLeft", tag: "SELECT" }))).toBeNull();
  });

  it("still takes the letter keys while a slider has focus, like n and l do", () => {
    expect(keyAction(ev({ key: ",", tag: "INPUT", inputType: "range" }))).toBe("trackPrev");
    expect(keyAction(ev({ key: "q", tag: "INPUT", inputType: "range" }))).toBe("queue");
  });

  it("Space keeps its native meaning on any interactive control, wherever it is", () => {
    for (const tag of ["BUTTON", "A", "SUMMARY", "INPUT", "SELECT", "TEXTAREA"]) {
      expect(keyAction(ev({ key: " ", tag }))).toBeNull();
    }
    // a target inside a control (a role=button, a contenteditable, a span in a button)
    expect(keyAction(ev({ key: " ", tag: "SPAN", insideControl: true }))).toBeNull();
    expect(keyAction(ev({ key: " ", tag: "SPAN" }))).toBe("playPause");
  });

  it("the other player keys still work with a button focused", () => {
    expect(keyAction(ev({ key: "ArrowLeft", tag: "BUTTON" }))).toBe("seekBack");
    expect(keyAction(ev({ key: ".", tag: "BUTTON" }))).toBe("trackNext");
  });

  it("Space works for a target without closest() (the existing stub shape)", () => {
    expect(keyAction({ key: " ", shiftKey: false, metaKey: false, ctrlKey: false, altKey: false, target: { tagName: "BODY" } })).toBe("playPause");
  });
});
