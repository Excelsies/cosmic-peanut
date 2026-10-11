// @ts-check
import { describe, expect, it } from "vitest";
import { createTarget } from "./gl.js";

/** Enough of a WebGL2 context to record what createTarget allocates. */
function fakeGL(/** @type {boolean} */ float) {
  /** @type {unknown[][]} */
  const images = [];
  const gl = {
    TEXTURE_2D: 1, FRAMEBUFFER: 2, COLOR_ATTACHMENT0: 3, COLOR_BUFFER_BIT: 4,
    RGBA16F: 10, RGBA8: 11, R16F: 12, R8: 13, RGBA: 20, RED: 21, HALF_FLOAT: 30, UNSIGNED_BYTE: 31,
    getExtension: (/** @type {string} */ name) => (float && name === "EXT_color_buffer_float" ? {} : null),
    createTexture: () => ({}),
    createFramebuffer: () => ({}),
    bindTexture() {},
    texParameteri() {},
    texImage2D: (/** @type {unknown[]} */ ...a) => images.push([a[2], a[6], a[7]]),
    bindFramebuffer() {},
    framebufferTexture2D() {},
    clearColor() {},
    clear() {},
  };
  return { gl: /** @type {WebGL2RenderingContext} */ (/** @type {unknown} */ (gl)), images, g: gl };
}

describe("createTarget", () => {
  it("allocates RGBA16F where float rendering is available, else RGBA8", () => {
    for (const float of [true, false]) {
      const { gl, images, g } = fakeGL(float);
      const t = createTarget(gl);
      t.resize(4, 2);
      expect(t.float).toBe(float);
      expect(images).toEqual([float ? [g.RGBA16F, g.RGBA, g.HALF_FLOAT] : [g.RGBA8, g.RGBA, g.UNSIGNED_BYTE]]);
    }
  });

  it("allocates one channel (R16F, else R8) for a red-only target", () => {
    for (const float of [true, false]) {
      const { gl, images, g } = fakeGL(float);
      const t = createTarget(gl, { red: true });
      t.resize(4, 2);
      expect(t.float).toBe(float);
      expect(images).toEqual([float ? [g.R16F, g.RED, g.HALF_FLOAT] : [g.R8, g.RED, g.UNSIGNED_BYTE]]);
    }
  });
});
