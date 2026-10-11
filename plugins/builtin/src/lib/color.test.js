// @ts-check
import { expect, it } from "vitest";
import { hexToRgb, hueRotation } from "./color.js";

it("parses #rrggbb into 0–1 floats in place", () => {
  const out = new Float32Array(3);
  expect(hexToRgb("#ff8000", out)).toBe(out);
  expect(out[0]).toBeCloseTo(1);
  expect(out[1]).toBeCloseTo(128 / 255);
  expect(out[2]).toBeCloseTo(0);
});

it("falls back to white for anything that isn't #rrggbb", () => {
  const out = new Float32Array(3);
  hexToRgb("red", out);
  expect(Array.from(out)).toEqual([1, 1, 1]);
});

it("builds the column-major RGB matrix of a YIQ chroma rotation in place", () => {
  /** @param {number[]} m row-major 3×3 @param {number[]} v */
  const mul = (m, v) => [0, 1, 2].map((r) => m[3 * r] * v[0] + m[3 * r + 1] * v[1] + m[3 * r + 2] * v[2]);
  const toYiq = [0.299, 0.587, 0.114, 0.596, -0.274, -0.322, 0.211, -0.523, 0.312];
  const toRgb = [1, 0.956, 0.621, 1, -0.272, -0.647, 1, -1.106, 1.703];
  const out = new Float32Array(9);
  for (const a of [0, 0.01, -0.3, 2]) {
    expect(hueRotation(a, out)).toBe(out);
    for (const c of [[1, 0, 0], [0.2, 0.7, 0.4], [0.9, 0.9, 0.1]]) {
      const y = mul(toYiq, c);
      const want = mul(toRgb, [y[0], y[1] * Math.cos(a) - y[2] * Math.sin(a), y[1] * Math.sin(a) + y[2] * Math.cos(a)]);
      for (let r = 0; r < 3; r++) {
        expect(out[r] * c[0] + out[3 + r] * c[1] + out[6 + r] * c[2]).toBeCloseTo(want[r], 6);
      }
    }
  }
});
