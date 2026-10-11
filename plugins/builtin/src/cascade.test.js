// @ts-check
// @ts-expect-error -- a Node builtin (tests run in Node); the web tsconfig has no @types/node.
import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import { createAudioFrame } from "../../../web/sdk/frame.js";
import create from "./cascade.js";
import { fakeWebGL2 } from "./lib/fake-webgl2.test-helper.js";

const ROOT = new URL("../", import.meta.url);
const manifest = JSON.parse(await readFile(new URL("tidalviz.json", ROOT), "utf8"));
const DEFAULTS = Object.fromEntries(
  manifest.visualizers.find((/** @type {{ id: string }} */ v) => v.id === "cascade").params.map(
    (/** @type {{ id: string, default: unknown }} */ p) => [p.id, p.default],
  ),
);

/** The textures one frame's draws sample, by uniform name (the last draw that reads each). @param {string[]} extensions */
async function sampled(extensions) {
  const fake = fakeWebGL2({ extensions });
  const ctx = /** @type {import('../tidalviz').VisualizerContext} */ (
    /** @type {unknown} */ ({
      gl: fake.gl,
      params: { ...DEFAULTS },
      size: { width: 2560, height: 1440, cssWidth: 1280, cssHeight: 720, dpr: 2 },
      reduceMotion: false,
      reduceFlashing: true,
      assets: { text: (/** @type {string} */ p) => readFile(new URL(p, ROOT), "utf8") },
    })
  );
  const viz = /** @type {import('../tidalviz').Visualizer} */ (await create(ctx));
  viz.frame(createAudioFrame(), { now: 0, dt: 1 / 60, frame: 0 });
  return Object.assign({}, ...fake.draws.map((d) => d.textures));
}

describe("cascade mist", () => {
  it.each([["EXT_color_buffer_float"], ["EXT_color_buffer_half_float"]])(
    "keeps the one-channel mist in R16F targets (%s)",
    async (ext) => {
      const t = await sampled([ext]);
      for (const name of ["u_prev", "u_src", "u_mist"]) expect(t[name].format).toBe("R16F");
    },
  );
});
