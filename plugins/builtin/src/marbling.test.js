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
  it("draws nothing on a silent frame that would repeat the last image", async () => {
    const s = await setup({ renew: 0 });
    expect(draws(frame(s))).toBe(1);
    for (let i = 0; i < 120; i++) expect(frame(s)).toEqual([]);
  });

  it("redraws after a parameter change, a resize, and a (re)started loop", async () => {
    const s = await setup({ renew: 0 });
    frame(s);
    frame(s);
    s.params.palette = "gold";
    s.viz.params?.({ palette: "gold" });
    expect(draws(frame(s))).toBe(1);
    expect(draws(frame(s))).toBe(0);

    const size = { width: 800, height: 360, cssWidth: 400, cssHeight: 180, dpr: 2 };
    /** @type {any} */ (s.ctx).size = size;
    s.viz.resize?.(size);
    expect(draws(frame(s))).toBe(1);
    expect(draws(frame(s))).toBe(0);

    expect(draws(frame(s, audioFrame(), 0))).toBe(1);
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
    expect(new Set(sheen).size).toBe(sheen.length);
    expect(sheen.at(-1)).toBe(0);
  });

  it("draws the frames that bring events (the renew drift), each with more pending", async () => {
    const s = await setup();
    frame(s);
    let drawn = 0;
    let pending = 0;
    for (let i = 0; i < 1200; i++) {
      const log = frame(s);
      if (!draws(log)) continue;
      drawn++;
      const n = log.filter((c) => c.op === "uniform1i" && c.args[0]?.name === "u_n").at(-1)?.args[1];
      if (!log.some((c) => c.op === "bindFramebuffer" && c.args[1])) expect(n).toBeGreaterThan(pending);
      pending = n;
    }
    expect(drawn).toBeGreaterThan(10);
    expect(drawn).toBeLessThan(400);
  });

  it("draws over the whole canvas without clearing it first", async () => {
    const s = await setup();
    frame(s);
    expect(frame(s, audioFrame({ silent: false, onset: true })).filter((c) => c.op === "clear")).toEqual([]);
  });
});
