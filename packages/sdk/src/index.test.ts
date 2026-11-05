import { describe, expect, it } from "vitest";
import { SDK_VERSION } from "./index";

describe("sdk scaffold", () => {
  it("exposes a semantic version string", () => {
    expect(SDK_VERSION).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
