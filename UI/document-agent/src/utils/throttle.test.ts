import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTrailingThrottle } from "./throttle";

describe("createTrailingThrottle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs the first call immediately", () => {
    const run = vi.fn();
    createTrailingThrottle(1000, run).call();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("runs a call made inside the window once, at the end of the window", () => {
    const run = vi.fn();
    const throttle = createTrailingThrottle(1000, run);
    throttle.call();
    vi.advanceTimersByTime(400);
    throttle.call();
    expect(run).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(599);
    expect(run).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("merges several calls inside the window into one trailing run", () => {
    const run = vi.fn();
    const throttle = createTrailingThrottle(1000, run);
    throttle.call();
    throttle.call();
    throttle.call();
    throttle.call();
    vi.advanceTimersByTime(5000);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("markRan starts a window without running", () => {
    const run = vi.fn();
    const throttle = createTrailingThrottle(1000, run);
    throttle.markRan();
    expect(run).not.toHaveBeenCalled();
    throttle.call();
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("cancel prevents the pending run", () => {
    const run = vi.fn();
    const throttle = createTrailingThrottle(1000, run);
    throttle.call();
    throttle.call();
    throttle.cancel();
    vi.advanceTimersByTime(5000);
    expect(run).toHaveBeenCalledTimes(1);
  });
});
