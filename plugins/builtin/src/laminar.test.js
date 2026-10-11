// @ts-check
import { describe, expect, it } from "vitest";
import create from "./laminar.js";
import { builtinAssets, fakeGl } from "./lib/fake-gl.test-helper.js";

const DEFAULTS = {
  reactivity: 1,
  speed: 1,
  turbulence: 1,
  density: 72,
  size: 1,
  detail: 0.2,
  trail: true,
  palette: "currents",
  backdrop: "black",
};

async function setup() {
  const fake = fakeGl();
  const ctx = /** @type {import('../tidalviz').VisualizerContext} */ (
    /** @type {unknown} */ ({
      gl: fake.gl,
      params: { ...DEFAULTS },
      size: { width: 1280, height: 720, cssWidth: 640, cssHeight: 360, dpr: 2 },
      assets: builtinAssets,
      reduceMotion: false,
      reduceFlashing: true,
    })
  );
  const created = fake.log.length;
  const viz = await create(ctx);
  return { ...fake, viz, created, f: 0 };
}

const audio = /** @type {import('../tidalviz').AudioFrame} */ (
  /** @type {unknown} */ ({
    bands: new Float32Array(64).fill(0.3),
    spectrum: new Float32Array(1024),
    waveform: new Float32Array(2048),
    rms: 0.3,
    peak: 0.6,
    centroid: 0.3,
    bassAtt: 1,
    onset: false,
    onsetStrength: 0,
    silent: false,
  })
);

/** One frame (one sim step); returns its GL calls. @param {Awaited<ReturnType<typeof setup>>} s */
function frame(s) {
  const from = s.log.length;
  s.viz.frame(audio, { now: s.f / 60, dt: 1 / 60, frame: s.f++ });
  return s.log.slice(from);
}

/** Which laminar pass a fake program is, from its fragment source. @param {any} p */
const progName = (p) => {
  const fs = p.shaders[1].src;
  const marks = [
    ["advect", "sampler2D u_fv;"],
    ["correct", "sampler2D u_v0;"],
    ["curl", "Vorticity (z of curl)"],
    ["viscous", "sampler2D u_x;"],
    ["divergence", "Divergence of the velocity"],
    ["pressure", "sampler2D u_div;"],
    ["project", "sampler2D u_p;"],
    ["resample", "u_Lold;"],
    ["composite", "sampler2D u_scal;"],
  ];
  return marks.find(([, m]) => fs.includes(m))?.[0] ?? "";
};

/**
 * Replay a call log, tracking the active unit, the textures bound per unit, the storage format of
 * every texture and the current program, and hand every draw the state it sees.
 * @param {Awaited<ReturnType<typeof setup>>} s
 * @param {(d: { program: string, units: any[], formats: Map<any, number> }) => void} onDraw
 */
function replay(s, onDraw) {
  let active = 0;
  /** @type {any[]} */
  const units = [];
  const formats = new Map();
  let program = "";
  for (const { op, args } of s.log) {
    if (op === "activeTexture") active = args[0] - s.gl.TEXTURE0;
    if (op === "bindTexture") units[active] = args[1];
    if (op === "texImage2D") formats.set(units[active], args[2]);
    if (op === "useProgram") program = progName(args[0]);
    if (op === "drawArrays") onDraw({ program, units, formats });
  }
}

describe("laminar GL calls", () => {
  it("sets sampler units, and the sim's grid uniforms, outside the frame; never re-selects the active unit", async () => {
    const s = await setup();
    frame(s);
    const log = frame(s);
    expect(log.filter((c) => c.op === "uniform1i")).toEqual([]);
    let program = "";
    /** @type {string[]} */
    const grid = [];
    for (const c of log) {
      if (c.op === "useProgram") program = progName(c.args[0]);
      if (["u_L", "u_texel"].includes(c.args[0]?.name)) grid.push(`${program}.${c.args[0].name}`);
    }
    expect(grid).toEqual(["composite.u_L"]);
    let active = -1;
    for (const c of s.log.slice(s.created)) {
      if (c.op !== "activeTexture") continue;
      expect(c.args[0]).not.toBe(active);
      active = c.args[0];
    }
  });

  it("sets every sampler's unit once, at creation", async () => {
    const s = await setup();
    /** @type {Map<string, number>} */
    const units = new Map();
    let program = "";
    for (const c of s.log) {
      if (c.op === "useProgram") program = progName(c.args[0]);
      if (c.op === "uniform1i") units.set(`${program}.${c.args[0].name}`, c.args[1]);
    }
    expect(Object.fromEntries(units)).toMatchObject({
      "advect.u_vel": 0,
      "advect.u_fv": 1,
      "advect.u_fs": 2,
      "correct.u_v0": 0,
      "correct.u_s0": 1,
      "correct.u_vf": 2,
      "correct.u_sf": 3,
      "correct.u_vb": 4,
      "correct.u_sb": 5,
      "correct.u_curl": 6,
      "curl.u_vel": 0,
      "viscous.u_x": 0,
      "viscous.u_b": 1,
      "divergence.u_vel": 0,
      "pressure.u_p": 0,
      "pressure.u_div": 1,
      "project.u_vel": 0,
      "project.u_p": 1,
      "resample.u_old": 0,
      "composite.u_scal": 0,
    });
  });
});
