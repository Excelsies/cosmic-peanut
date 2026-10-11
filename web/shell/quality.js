// Quality mode → the rendering limits the SDK applies (PRD "Adaptive quality").

/** @typedef {import("./validate.js").QualityMode} QualityMode */
/** @typedef {{ maxDpr: number; fpsCap: number; renderScaleMax: number }} QualityProfile */

/**
 * `maxDpr: 0` means native: the SDK reads the live devicePixelRatio, so a window moved to
 * another monitor stays sharp (caps are min'd with it too). `fpsCap: 0` means uncapped (match
 * the display). In `auto` the SDK may lower render scale below `renderScaleMax` when frames run
 * over budget.
 * @param {QualityMode} mode
 * @returns {QualityProfile}
 */
export function qualityProfile(mode) {
  if (mode === "balanced") return { maxDpr: 1.5, fpsCap: 0, renderScaleMax: 1 };
  if (mode === "battery") return { maxDpr: 1, fpsCap: 30, renderScaleMax: 1 };
  return { maxDpr: 0, fpsCap: 0, renderScaleMax: 1 };
}
