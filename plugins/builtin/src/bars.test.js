// @ts-check
import { describe, expect, it } from "vitest";
import create from "./bars.js";
import { fakeContext } from "./lib/fake-2d.test-helper.js";

const DEFAULTS = { count: 64, smoothing: 0.7, color: "#4fd1ff", mirror: false, waveform: true };

function setup() {
  const fake = fakeContext();
  const ctx = /** @type {import('../tidalviz').VisualizerContext} */ (
    /** @type {unknown} */ ({
      ctx2d: fake.ctx,
      params: { ...DEFAULTS },
      size: { width: 2560, height: 1440, cssWidth: 1280, cssHeight: 720, dpr: 2 },
      reduceMotion: false,
      reduceFlashing: true,
    })
  );
  const viz = /** @type {import('../tidalviz').Visualizer} */ (create(ctx));
  return { ...fake, ctx, viz };
}

/** @param {number} f frame number; music for the first second, then digital silence */
function audioAt(f) {
  const loud = f < 60;
  return /** @type {import('../tidalviz').AudioFrame} */ (
    /** @type {unknown} */ ({
      bands: Float32Array.from({ length: 64 }, (_, i) => (loud ? 0.3 + 0.6 * Math.abs(Math.sin(f * 0.3 + i)) : 0)),
      waveform: Float32Array.from({ length: 2048 }, (_, i) => (loud ? Math.sin(i * 0.05 + f) * 0.5 : 0)),
      onset: loud && f % 30 === 0,
      onsetStrength: 0.8,
      bassAtt: loud ? 1.2 : 0,
      silent: !loud,
    })
  );
}

/** Run one frame and return its draw calls. @param {ReturnType<typeof setup>} s @param {number} f */
function frame(s, f) {
  s.log.length = 0;
  s.viz.frame(audioAt(f), { now: f / 60, dt: 1 / 60, frame: f });
  return [...s.log];
}

describe("bars", () => {
  it("skips a frame only when it would draw exactly what the canvas already shows", () => {
    const lazy = setup();
    const eager = setup(); // forced to redraw every frame
    /** @type {unknown[]} */
    let shown = [];
    let firstSkip = -1;
    let lastDraw = -1;
    for (let f = 0; f < 60 * 20; f++) {
      eager.viz.params?.({});
      const want = frame(eager, f);
      const got = frame(lazy, f);
      expect(want.length).toBeGreaterThan(0);
      if (got.length === 0) {
        expect(want).toEqual(shown);
        if (firstSkip < 0) firstSkip = f;
      } else {
        expect(got).toEqual(want);
        shown = got;
        lastDraw = f;
      }
    }
    // Levels, caps and the glow settle within seconds of silence; from then on nothing is drawn.
    expect(firstSkip).toBeGreaterThan(60);
    expect(lastDraw).toBeLessThan(60 * 12);
  });

  it("redraws a settled frame after a param change, a resize, or any non-zero sample", () => {
    const s = setup();
    let f = 0;
    for (; f < 60 * 15; f++) frame(s, f);
    expect(frame(s, f++)).toEqual([]);
    s.viz.params?.({ color: "#4fd1ff" });
    expect(frame(s, f++).length).toBeGreaterThan(0);
    expect(frame(s, f++)).toEqual([]);
    s.viz.resize?.(s.ctx.size);
    expect(frame(s, f++).length).toBeGreaterThan(0);
    expect(frame(s, f++)).toEqual([]);
    const a = audioAt(f);
    a.waveform[1000] = 1e-6;
    s.log.length = 0;
    s.viz.frame(a, { now: f / 60, dt: 1 / 60, frame: f });
    expect(s.log.length).toBeGreaterThan(0);
  });
});
