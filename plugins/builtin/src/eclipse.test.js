// @ts-check
import { describe, expect, it } from "vitest";
import create from "./eclipse.js";
import { builtinAssets, fakeGl } from "./lib/fake-gl.test-helper.js";
import { fitEclipse } from "./lib/eclipse.js";

const DEFAULTS = {
  reactivity: 1,
  reach: 1,
  thickness: 1,
  clouds: 0.45,
  tint: "monochrome",
  sky: "black",
  rotation: 0.03,
};
const W = 2560;
const H = 1440;

async function setup() {
  const fake = fakeGl();
  const params = { ...DEFAULTS };
  const ctx = /** @type {import('../tidalviz').VisualizerContext} */ (
    /** @type {unknown} */ ({
      gl: fake.gl,
      params,
      size: { width: W, height: H, cssWidth: W / 2, cssHeight: H / 2, dpr: 2 },
      assets: builtinAssets,
      reduceMotion: false,
      reduceFlashing: true,
    })
  );
  const viz = await create(ctx);
  return { ...fake, params, viz };
}

const audio = /** @type {import('../tidalviz').AudioFrame} */ (
  /** @type {unknown} */ ({
    spectrum: new Float32Array(1024).fill(0.01),
    waveform: new Float32Array(2048),
    sampleRate: 48000,
    bassAtt: 1,
    midAtt: 1,
    onset: false,
    onsetStrength: 0,
  })
);

/** Run one frame and return its GL calls. @param {Awaited<ReturnType<typeof setup>>} s */
function frame(s, f = 0) {
  s.log.length = 0;
  s.viz.frame(audio, { now: f / 60, dt: 1 / 60, frame: f });
  return s.log;
}

const uniformsSet = (/** @type {{ op: string, args: any[] }[]} */ log) =>
  log.filter((c) => c.op.startsWith("uniform")).map((c) => c.args[0]?.name);

describe("eclipse GL calls", () => {
  it("sets the sampler units once, and the tint and sky only when they change", async () => {
    const s = await setup();
    frame(s, 0);
    const set = uniformsSet(frame(s, 1));
    for (const name of ["u_lev", "u_fields", "u_gamma", "u_sky"]) expect(set).not.toContain(name);

    s.params.tint = "sepia";
    s.viz.params?.({ tint: "sepia" });
    const gamma = s.log.filter((c) => c.op === "uniform3f" && c.args[0]?.name === "u_gamma").at(-1);
    expect(gamma?.args.slice(1).map((x) => Math.round(x * 100) / 100)).toEqual([0.82, 1, 1.35]);
  });

  it("masks the cloud field with a margin of at least 4 field texels around the empty disc", async () => {
    const s = await setup();
    const log = frame(s, 0);
    const field = log.filter((c) => c.op === "viewport")[0].args;
    const mask = log.find((c) => c.op === "uniform1f" && c.args[0]?.name === "u_mask");
    const { radius } = fitEclipse(W, H, { cx: 0, cy: 0, radius: 1 });
    const texel = Math.max(W / field[2], H / field[3]) / radius;
    expect(mask?.args[1]).toBeGreaterThanOrEqual(4 * texel);
    expect(mask?.args[1]).toBeLessThan(5 * texel);
  });

  it("draws over the whole canvas without clearing it first", async () => {
    const s = await setup();
    frame(s, 0);
    expect(frame(s, 1).filter((c) => c.op === "clear")).toEqual([]);
  });
});
