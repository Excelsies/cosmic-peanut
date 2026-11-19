// @ts-check
import { describe, expect, it } from "vitest";
import create from "./marbling.js";
import { builtinAssets, fakeGl } from "./lib/fake-gl.test-helper.js";

const DEFAULTS = { palette: "beast", paper: "cream", pour: 1, size: 1, rake: 1, reactivity: 1, renew: 1 };

/** @param {Partial<typeof DEFAULTS>} [params] */
async function setup(params = {}) {
  const fake = fakeGl();
  const p = { ...DEFAULTS, ...params };
  const ctx = /** @type {import('../tidalviz').VisualizerContext} */ (
    /** @type {unknown} */ ({
      gl: fake.gl,
      params: p,
      size: { width: 640, height: 360, cssWidth: 320, cssHeight: 180, dpr: 2 },
      assets: builtinAssets,
      reduceMotion: false,
      reduceFlashing: true,
    })
  );
  const viz = await create(ctx);
  return { ...fake, ctx, params: p, viz, f: 0 };
}

/** @param {{ silent?: boolean, onset?: boolean }} [o] */
function audioFrame(o = {}) {
  const silent = o.silent ?? true;
  return /** @type {import('../tidalviz').AudioFrame} */ (
    /** @type {unknown} */ ({
      bands: new Float32Array(64).fill(silent ? 0 : 0.5),
      spectrum: new Float32Array(1024),
      waveform: new Float32Array(2048),
      rms: silent ? 0 : 0.3,
      peak: silent ? 0 : 0.6,
      centroid: 0.3,
      bassAtt: 1,
      onset: o.onset ?? false,
      onsetStrength: o.onset ? 1 : 0,
      silent,
    })
  );
}

/** One frame; returns its GL calls. @param {Awaited<ReturnType<typeof setup>>} s */
function frame(s, audio = audioFrame(), dt = 1 / 60) {
  s.log.length = 0;
  s.viz.frame(audio, { now: s.f / 60, dt, frame: s.f++ });
  return s.log;
}

const draws = (/** @type {{ op: string }[]} */ log) => log.filter((c) => c.op === "drawArrays").length;

describe("marbling redraws", () => {
  // In headless software-GL WebKit (Playwright), a WebGL canvas created with `antialias: false`
  // showed each draw one draw late (a draw reached the screen when the next one was made), so a
  // single draw followed by skips stayed on the previous image. Not measured on WebKitGTK or
  // WKWebView, so the first frame that would repeat the image is drawn defensively: it pushes the
  // last real draw to the screen, and only then are repeats skipped.
  it("draws the image and one identical repeat, then nothing on silent frames that repeat it", async () => {
    const s = await setup({ renew: 0 });
    expect(draws(frame(s))).toBe(1);
    expect(draws(frame(s))).toBe(1);
    for (let i = 0; i < 120; i++) expect(frame(s)).toEqual([]);
  });

  it("redraws after a parameter change, a resize, and a (re)started loop", async () => {
    const s = await setup({ renew: 0 });
    for (let i = 0; i < 3; i++) frame(s);
    s.params.palette = "gold";
    s.viz.params?.({ palette: "gold" });
    expect(draws(frame(s))).toBe(1);
    expect(draws(frame(s))).toBe(1); // the repeat that shows it
    expect(draws(frame(s))).toBe(0);

    const size = { width: 800, height: 360, cssWidth: 400, cssHeight: 180, dpr: 2 };
    /** @type {any} */ (s.ctx).size = size;
    s.viz.resize?.(size);
    expect(draws(frame(s))).toBe(1);
    expect(draws(frame(s))).toBe(1);
    expect(draws(frame(s))).toBe(0);

    expect(draws(frame(s, audioFrame(), 0))).toBe(1);
    expect(draws(frame(s))).toBe(1);
    expect(draws(frame(s))).toBe(0);
  });

  it("keeps drawing while the sheen fades, until the value the shader gets stops changing", async () => {
    const s = await setup({ renew: 0 });
    frame(s, audioFrame({ silent: false, onset: true }));
    /** @type {number[]} */
    const sheen = [];
    let drawn = 0;
    for (let i = 0; i < 3000; i++) {
      const log = frame(s);
      if (!draws(log)) continue;
      drawn++;
      const u = log.find((c) => c.op === "uniform1f" && c.args[0]?.name === "u_sheen");
      sheen.push(Math.fround(u?.args[1]));
    }
    expect(drawn).toBeGreaterThan(60);
    expect(drawn).toBeLessThan(3000);
    // No value is drawn more than twice (the second is the repeat that shows the first), and the
    // last one (0) is drawn twice.
    for (const v of new Set(sheen)) expect(sheen.filter((x) => x === v).length).toBeLessThanOrEqual(2);
    expect(sheen.slice(-2)).toEqual([0, 0]);
  });

  it("draws the frames that bring events, each with more pending", async () => {
    // Music playing (renewal and the dropper rest in silence), no currents (they move every frame).
    const s = await setup({ reactivity: 0 });
    const music = audioFrame({ silent: false });
    frame(s, music);
    let drawn = 0;
    let pending = 0;
    for (let i = 0; i < 600; i++) {
      const log = frame(s, music);
      const events = /** @type {any} */ (s.viz).debug.events;
      if (events > 0) expect(draws(log), `frame ${i}`).toBeGreaterThan(0);
      if (!draws(log)) continue;
      drawn++;
      const n = log.filter((c) => c.op === "uniform1i" && c.args[0]?.name === "u_n").at(-1)?.args[1];
      if (!log.some((c) => c.op === "bindFramebuffer" && c.args[1])) expect(n).toBeGreaterThan(pending);
      pending = n;
    }
    expect(drawn).toBeGreaterThan(10);
  });

  it("draws every frame while the currents move the bath, and nothing once they have stopped", async () => {
    const s = await setup({ renew: 0 });
    const music = audioFrame({ silent: false });
    for (let i = 0; i < 5; i++) frame(s, music);
    // Between beats the currents still carry the paper: each of these frames shows something new.
    for (let i = 0; i < 60; i++) expect(draws(frame(s, music))).toBeGreaterThan(0);
    // Silence: the currents ease off, then the page stands still (and the frames are skipped again).
    let still = 0;
    for (let i = 0; i < 1200; i++) still = draws(frame(s)) ? 0 : still + 1;
    expect(still).toBeGreaterThan(300);
  });

  it("draws over the whole canvas without clearing it first", async () => {
    const s = await setup();
    frame(s);
    expect(frame(s, audioFrame({ silent: false, onset: true })).filter((c) => c.op === "clear")).toEqual([]);
  });
});
