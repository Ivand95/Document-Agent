import { describe, expect, it } from "vitest";
import { firstAreaPath, scopeLabel, visibleAreas, visibleGroups, type AdminArea } from "./areas";

const ALL: ReadonlySet<AdminArea> = new Set<AdminArea>([
  "users", "rbac", "audit", "capabilities", "prompt_tools", "models", "ingestion",
]);

describe("visibleAreas", () => {
  it("keeps only areas that are both granted and implemented, in sidebar order", () => {
    const implemented = new Set<AdminArea>(["ingestion", "users"]);
    expect(visibleAreas(["ingestion", "rbac", "users"], implemented).map((a) => a.id)).toEqual([
      "users",
      "ingestion",
    ]);
  });

  it("ignores admin areas the UI does not know (e.g. code_tools)", () => {
    expect(visibleAreas(["code_tools"], ALL)).toEqual([]);
  });
});

describe("visibleGroups", () => {
  it("drops groups with no visible area", () => {
    const groups = visibleGroups(["users"], ALL);
    expect(groups.map((g) => g.label)).toEqual(["Personas"]);
    expect(groups[0].areas.map((a) => a.id)).toEqual(["users"]);
  });

  it("orders groups Personas, Herramientas e IA, Datos", () => {
    const groups = visibleGroups(["ingestion", "models", "audit"], ALL);
    expect(groups.map((g) => g.label)).toEqual(["Personas", "Herramientas e IA", "Datos"]);
  });
});

describe("firstAreaPath", () => {
  it("returns the path of the first visible area", () => {
    expect(firstAreaPath(["ingestion", "prompt_tools"], ALL)).toBe("/admin/prompt-tools");
  });

  it("returns null when nothing is visible", () => {
    expect(firstAreaPath(["users"], new Set<AdminArea>(["ingestion"]))).toBeNull();
  });
});

describe("scopeLabel", () => {
  it("names super admins first", () => {
    expect(scopeLabel(["super_admin", "admin"], { limited: false, departments: [] })).toBe("Super admin · Global");
  });

  it("lists a department admin's departments", () => {
    expect(scopeLabel(["department_admin"], { limited: true, departments: ["Marketing", "Comercial"] })).toBe(
      "Gerente · Marketing, Comercial",
    );
  });

  it("says when a department admin has no department", () => {
    expect(scopeLabel(["department_admin"], { limited: true, departments: [] })).toBe("Gerente · sin departamento");
  });

  it("is global for everyone else, including admins without the users area", () => {
    expect(scopeLabel(["admin"], { limited: false, departments: [] })).toBe("Admin · Global");
    expect(scopeLabel(["tool_admin"], null)).toBe("Admin · Global");
  });
});
