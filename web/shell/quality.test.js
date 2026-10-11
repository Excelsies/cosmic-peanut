import { describe, expect, it } from "vitest";
import { qualityProfile } from "./quality.js";

describe("qualityProfile", () => {
  it("maps each mode to DPR and fps caps (maxDpr 0 = native, fpsCap 0 = uncapped)", () => {
    expect(qualityProfile("high")).toEqual({ maxDpr: 0, fpsCap: 0, renderScaleMax: 1 });
    expect(qualityProfile("auto")).toEqual({ maxDpr: 0, fpsCap: 0, renderScaleMax: 1 });
    expect(qualityProfile("balanced")).toEqual({ maxDpr: 1.5, fpsCap: 0, renderScaleMax: 1 });
    expect(qualityProfile("battery")).toEqual({ maxDpr: 1, fpsCap: 30, renderScaleMax: 1 });
  });
});
