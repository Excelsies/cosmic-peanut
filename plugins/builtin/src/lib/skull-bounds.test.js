// @ts-check
import { describe, expect, it } from "vitest";
import { JAW_LIMIT, PULSE_LIMIT } from "./skull-motion.js";
import {
  CAM_DIST,
  glslDefines,
  hullSpheres,
  JAW_BOUND_C,
  JAW_BOUND_R,
  screenRadius,
  SHAPES,
  SKULL_RADIUS,
  TOOTH,
  TOOTH_ROUND,
  TOOTH_SIDE,
  TOOTH_STEP,
} from "./skull-bounds.js";

// skull.frag's teeth and jaw distance functions, in JS (jaw-space point j for the jaw).
/** @typedef {[number, number, number]} V */
const len = Math.hypot;
/** @param {number} a @param {number} b @param {number} k */
const smin = (a, b, k) => {
  const h = Math.min(1, Math.max(0, 0.5 + (0.5 * (b - a)) / k));
  return b + (a - b) * h - k * h * (1 - h);
};
/** @param {V} p @param {number[]} c @param {number[]} r */
const sdEllipsoid = (p, c, r) => {
  const q = p.map((v, i) => v - c[i]);
  const k0 = len(...q.map((v, i) => v / r[i]));
  const k1 = len(...q.map((v, i) => v / (r[i] * r[i])));
  return (k0 * (k0 - 1)) / Math.max(k1, 1e-5);
};
/** @param {V} p @param {number[]} a @param {number[]} b @param {number} r */
const sdCapsule = (p, a, b, r) => {
  const pa = p.map((v, i) => v - a[i]);
  const ba = b.map((v, i) => v - a[i]);
  const h = Math.min(1, Math.max(0, (pa[0] * ba[0] + pa[1] * ba[1] + pa[2] * ba[2]) / (ba[0] ** 2 + ba[1] ** 2 + ba[2] ** 2)));
  return len(...pa.map((v, i) => v - ba[i] * h)) - r;
};
/** @param {V} p @param {number[]} c @param {number} arc */
const sdTeeth = (p, c, arc) => {
  const [qx, qy, qz] = p.map((v, i) => v - c[i]);
  const id = Math.min(TOOTH_SIDE, Math.max(-TOOTH_SIDE, Math.floor(Math.atan2(qx, qz) / TOOTH_STEP + 0.5)));
  const co = Math.cos(id * TOOTH_STEP);
  const si = Math.sin(id * TOOTH_STEP);
  const q = [Math.abs(co * qx - si * qz), Math.abs(qy), Math.abs(si * qx + co * qz - arc)];
  const b = [TOOTH[0], TOOTH[1] * (1 - 0.07 * Math.abs(id)), TOOTH[2]].map((v, i) => q[i] - v);
  return len(...b.map((v) => Math.max(v, 0))) + Math.min(Math.max(...b), 0) - TOOTH_ROUND;
};
/** @param {V} j */
const sdJaw = (j) => {
  const S = /** @type {Record<string, any>} */ (SHAPES);
  /** @type {V} */
  const m = [Math.abs(j[0]), j[1], j[2]];
  let d = sdEllipsoid(j, S.CHIN.c, S.CHIN.r);
  d = smin(d, sdCapsule(m, S.JAW_BODY.a, S.JAW_BODY.b, S.JAW_BODY.r), 0.1);
  d = smin(d, sdCapsule(m, S.RAMUS.a, S.RAMUS.b, S.RAMUS.r), 0.08);
  return smin(d, sdTeeth(j, S.TEETH_DOWN.c, S.TEETH_DOWN.arc), 0.015);
};
/** Grid points (x ≥ 0: the parts are mirror-symmetric) in a ball of radius `r` round the origin. */
function* ball(/** @type {number} */ r, /** @type {number} */ step) {
  for (let x = 0; x <= r; x += step)
    for (let y = -r; y <= r; y += step)
      for (let z = -r; z <= r; z += step) if (len(x, y, z) <= r) yield /** @type {V} */ ([x, y, z]);
}

describe("skull bounding sphere", () => {
  it("contains every part of the skull at every jaw angle", () => {
    for (let i = 0; i <= 20; i++) {
      const jaw = (JAW_LIMIT * i) / 20;
      for (const [x, y, z, r] of hullSpheres(jaw)) {
        expect(Math.hypot(x, y, z) + r, `jaw ${jaw}`).toBeLessThanOrEqual(SKULL_RADIUS);
      }
    }
  });
});

describe("map()'s jaw bound (skull.frag)", () => {
  // map() runs within SKULL_RADIUS plus the normal and AO offsets (0.18); the jaw frame turns that
  // about the hinge, which is 0.43 from the origin, by up to JAW_LIMIT.
  const REACH = SKULL_RADIUS + 0.19;
  const JAW_REACH = REACH + 2 * 0.43 * Math.sin(JAW_LIMIT / 2);

  it("the jaw's distance function is never below its bounding sphere, in the jaw's frame", () => {
    // Below this floor sdJaw can't go: each part is >= -(its largest radius), each smin dips at
    // most k/4 under the min. Where the sphere bound is under it, it holds without sampling, and
    // that includes the chin ellipsoid's centre, where the approximate distance is discontinuous.
    const S = /** @type {Record<string, any>} */ (SHAPES);
    const floor = -Math.max(...S.CHIN.r, S.JAW_BODY.r, S.RAMUS.r, TOOTH[2] + TOOTH_ROUND) - (0.1 + 0.08 + 0.015) / 4;
    const bound = (/** @type {V} */ j) => len(...j.map((v, i) => v - JAW_BOUND_C[i])) - JAW_BOUND_R;
    expect(len(...S.CHIN.c.map((/** @type {number} */ v, /** @type {number} */ i) => v - JAW_BOUND_C[i])) + 0.1 - JAW_BOUND_R).toBeLessThan(floor);
    let slack = Infinity;
    const check = (/** @type {V} */ j) => {
      const b = bound(j);
      if (b > floor) slack = Math.min(slack, sdJaw(j) - b);
    };
    for (const j of ball(JAW_REACH, 0.03)) check(j);
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
    for (let i = 0; i < 100000; i++) {
      /** @type {V} */
      const j = [rnd() * JAW_REACH, rnd() * JAW_REACH, rnd() * JAW_REACH];
      if (len(...j) <= JAW_REACH) check(j);
    }
    // A margin for what lies between the samples.
    expect(slack).toBeGreaterThan(0.05);
  });

  it("is injected into the shader", () => {
    const src = glslDefines();
    expect(src).toContain(`#define JAW_BOUND_R ${JAW_BOUND_R}`);
    expect(src).toMatch(/#define JAW_BOUND_C vec3\(/);
  });
});

describe("screenRadius", () => {
  it("is a tight circle around the bounding sphere's perspective silhouette", () => {
    for (const focal of [1.2, 3.3, 6]) {
      for (const scale of [0.5, 1, 1.5 * (1 + PULSE_LIMIT)]) {
        const R = SKULL_RADIUS * scale;
        const circle = screenRadius(scale, focal);
        let far = 0;
        for (let i = 0; i < 400; i++) {
          // Points on the sphere (Fibonacci lattice), projected by a camera at z = CAM_DIST.
          const y = 1 - (2 * (i + 0.5)) / 400;
          const ring = Math.sqrt(1 - y * y);
          const a = i * 2.399963;
          const [px, py, pz] = [R * ring * Math.cos(a), R * y, R * ring * Math.sin(a)];
          far = Math.max(far, (focal * Math.hypot(px, py)) / (CAM_DIST - pz));
        }
        expect(far, `${focal} ${scale}`).toBeLessThanOrEqual(circle);
        expect(far, `${focal} ${scale}`).toBeGreaterThan(0.97 * circle);
      }
    }
  });
});

describe("glslDefines", () => {
  it("writes every shape of the table as #defines for the shader", () => {
    const src = glslDefines();
    expect(src).toContain("#define CRANIUM_C vec3(0.0, 0.28, -0.12)");
    expect(src).toContain("#define CRANIUM_R vec3(0.9, 0.92, 1.02)");
    expect(src).toContain("#define ARCH_A vec3(0.58, -0.22, 0.38)");
    expect(src).toContain("#define ARCH_R 0.075");
    expect(src).toContain("#define TEETH_UP_C vec3(0.0, -0.6, 0.12)");
    expect(src).toContain("#define TEETH_UP_ARC 0.4");
    expect(src).toContain("#define JAW_HINGE vec3(0.0, -0.42, -0.1)");
    expect(src).toContain("#define SKULL_RADIUS 1.42");
    expect(src).toContain("#define TOOTH_SIDE 4.0");
  });
});
