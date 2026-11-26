import { describe, expect, it } from "vitest";
import { withoutParams } from "./legacyParams";

describe("withoutParams", () => {
  it("returns null when there is nothing to drop", () => {
    expect(withoutParams(new URLSearchParams("status=active&page=2"), ["q"])).toBeNull();
    expect(withoutParams(new URLSearchParams(""), ["q"])).toBeNull();
  });

  it("drops the legacy users search text and keeps the other params", () => {
    const cleaned = withoutParams(new URLSearchParams("status=active&q=ana&department=IT"), ["q"]);
    expect(cleaned?.toString()).toBe("status=active&department=IT");
  });

  it("drops the legacy audit user label, even when empty or repeated", () => {
    const cleaned = withoutParams(new URLSearchParams("user_id=u1&user_label=Ana&user_label=&page=3"), ["user_label"]);
    expect(cleaned?.toString()).toBe("user_id=u1&page=3");
    expect(withoutParams(new URLSearchParams("user_label="), ["user_label"])?.toString()).toBe("");
  });

  it("does not modify the given params", () => {
    const params = new URLSearchParams("q=ana");
    withoutParams(params, ["q"]);
    expect(params.get("q")).toBe("ana");
  });
});
