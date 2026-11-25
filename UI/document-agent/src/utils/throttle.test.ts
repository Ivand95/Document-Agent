import { describe, expect, it } from "vitest";
import { createThrottle } from "./throttle";

describe("createThrottle", () => {
  it("allows the first call", () => {
    expect(createThrottle(1000)(0)).toBe(true);
  });

  it("refuses calls inside the window and allows the first one after it", () => {
    const allow = createThrottle(1000);
    expect(allow(5000)).toBe(true);
    expect(allow(5999)).toBe(false);
    expect(allow(6000)).toBe(true);
  });

  it("does not extend the window with refused calls", () => {
    const allow = createThrottle(1000);
    allow(0);
    allow(500); // refused: must not move the window
    expect(allow(1000)).toBe(true);
  });
});
