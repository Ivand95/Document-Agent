import { describe, expect, it } from "vitest";
import {
  ACTION_LABELS,
  CATEGORIES,
  actionLabel,
  actorLabel,
  categoryPrefix,
  detailsView,
  isCategoryId,
  isHighlighted,
  outcomeLabel,
  outcomeTone,
  resourceLink,
  resourceTypeLabel,
} from "./auditLabels";

describe("actionLabel", () => {
  it("translates known actions and shows unknown ones as they are", () => {
    expect(actionLabel("role.assign")).toBe("Rol asignado");
    expect(actionLabel("access.denied")).toBe("Acceso denegado");
    expect(actionLabel("something.new")).toBe("something.new");
    expect(actionLabel("constructor")).toBe("constructor");
  });
});

describe("CATEGORIES", () => {
  it("puts every labelled action in exactly one category", () => {
    for (const action of Object.keys(ACTION_LABELS)) {
      const matching = CATEGORIES.filter((category) => category.prefixes.some((prefix) => action.startsWith(prefix)));
      expect(matching.map((category) => category.id), action).toHaveLength(1);
    }
  });

  it("joins a category's prefixes for action_prefix", () => {
    expect(categoryPrefix("rbac")).toBe("role.,permission.,mapping.");
    expect(categoryPrefix("access")).toBe("access.");
  });

  it("recognises category ids", () => {
    expect(isCategoryId("models")).toBe(true);
    expect(isCategoryId("nope")).toBe(false);
    expect(isCategoryId(null)).toBe(false);
  });
});

describe("outcome", () => {
  it("labels and colours the outcome", () => {
    expect(outcomeLabel("ok")).toBe("Correcto");
    expect(outcomeLabel("denied")).toBe("Denegado");
    expect(outcomeLabel("weird")).toBe("weird");
    expect(outcomeTone("ok")).toBe("ok");
    expect(outcomeTone("denied")).toBe("error");
    expect(outcomeTone("error")).toBe("error");
    expect(outcomeTone("weird")).toBe("warn");
  });

  it("highlights failures and denied accesses", () => {
    expect(isHighlighted({ action: "role.create", outcome: "ok" })).toBe(false);
    expect(isHighlighted({ action: "provider.check", outcome: "error" })).toBe(true);
    expect(isHighlighted({ action: "access.denied", outcome: "ok" })).toBe(true);
  });
});

describe("actorLabel", () => {
  it("prefers the stored actor, then the id, then 'Sistema'", () => {
    expect(actorLabel({ actor: "ana@x.com", user_id: "u1" })).toBe("ana@x.com");
    expect(actorLabel({ actor: null, user_id: "u1" })).toBe("u1");
    expect(actorLabel({ actor: null, user_id: null })).toBe("Sistema");
  });
});

describe("resources", () => {
  it("labels resource types", () => {
    expect(resourceTypeLabel("user")).toBe("Usuario");
    expect(resourceTypeLabel("admin_area")).toBe("Área de administración");
    expect(resourceTypeLabel("other")).toBe("other");
    expect(resourceTypeLabel(null)).toBe("—");
  });

  it("links only to screens that exist and the admin can open", () => {
    expect(resourceLink("user", "u 1", ["users"])).toBe("/admin/users/u%201");
    expect(resourceLink("user", "u1", ["audit"])).toBeNull();
    expect(resourceLink("role", "r1", ["users"])).toBeNull();
    expect(resourceLink("role", "r1", ["rbac"])).toBe("/admin/rbac/roles/r1");
    expect(resourceLink("capability", "web_search", ["capabilities"])).toBeNull();
    expect(resourceLink(null, "u1", ["users"])).toBeNull();
    expect(resourceLink("user", null, ["users"])).toBeNull();
  });
});

describe("detailsView", () => {
  it("is empty without details", () => {
    expect(detailsView({})).toEqual({ kind: "empty" });
    expect(detailsView(null)).toEqual({ kind: "empty" });
  });

  it("lists flat details as key-value pairs", () => {
    expect(detailsView({ role: "audio_user", enabled: true, count: 3, note: null })).toEqual({
      kind: "pairs",
      pairs: [
        ["role", "audio_user"],
        ["enabled", "sí"],
        ["count", "3"],
        ["note", "—"],
      ],
    });
  });

  it("shows nested details as JSON", () => {
    const details = { changes: { enabled: false } };
    expect(detailsView(details)).toEqual({ kind: "json", text: JSON.stringify(details, null, 2) });
  });
});
