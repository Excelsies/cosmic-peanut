// @ts-check
// @ts-expect-error -- a Node builtin (tests run in Node); the web tsconfig has no @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import create from "./cosmic-peanut.js";
import { ringGain } from "./lib/params.js";

const ROOT = new URL("../", import.meta.url);
const read = (/** @type {string} */ p) => readFileSync(new URL(p, ROOT), "utf8");
/** @type {Record<string, any>} */
const DEFAULTS = Object.fromEntries(
  JSON.parse(read("tidalviz.json")).visualizers[0].params.map((/** @type {any} */ p) => [p.id, p.default]),
);
const AUDIO = { waveform: new Float32Array(2048), bass: 1 };
const TIME = { dt: 1 / 60, now: 0 };

/** @typedef {{ name: string, args: any[], fb: any, viewport: number[], program: any }} Draw */

/**
 * Recording WebGL2 stand-in: enums are their own names, created objects are tagged, every call is
 * logged as [name, ...args] and every draw with the framebuffer, viewport and program it hit.
 * Uniform locations are { program, name }; setting one while another program is bound throws,
 * like the GL error it would be.
 */
function fakeGL() {
  /** @type {any[][]} */
  const calls = [];
  /** @type {Draw[]} */
  const draws = [];
  /** @type {any} */
  let program = null;
  /** @type {any} */
  let fb = null;
  /** @type {number[]} */
  let viewport = [];
  let id = 0;
  /** @type {Record<string, (...a: any[]) => any>} */
  const special = {
    getParameter: (p) => (p === "MAX_SAMPLES" ? 4 : 0),
    getShaderParameter: () => true,
    getProgramParameter: (p, k) => (k === "ACTIVE_UNIFORMS" ? p.uniforms.length : true),
    getActiveUniform: (p, i) => ({ name: p.uniforms[i] }),
    getUniformLocation: (p, name) => ({ program: p, name }),
    isContextLost: () => false,
    createShader: () => ({ id: ++id, src: "" }),
    shaderSource: (s, src) => (s.src = src),
    createProgram: () => ({ id: ++id, uniforms: [] }),
    attachShader: (p, s) => {
      for (const m of s.src.matchAll(/\buniform\s+\w+\s+([\w\s,]+);/g))
        for (const n of m[1].split(",")) if (!p.uniforms.includes(n.trim())) p.uniforms.push(n.trim());
    },
    useProgram: (p) => (program = p),
    bindFramebuffer: (t, f) => {
      if (t === "FRAMEBUFFER" || t === "DRAW_FRAMEBUFFER") fb = f;
    },
    viewport: (...v) => (viewport = v),
  };
  const gl = new Proxy(
    {},
    {
      get(_, k) {
        if (k === "calls") return calls;
        if (k === "draws") return draws;
        if (typeof k !== "string") return undefined;
        if (/^[A-Z0-9_]+$/.test(k)) return k;
        return (/** @type {any[]} */ ...args) => {
          calls.push([k, ...args]);
          if (k.startsWith("uniform") && args[0]?.program !== program) {
            throw new Error(`${k}(${args[0]?.name}) without its program bound`);
          }
          if (k.startsWith("draw")) draws.push({ name: k, args, fb, viewport, program });
          if (k in special) return special[k](...args);
          if (k.startsWith("create")) return { id: ++id };
          return undefined;
        };
      },
    },
  );
  return /** @type {any} */ (gl);
}

/** @param {{ dpr?: number, css?: [number, number], params?: Record<string, any> }} [o] */
async function setup({ dpr = 2, css = [1280, 720], params = {} } = {}) {
  const gl = fakeGL();
  const ctx = {
    gl,
    size: { width: css[0] * dpr, height: css[1] * dpr, cssWidth: css[0], cssHeight: css[1], dpr },
    params: { ...DEFAULTS, ...params },
    reduceMotion: false,
    assets: {
      text: async (/** @type {string} */ p) => read(p),
      json: async (/** @type {string} */ p) => JSON.parse(read(p)),
    },
  };
  const viz = await create(/** @type {any} */ (ctx));
  /** @param {Record<string, any>} changed */
  const setParams = (changed) => {
    Object.assign(ctx.params, changed);
    viz.params?.(changed);
  };
  return { gl, ctx, viz, setParams };
}

/** [width, height] of every RGBA8 texture allocation so far (the soft-lines resolve texture). */
const softTextures = (/** @type {any} */ gl) =>
  gl.calls
    .filter((/** @type {any[]} */ c) => c[0] === "texImage2D" && c[3] === "RGBA8")
    .map((/** @type {any[]} */ c) => [c[4], c[5]]);

/**
 * Run one frame and return its draws.
 * @param {any} gl
 * @param {any} viz
 * @returns {Draw[]}
 */
function frameDraws(gl, viz) {
  const n = gl.draws.length;
  viz.frame(AUDIO, TIME);
  return gl.draws.slice(n);
}

/** Value last given to the uniform `name`. @param {any} gl @param {string} name */
const lastUniform = (gl, name) =>
  gl.calls.findLast((/** @type {any[]} */ c) => c[0].startsWith("uniform") && c[1].name === name)?.[2];

describe("cosmic-peanut GL", () => {
  it("holds only a 1×1 soft texture in Fine lines, and frees it again on Soft → Fine", async () => {
    const { gl, setParams } = await setup({ params: { lines: "fine" } });
    expect(softTextures(gl).at(-1)).toEqual([1, 1]);
    setParams({ lines: "soft", antialias: false });
    expect(softTextures(gl).at(-1)).toEqual([1280, 720]);
    setParams({ lines: "fine" });
    expect(softTextures(gl).at(-1)).toEqual([1, 1]);
  });

  it("draws plain Soft lines straight to the canvas when the 1× buffer would be canvas-sized", async () => {
    const { gl, ctx, viz, setParams } = await setup({ dpr: 1, css: [2560, 1440], params: { antialias: false } });
    expect(softTextures(gl).at(-1)).toEqual([1, 1]);
    expect(frameDraws(gl, viz).map((d) => [d.fb, d.viewport])).toEqual([[null, [0, 0, 2560, 1440]]]);
    expect(lastUniform(gl, "uGain")).toBe(ringGain(DEFAULTS.bright, 240, false)); // still Soft gain

    Object.assign(ctx.size, { width: 5120, height: 2880, dpr: 2 }); // same CSS size, now DPR 2
    viz.resize?.(ctx.size);
    expect(softTextures(gl).at(-1)).toEqual([2560, 1440]);
    expect(frameDraws(gl, viz).length).toBe(2);
    setParams({ antialias: true });
    expect(frameDraws(gl, viz).length).toBe(2);
  });

  it("keeps the 1× buffer and its upscale when it is smaller than the canvas, or multisampled", async () => {
    for (const [dpr, antialias] of [
      [2, false],
      [2, true],
      [1, true],
    ]) {
      const { gl, viz } = await setup({ dpr: Number(dpr), params: { antialias } });
      const [rings, blit, ...rest] = frameDraws(gl, viz);
      expect(rings.fb).not.toBeNull();
      expect(rings.viewport).toEqual([0, 0, 1280, 720]);
      expect([blit.name, blit.fb, blit.viewport]).toEqual(["drawArrays", null, [0, 0, 1280 * Number(dpr), 720 * Number(dpr)]]);
      expect(rest).toEqual([]);
      expect(softTextures(gl).at(-1)).toEqual([1280, 720]);
    }
  });

  it("draws one instanced line strip per ring but the oldest, with no vertex or index buffers", async () => {
    // The oldest ring has age (N − 1 + frac) / (N − 1) ≥ 1: fully faded, it adds exactly zero.
    const { gl, viz, setParams } = await setup();
    const [rings] = frameDraws(gl, viz);
    expect([rings.name, ...rings.args]).toEqual(["drawArraysInstanced", "LINE_STRIP", 0, 513, 239]);
    setParams({ density: "dense" });
    expect(frameDraws(gl, viz)[0].args).toEqual(["LINE_STRIP", 0, 513, 399]);
    expect(gl.calls.filter((/** @type {any[]} */ c) => c[0] === "createBuffer" || c[0] === "bufferData")).toEqual([]);
  });
});
