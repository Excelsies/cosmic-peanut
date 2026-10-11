// @ts-check
// @ts-expect-error -- a Node builtin (tests run in Node); the web tsconfig has no @types/node.
import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import { createAudioFrame } from "../../../web/sdk/frame.js";
import create from "./blaze.js";
import { fakeWebGL2 } from "./lib/fake-webgl2.test-helper.js";

const ROOT = new URL("../", import.meta.url);
const manifest = JSON.parse(await readFile(new URL("tidalviz.json", ROOT), "utf8"));
const DEFAULTS = Object.fromEntries(
  manifest.visualizers.find((/** @type {{ id: string }} */ v) => v.id === "blaze").params.map(
    (/** @type {{ id: string, default: unknown }} */ p) => [p.id, p.default],
  ),
);

/** @param {string[]} [extensions] */
async function setup(extensions) {
  const fake = fakeWebGL2({ extensions });
  const state = { reduceMotion: false };
  /** @type {Record<string, unknown>} */
  const params = { ...DEFAULTS };
  const size = { width: 2560, height: 1440, cssWidth: 1280, cssHeight: 720, dpr: 2 };
  const ctx = /** @type {import('../tidalviz').VisualizerContext} */ (
    /** @type {unknown} */ ({
      gl: fake.gl,
      params,
      size,
      get reduceMotion() {
        return state.reduceMotion;
      },
      reduceFlashing: true,
      assets: { text: (/** @type {string} */ p) => readFile(new URL(p, ROOT), "utf8") },
    })
  );
  const viz = /** @type {import('../tidalviz').Visualizer} */ (await create(ctx));
  const audio = createAudioFrame();
  let f = 0;
  /** One frame of `dt` seconds (1/60 s = 2 sim steps); returns its draws and uniform uploads. */
  const frame = (dt = 1 / 60) => {
    fake.draws.length = 0;
    fake.uploads.length = 0;
    viz.frame(audio, { now: f / 60, dt, frame: f++ });
    return { draws: [...fake.draws], uploads: [...fake.uploads] };
  };
  return { ...fake, params, size, viz, state, frame };
}

/** @param {{ program: string }[]} draws @param {string} label */
const last = (draws, label) => /** @type {any} */ (draws.filter((d) => d.program.startsWith(label)).at(-1));

describe("blaze GL state", () => {
  it("narrows the sim targets on the half-float path and keeps RGBA8 without it", async () => {
    const half = await setup();
    const d = last(half.frame().draws, "Velocity step");
    expect(d.textures.u_vel.format).toBe("RG16F");
    expect(d.textures.u_scal.format).toBe("RG16F");
    expect(d.textures.u_curl.format).toBe("R16F");
    const none = await setup([]);
    const e = last(none.frame().draws, "Velocity step");
    for (const t of ["u_vel", "u_scal", "u_curl"]) expect(e.textures[t].format).toBe("RGBA8");
  });
});
