// Quality mode → the rendering limits the SDK applies (PRD "Adaptive quality").

/** @typedef {import("./validate.js").QualityMode} QualityMode */
/** @typedef {{ maxDpr: number; fpsCap: number; renderScaleMax: number }} QualityProfile */

// Below this the display, not the quality mode, sets the frame rate: WebKitGTK paces rAF from a
// 60 Hz timer whatever the monitor, and a 30 Hz panel would read as dropped frames to Auto.
const SLOW_DISPLAY_HZ = 55;

/**
 * `maxDpr: 0` means native: the SDK reads the live devicePixelRatio, so a window moved to
 * another monitor stays sharp (caps are min'd with it too). `fpsCap: 0` means uncapped (match
 * the display). In `auto` the SDK may lower render scale below `renderScaleMax` when frames run
 * over budget. A display under 55 Hz caps fps at its refresh rate.
 * @param {QualityMode} mode
 * @param {number} [refreshHz] the window's monitor (0 = unknown)
 * @returns {QualityProfile}
 */
export function qualityProfile(mode, refreshHz = 0) {
  const maxDpr = mode === "balanced" ? 1.5 : mode === "battery" ? 1 : 0;
  let fpsCap = mode === "battery" ? 30 : 0;
  if (refreshHz > 0 && refreshHz < SLOW_DISPLAY_HZ) fpsCap = fpsCap > 0 ? Math.min(fpsCap, refreshHz) : refreshHz;
  return { maxDpr, fpsCap, renderScaleMax: 1 };
}
