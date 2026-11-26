import { describe, expect, it } from "vitest";
import { formatRelative, isoToLocalInput, localInputToIso } from "./format";

const NOW = new Date("2026-11-26T12:00:00Z").getTime();
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("formatRelative", () => {
  it.each([
    [30_000, "hace un momento"],
    [5 * MIN, "hace 5 min"],
    [3 * HOUR, "hace 3 h"],
    [30 * HOUR, "ayer"],
    [3 * DAY, "hace 3 días"],
  ])("%i ms ago → %s", (ms, text) => {
    expect(formatRelative(ago(ms), NOW)).toBe(text);
  });

  it("shows the date for values older than a week", () => {
    const iso = ago(10 * DAY);
    expect(formatRelative(iso, NOW)).toBe(new Date(iso).toLocaleDateString("es-DO", { dateStyle: "short" }));
  });

  it("treats future values as now", () => {
    expect(formatRelative(ago(-MIN), NOW)).toBe("hace un momento");
  });

  it("returns a dash for empty or invalid values", () => {
    expect(formatRelative(null, NOW)).toBe("—");
    expect(formatRelative("", NOW)).toBe("—");
    expect(formatRelative("nope", NOW)).toBe("—");
  });
});

describe("localInputToIso", () => {
  it("converts a datetime-local value (local time) to an ISO instant", () => {
    const iso = localInputToIso("2026-12-01T10:30");
    expect(iso).not.toBeNull();
    expect(new Date(iso ?? "").getTime()).toBe(new Date(2026, 11, 1, 10, 30).getTime());
  });

  it("returns null for empty or invalid input", () => {
    expect(localInputToIso("")).toBeNull();
    expect(localInputToIso("not a date")).toBeNull();
  });
});

describe("isoToLocalInput", () => {
  it("formats an ISO instant as a datetime-local value in local time", () => {
    const iso = new Date(2026, 0, 5, 9, 7).toISOString();
    expect(isoToLocalInput(iso)).toBe("2026-01-05T09:07");
  });

  it("drops seconds", () => {
    const iso = new Date(2026, 11, 1, 23, 59, 45).toISOString();
    expect(isoToLocalInput(iso)).toBe("2026-12-01T23:59");
  });

  it("returns an empty string for null or invalid values", () => {
    expect(isoToLocalInput(null)).toBe("");
    expect(isoToLocalInput("")).toBe("");
    expect(isoToLocalInput("nope")).toBe("");
  });

  it("round-trips with localInputToIso", () => {
    const iso = new Date(2026, 6, 15, 18, 45).toISOString();
    expect(localInputToIso(isoToLocalInput(iso))).toBe(iso);
    expect(isoToLocalInput(localInputToIso("2026-03-09T08:00"))).toBe("2026-03-09T08:00");
  });
});
