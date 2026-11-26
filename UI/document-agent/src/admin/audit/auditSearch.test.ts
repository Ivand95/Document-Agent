import { describe, expect, it } from "vitest";
import {
  AUDIT_PAGE_SIZE,
  EMPTY_AUDIT_SEARCH,
  customRangeInvalid,
  isValidDay,
  localDayStartIso,
  parseAuditSearch,
  toAuditHistoryState,
  toAuditQuery,
  toAuditSearchParams,
  type AuditSearch,
} from "./auditSearch";

const DAY = 24 * 60 * 60 * 1000;
const NOW = new Date(2026, 10, 26, 15, 30).getTime();

describe("parseAuditSearch", () => {
  it("has all dates and no filters by default", () => {
    expect(parseAuditSearch(new URLSearchParams())).toEqual(EMPTY_AUDIT_SEARCH);
  });

  it("drops invalid values", () => {
    const search = parseAuditSearch(
      new URLSearchParams("range=year&since=2026-13-45&until=ayer&category=nope&page=0&user_label=Ana"),
    );
    expect(search).toEqual(EMPTY_AUDIT_SEARCH);
  });

  it("reads the user's name from the history state, only with a user filter", () => {
    expect(parseAuditSearch(new URLSearchParams("user_id=u1"), { userLabel: "Ana" }).userLabel).toBe("Ana");
    expect(parseAuditSearch(new URLSearchParams(), { userLabel: "Ana" }).userLabel).toBe("");
    expect(parseAuditSearch(new URLSearchParams("user_id=u1"), { userLabel: 3 }).userLabel).toBe("");
    expect(parseAuditSearch(new URLSearchParams("user_id=u1&user_label=Ana")).userLabel).toBe("");
  });

  it("round-trips through the URL and the history state", () => {
    const search: AuditSearch = {
      range: "custom",
      since: "2026-11-01",
      until: "2026-11-20",
      category: "rbac",
      userId: "u1",
      userLabel: "Ana Pérez",
      resourceType: "user",
      resourceId: "u2",
      page: 3,
    };
    expect(parseAuditSearch(toAuditSearchParams(search), toAuditHistoryState(search))).toEqual(search);
  });
});

describe("toAuditSearchParams", () => {
  it("writes nothing for the defaults", () => {
    expect(toAuditSearchParams(EMPTY_AUDIT_SEARCH).toString()).toBe("");
  });

  it("keeps the custom days only with the custom range", () => {
    const params = toAuditSearchParams({ ...EMPTY_AUDIT_SEARCH, range: "7d", since: "2026-11-01" });
    expect(params.toString()).toBe("range=7d");
  });

  it("never writes the user's name", () => {
    const params = toAuditSearchParams({ ...EMPTY_AUDIT_SEARCH, userId: "u1", userLabel: "Ana Pérez" });
    expect(params.toString()).toBe("user_id=u1");
  });
});

describe("toAuditHistoryState", () => {
  it("keeps the user's name only while that user is filtered", () => {
    expect(toAuditHistoryState({ ...EMPTY_AUDIT_SEARCH, userId: "u1", userLabel: "Ana" })).toEqual({ userLabel: "Ana" });
    expect(toAuditHistoryState({ ...EMPTY_AUDIT_SEARCH, userLabel: "Ana" })).toBeNull();
    expect(toAuditHistoryState({ ...EMPTY_AUDIT_SEARCH, userId: "u1" })).toBeNull();
  });
});

describe("toAuditQuery", () => {
  it("asks for the first page with no filters", () => {
    expect(toAuditQuery(EMPTY_AUDIT_SEARCH, NOW)).toEqual({ limit: AUDIT_PAGE_SIZE, offset: 0 });
  });

  it("resolves the quick ranges against now", () => {
    expect(toAuditQuery({ ...EMPTY_AUDIT_SEARCH, range: "today" }, NOW).since).toBe(
      new Date(2026, 10, 26).toISOString(),
    );
    expect(toAuditQuery({ ...EMPTY_AUDIT_SEARCH, range: "7d" }, NOW).since).toBe(new Date(NOW - 7 * DAY).toISOString());
    expect(toAuditQuery({ ...EMPTY_AUDIT_SEARCH, range: "30d" }, NOW).since).toBe(
      new Date(NOW - 30 * DAY).toISOString(),
    );
  });

  it("makes the custom range's last day inclusive", () => {
    const query = toAuditQuery({ ...EMPTY_AUDIT_SEARCH, range: "custom", since: "2026-11-01", until: "2026-11-20" }, NOW);
    expect(query.since).toBe(new Date(2026, 10, 1).toISOString());
    expect(query.until).toBe(new Date(2026, 10, 21).toISOString());
    const open = toAuditQuery({ ...EMPTY_AUDIT_SEARCH, range: "custom", until: "2026-11-20" }, NOW);
    expect(open.since).toBeUndefined();
  });

  it("sends the category, user, resource and page", () => {
    const query = toAuditQuery(
      { ...EMPTY_AUDIT_SEARCH, category: "models", userId: "u1", resourceType: "user", resourceId: "u2", page: 2 },
      NOW,
    );
    expect(query).toEqual({
      limit: AUDIT_PAGE_SIZE,
      offset: AUDIT_PAGE_SIZE,
      action_prefix: "model.,provider.",
      user_id: "u1",
      resource_type: "user",
      resource_id: "u2",
    });
  });
});

describe("dates", () => {
  it("turns a local day into its midnight", () => {
    expect(localDayStartIso("2026-02-28")).toBe(new Date(2026, 1, 28).toISOString());
    expect(localDayStartIso("2026-02-28", 1)).toBe(new Date(2026, 2, 1).toISOString());
  });

  it("accepts only real days from 1970 on", () => {
    expect(isValidDay("2026-02-28")).toBe(true);
    expect(isValidDay("2026-02-30")).toBe(false);
    expect(isValidDay("0002-11-26")).toBe(false);
    expect(isValidDay("1969-12-31")).toBe(false);
    expect(isValidDay("")).toBe(false);
    expect(isValidDay(null)).toBe(false);
  });

  it("flags a custom range that ends before it starts", () => {
    const custom = { ...EMPTY_AUDIT_SEARCH, range: "custom" as const };
    expect(customRangeInvalid({ ...custom, since: "2026-11-20", until: "2026-11-01" })).toBe(true);
    expect(customRangeInvalid({ ...custom, since: "2026-11-01", until: "2026-11-01" })).toBe(false);
    expect(customRangeInvalid({ ...custom, since: "2026-11-20" })).toBe(false);
    expect(customRangeInvalid({ ...EMPTY_AUDIT_SEARCH, since: "2026-11-20", until: "2026-11-01" })).toBe(false);
  });
});
