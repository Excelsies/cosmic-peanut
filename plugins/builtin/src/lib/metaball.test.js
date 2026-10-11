// @ts-check
import { describe, expect, it } from "vitest";
import { blobSdf, boundRadius, bridgeDistance, CAM, smin, TANF, volumeScissor } from "./metaball.js";
// @ts-expect-error -- a Node builtin (tests run in Node); the web tsconfig has no @types/node.
import { readFileSync } from "node:fs";
import { tetraVertices } from "./tetra-motion.js";

describe("smin", () => {
  it("is a union: never above min, equal to min once the two are more than k apart", () => {
    for (const [a, b] of [
      [0.3, 0.31],
      [-0.2, 0.1],
      [1, -1],
      [0.5, 0.5],
    ]) {
      expect(smin(a, b, 0.2)).toBeLessThanOrEqual(Math.min(a, b));
      expect(smin(a, b, 0.2)).toBe(smin(b, a, 0.2));
    }
    expect(smin(0.1, 0.6, 0.2)).toBe(0.1);
    expect(smin(0.5, 0.5, 0.2)).toBeCloseTo(0.5 - 0.05, 9); // deepest blend: k / 4 below
    expect(smin(0.2, 0.9, 0)).toBe(0.2); // k = 0 is a hard union
  });
});

describe("blobSdf", () => {
  const c = new Float32Array(12);
  const r = new Float32Array([0.3, 0.3, 0.3, 0.3]);
  /** Two balls on the x axis `d` apart; the other two far away. */
  const pair = (/** @type {number} */ d) => {
    c.set([-d / 2, 0, 0, d / 2, 0, 0, 0, 50, 0, 0, -50, 0]);
    return blobSdf(0, 0, 0, c, r, 0.24);
  };

  it("is the sphere distance near a lone ball", () => {
    c.set([0, 0, 0, 40, 0, 0, 0, 40, 0, 0, 0, 40]);
    expect(blobSdf(0.5, 0, 0, c, r, 0.24)).toBeCloseTo(0.2, 6);
    expect(blobSdf(0, 0, 0, c, r, 0.24)).toBeCloseTo(-0.3, 6);
  });

  it("pinches a liquid bridge between two balls closer than bridgeDistance, droplets beyond", () => {
    const dStar = bridgeDistance(0.3, 0.24);
    expect(dStar).toBeGreaterThan(0.6); // bridges reach past touching
    expect(pair(dStar * 0.95)).toBeLessThan(0); // midpoint is inside: a bridge
    expect(pair(dStar * 1.05)).toBeGreaterThan(0); // midpoint is outside: two droplets
  });
});

describe("boundRadius", () => {
  it("covers the whole blob at the largest balls and furthest bounce, in any orientation", () => {
    const k = 0.3;
    for (const [dist, rad] of [
      [0.2, 0.6], // squeezed together: one fused lump
      [0.9, 0.35],
      [1.6, 0.5], // burst apart
    ]) {
      const R = boundRadius(dist, rad, k);
      const v = tetraVertices(new Float32Array(12));
      const c = new Float32Array(12);
      const r = new Float32Array([rad, rad, rad, rad]);
      let nearest = Infinity;
      for (let a = 0; a < 6; a++) {
        // a few tumbles of the tetrahedron (rotate about z then x)
        const ca = Math.cos(a * 0.7);
        const sa = Math.sin(a * 0.7);
        for (let i = 0; i < 4; i++) {
          const x = v[i * 3] * ca - v[i * 3 + 1] * sa;
          const y = v[i * 3] * sa + v[i * 3 + 1] * ca;
          const z = v[i * 3 + 2];
          c[i * 3] = x * dist;
          c[i * 3 + 1] = (y * ca - z * sa) * dist;
          c[i * 3 + 2] = (y * sa + z * ca) * dist;
        }
        for (let j = 0; j < 400; j++) {
          // Fibonacci sphere of directions
          const zz = 1 - (2 * (j + 0.5)) / 400;
          const rr = Math.sqrt(1 - zz * zz);
          const ph = j * 2.399963;
          const d = blobSdf(R * rr * Math.cos(ph), R * rr * Math.sin(ph), R * zz, c, r, k);
          nearest = Math.min(nearest, d);
        }
      }
      expect(nearest, `${dist} ${rad}`).toBeGreaterThanOrEqual(0);
      expect(nearest, `${dist} ${rad} (not wastefully loose)`).toBeLessThan(0.35);
    }
  });
});

describe("volumeScissor", () => {
  const f = Math.fround;
  /** common.glsl rayDir + sphereHit at a texel centre, in float32: does the ray enter the sphere? */
  function hits(/** @type {number} */ i, /** @type {number} */ j, /** @type {number} */ tw, /** @type {number} */ th, /** @type {number} */ cy, /** @type {number} */ R) {
    const px = f(f(f(i + 0.5) - f(0.5 * tw)) / f(0.5 * th));
    const py = f(f(f(j + 0.5) - f(0.5 * th)) / f(0.5 * th));
    const dx = f(px * f(TANF));
    const dy = f(py * f(TANF));
    const n = f(Math.sqrt(f(f(f(dx * dx) + f(dy * dy)) + 1)));
    const [rx, ry, rz] = [f(dx / n), f(dy / n), f(-1 / n)];
    const [ox, oy, oz] = [0, f(-cy), f(CAM)];
    const b = f(f(f(ox * rx) + f(oy * ry)) + f(oz * rz));
    const c = f(f(f(f(ox * ox) + f(oy * oy)) + f(oz * oz)) - f(f(R) * f(R)));
    const h = f(f(b * b) - c);
    return h >= 0 && f(-b + f(Math.sqrt(h))) > f(-b - f(Math.sqrt(h)));
  }

  it("leaves out only canvas pixels that sample nothing, and lets the rest sample only texels it writes", () => {
    const out = new Int32Array(8);
    const cases = [
      [1280, 720, 0.4, 0.3, 1.0],
      [2560, 1440, 0.6, 0.55, 1.65],
      [1000, 563, 0.5, 1.2, 1.4], // reaching past the top edge
      [801, 601, 0.5, -0.4, 0.25],
      [1920, 1080, 0.5, 0.0, 4.9], // camera nearly inside: whole pass
    ];
    for (const [cw, ch, s, cy, R] of cases) {
      const tw = Math.ceil(cw * s);
      const th = Math.ceil(ch * s);
      volumeScissor(cy, R, cw, ch, tw, th, out);
      const [vx0, vy0, vx1, vy1, cx0, cy0, cx1, cy1] = out;
      const colHit = new Uint8Array(tw);
      const rowHit = new Uint8Array(th);
      for (let j = 0; j < th; j++)
        for (let i = 0; i < tw; i++)
          if (hits(i, j, tw, th, cy, R)) colHit[i] = rowHit[j] = 1;
      expect(colHit.includes(1)).toBe(true);
      /** For each canvas line along one axis: its two bilinear tap lines, clamped to the texture. */
      const check = (/** @type {number} */ c, /** @type {number} */ t, /** @type {Uint8Array} */ hit, /** @type {number} */ v0, /** @type {number} */ v1, /** @type {number} */ c0, /** @type {number} */ c1) => {
        expect(0 <= v0 && v0 <= v1 && v1 <= t && 0 <= c0 && c0 <= c1 && c1 <= c).toBe(true);
        for (let X = 0; X < c; X++) {
          const u = ((X + 0.5) * t) / c - 0.5;
          const i0 = Math.min(t - 1, Math.max(0, Math.floor(u)));
          const i1 = Math.min(t - 1, Math.max(0, Math.floor(u) + 1));
          if (X < c0 || X >= c1) expect(hit[i0] || hit[i1]).toBe(0);
          else expect(i0 >= v0 && i1 < v1).toBe(true);
        }
      };
      check(cw, tw, colHit, vx0, vx1, cx0, cx1);
      check(ch, th, rowHit, vy0, vy1, cy0, cy1);
      if (R < 4) expect((cx1 - cx0) * (cy1 - cy0)).toBeLessThan(cw * ch);
    }
  });

  it("shares the camera with common.glsl", () => {
    const src = readFileSync(new URL("../../shaders/tetraballs/common.glsl", import.meta.url), "utf8");
    expect(Number(src.match(/const float CAM = ([\d.]+);/)?.[1])).toBe(CAM);
    expect(Number(src.match(/const float TANF = ([\d.]+);/)?.[1])).toBe(TANF);
  });
});
