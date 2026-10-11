// @ts-check
/**
 * The Tetraballs blob as a signed distance field, mirrored from shaders/tetraballs/common.glsl so
 * the fusing behaviour and the bounds are tested here. Four spheres joined by a polynomial
 * smooth minimum (Quilez): within k of each other two surfaces melt into a liquid bridge; further
 * apart they're separate droplets.
 */

/**
 * Quadratic smooth minimum. ≤ min(a, b); equal to it once |a − b| ≥ k; k = 0 is a hard union.
 * @param {number} a
 * @param {number} b
 * @param {number} k blend width
 */
export function smin(a, b, k) {
  if (k <= 0) return Math.min(a, b);
  const h = Math.max(k - Math.abs(a - b), 0) / k;
  return Math.min(a, b) - h * h * k * 0.25;
}

/**
 * Signed distance from (x, y, z) to the blob of four balls.
 * @param {number} x
 * @param {number} y
 * @param {number} z
 * @param {Float32Array} centers 4 × xyz
 * @param {Float32Array} radii 4
 * @param {number} k blend width
 */
export function blobSdf(x, y, z, centers, radii, k) {
  let d = Infinity;
  for (let i = 0; i < 4; i++) {
    const s = Math.hypot(x - centers[i * 3], y - centers[i * 3 + 1], z - centers[i * 3 + 2]) - radii[i];
    d = i === 0 ? s : smin(d, s, k);
  }
  return d;
}

/**
 * Centre distance below which two balls of radius r are joined by a bridge (the blend reaches the
 * midpoint): at the midpoint both distances are D/2 − r, and smin(a, a, k) = a − k/4.
 * @param {number} r
 * @param {number} k
 */
export function bridgeDistance(r, k) {
  return 2 * r + k / 2;
}

/**
 * Radius of a sphere about the centroid that contains the whole blob when every ball sits at most
 * `maxDist` from the centroid with radius at most `maxR`. The smooth minimum can push the surface
 * out by up to k/4 where two balls blend, hence the margin. The raymarch is clipped to it.
 * @param {number} maxDist
 * @param {number} maxR
 * @param {number} k
 */
export function boundRadius(maxDist, maxR, k) {
  return maxDist + maxR + k * 0.25;
}

/** The camera of shaders/tetraballs/common.glsl: distance on +z, tan(half vertical field of view). */
export const CAM = 5;
export const TANF = 0.42;

/**
 * Scissor rectangles for a reduced-resolution pass (tw × th) that writes 0 wherever the ray misses
 * the sphere at (0, cy, 0) of radius r, and for its bilinear upscale onto the canvas (cw × ch),
 * blended so that 0 changes nothing. out = [vx0, vy0, vx1, vy1, cx0, cy0, cx1, cy1], half-open, in
 * window pixels (y up): the pass covers every texel the upscale samples inside its rectangle, and
 * every canvas pixel outside it samples only texels whose rays miss. The whole frame when the
 * camera is (nearly) inside the sphere.
 * @param {number} cy @param {number} r @param {number} cw @param {number} ch @param {number} tw @param {number} th
 * @param {Int32Array} out
 */
export function volumeScissor(cy, r, cw, ch, tw, th, out) {
  const q = CAM * CAM - r * r;
  if (q < 0.01 * CAM * CAM) {
    out.set([0, 0, tw, th, 0, 0, cw, ch]);
    return out;
  }
  // Slopes of the planes through the eye tangent to the sphere (x = s·depth, y = s·depth).
  const sx = r / Math.sqrt(q);
  const k = r * Math.sqrt(cy * cy + q);
  // Texel-space extent of the projection, one texel wider for float32 rounding in the shader.
  const ax = (sx / TANF) * 0.5 * th + 1;
  const ay0 = 0.5 * th + ((cy * CAM - k) / q / TANF) * 0.5 * th - 1;
  const ay1 = 0.5 * th + ((cy * CAM + k) / q / TANF) * 0.5 * th + 1;
  axis(0.5 * tw - ax, 0.5 * tw + ax, tw, cw, out, 0);
  axis(ay0, ay1, th, ch, out, 1);
  return out;
}

/**
 * One axis of {@link volumeScissor}: texels whose centres lie outside [a0, a1] are 0.
 * @param {number} a0 @param {number} a1 @param {number} t @param {number} c @param {Int32Array} out @param {number} i
 */
function axis(a0, a1, t, c, out, i) {
  const lo = Math.floor(a0);
  const hi = Math.ceil(a1);
  out[i] = clampInt(lo - 4, 0, t);
  out[i + 2] = clampInt(hi + 4, 0, t);
  // Canvas pixel X samples the texels around u = (X + ½)·t/c − ½: keep those within 1.5 texels.
  out[i + 4] = clampInt(Math.floor(((lo - 1.5) * c) / t - 0.5), 0, c);
  out[i + 6] = clampInt(Math.ceil(((hi + 1.5) * c) / t - 0.5) + 1, 0, c);
}

/** @param {number} x @param {number} lo @param {number} hi */
function clampInt(x, lo, hi) {
  return Math.min(hi, Math.max(lo, x));
}
