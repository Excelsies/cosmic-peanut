import { describe, expect, it } from "vitest";
import { qualityProfile } from "./quality.js";

describe("qualityProfile", () => {
  it("maps each mode to DPR and fps caps (maxDpr 0 = native, fpsCap 0 = uncapped)", () => {
    expect(qualityProfile("high")).toEqual({ maxDpr: 0, fpsCap: 0, renderScaleMax: 1 });
    expect(qualityProfile("auto")).toEqual({ maxDpr: 0, fpsCap: 0, renderScaleMax: 1 });
    expect(qualityProfile("balanced")).toEqual({ maxDpr: 1.5, fpsCap: 0, renderScaleMax: 1 });
    expect(qualityProfile("battery")).toEqual({ maxDpr: 1, fpsCap: 30, renderScaleMax: 1 });
  });

  it("caps fps at a display's refresh rate under 55 Hz (0 = unknown)", () => {
    expect(qualityProfile("high", 30).fpsCap).toBe(30);
    expect(qualityProfile("auto", 29.97).fpsCap).toBe(29.97);
    expect(qualityProfile("battery", 24).fpsCap).toBe(24);
    expect(qualityProfile("battery", 50).fpsCap).toBe(30);
    expect(qualityProfile("auto", 59.95).fpsCap).toBe(0);
    expect(qualityProfile("auto", 144).fpsCap).toBe(0);
    expect(qualityProfile("auto", 0).fpsCap).toBe(0);
  });
});
