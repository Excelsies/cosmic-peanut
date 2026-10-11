// @ts-check
/**
 * Parse a `#rrggbb` param value into 0–1 floats (e.g. for a vec3 uniform). Anything else → white.
 * @template {Float32Array | number[]} T
 * @param {unknown} hex
 * @param {T} out length ≥ 3
 * @returns {T}
 */
export function hexToRgb(hex, out) {
  const m = typeof hex === "string" ? /^#([0-9a-f]{6})$/i.exec(hex) : null;
  const n = m ? parseInt(m[1], 16) : 0xffffff;
  out[0] = ((n >> 16) & 255) / 255;
  out[1] = ((n >> 8) & 255) / 255;
  out[2] = (n & 255) / 255;
  return out;
}

const TO_YIQ = [0.299, 0.587, 0.114, 0.596, -0.274, -0.322, 0.211, -0.523, 0.312]; // rows Y, I, Q
const TO_RGB = [1, 1, 1, 0.956, -0.272, -1.106, 0.621, -0.647, 1.703]; // columns Y, I, Q

/**
 * The RGB matrix (column-major, for a mat3 uniform) that rotates chroma by `a` radians in YIQ.
 * @template {Float32Array | number[]} T
 * @param {number} a
 * @param {T} out length ≥ 9
 * @returns {T}
 */
export function hueRotation(a, out) {
  const cs = Math.cos(a);
  const sn = Math.sin(a);
  for (let i = 0; i < 3; i++) {
    const y = TO_RGB[i];
    const ci = TO_RGB[3 + i] * cs + TO_RGB[6 + i] * sn;
    const cq = TO_RGB[6 + i] * cs - TO_RGB[3 + i] * sn;
    for (let j = 0; j < 3; j++) out[3 * j + i] = y * TO_YIQ[j] + ci * TO_YIQ[3 + j] + cq * TO_YIQ[6 + j];
  }
  return out;
}
