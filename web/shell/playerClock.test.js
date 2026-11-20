import { describe, expect, it } from "vitest";
import { collapseLevel, fitLevel, formatTime, msToNextSecond, positionAt } from "./playerClock.js";

describe("formatTime", () => {
  it.each([
    [0, "0:00"], [5.9, "0:05"], [59.999, "0:59"], [60, "1:00"], [605, "10:05"],
    [3599, "59:59"], [3600, "1:00:00"], [3725.4, "1:02:05"], [-3, "0:00"], [Number.NaN, "0:00"],
  ])("%s -> %s", (s, text) => expect(formatTime(s)).toBe(text));
});

describe("positionAt", () => {
  const np = (o = {}) => ({ position: 10, playing: true, duration: 100, ...o });

  it("advances by the time since the message while playing", () => {
    expect(positionAt(np(), 5000, 5000)).toBe(10);
    expect(positionAt(np(), 5000, 7500)).toBe(12.5);
  });

  it("holds still while paused or stopped", () => {
    expect(positionAt(np({ playing: false }), 5000, 99999)).toBe(10);
  });

  it("clamps to a known duration and never infers the end of the track", () => {
    expect(positionAt(np({ position: 98 }), 0, 60000)).toBe(100);
  });

  it("runs on without a duration", () => {
    expect(positionAt(np({ duration: null }), 0, 600000)).toBe(610);
  });

  it("a clock that ran backwards cannot move the position before the message", () => {
    expect(positionAt(np(), 5000, 4000)).toBe(10);
  });

  it("a duration of 0 is unknown (a clamp to 0 would freeze the display)", () => {
    expect(positionAt(np({ duration: 0 }), 0, 2000)).toBe(12);
  });
});

describe("msToNextSecond", () => {
  it("is the time until the displayed whole second changes", () => {
    expect(msToNextSecond(10, null)).toBe(1000);
    expect(msToNextSecond(10.25, null)).toBe(750);
    expect(msToNextSecond(10.999, null)).toBeGreaterThanOrEqual(1);
  });

  it("is null once the position sits on the duration (nothing more will change)", () => {
    expect(msToNextSecond(100, 100)).toBeNull();
  });

  it("stops at the duration instead of ticking past it", () => {
    expect(msToNextSecond(99.5, 99.8)).toBe(300);
  });
});

describe("collapseLevel", () => {
  it("drops the album first, then the band, then the time labels as the window narrows", () => {
    expect(collapseLevel(1280)).toBe(0);
    expect(collapseLevel(720)).toBe(0);
    expect(collapseLevel(719)).toBe(1); // no album
    expect(collapseLevel(520)).toBe(1);
    expect(collapseLevel(519)).toBe(2); // no band
    expect(collapseLevel(420)).toBe(2);
    expect(collapseLevel(419)).toBe(3); // no time labels
    expect(collapseLevel(320)).toBe(3);
  });
});

describe("fitLevel", () => {
  const rem = 16;
  it("keeps the title line while the room between the bars holds the whole row", () => {
    expect(fitLevel(400, rem)).toBe(0);
    expect(fitLevel(4.75 * rem, rem)).toBe(0);
  });

  it("gives up the title line first, then the buttons' padding", () => {
    expect(fitLevel(4.75 * rem - 1, rem)).toBe(1);
    expect(fitLevel(3 * rem, rem)).toBe(1);
    expect(fitLevel(3 * rem - 1, rem)).toBe(2);
    expect(fitLevel(1.3 * rem, rem)).toBe(2);
  });

  it("shows no row at all when even the tight one does not fit", () => {
    expect(fitLevel(1.3 * rem - 0.5, rem)).toBe(3);
    expect(fitLevel(0, rem)).toBe(3);
    expect(fitLevel(-40, rem)).toBe(3);
  });

  it("decides from the room and the font alone, never from the row's own height", () => {
    // same inputs, same answer: nothing for an observer callback to flip
    for (let i = 0; i < 3; i++) expect(fitLevel(22, 16)).toBe(2);
    expect(fitLevel(22, 18)).toBe(3);
    expect(fitLevel(22, 20)).toBe(3);
  });

  it("follows the font size, not pixels", () => {
    expect(fitLevel(100, 16)).toBe(0);
    expect(fitLevel(100, 24)).toBe(1);
  });
});
