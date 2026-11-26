import { describe, expect, it } from "vitest";
import type { RoleMapping } from "../../utils/api/rbac";
import { mappingText, mappingValueError, roleKeyError, roleMappingsSummary } from "./rbacLabels";

const mapping = (over: Partial<RoleMapping>): RoleMapping => ({
  id: "m1",
  kind: "department",
  value: "Tecnología",
  role_id: "r1",
  created_at: "2026-01-01T00:00:00Z",
  created_by: "system",
  ...over,
});

describe("roleKeyError", () => {
  it("accepts the backend pattern and explains the rule otherwise", () => {
    expect(roleKeyError("report_user")).toBeNull();
    expect(roleKeyError("a1")).toBeNull();
    expect(roleKeyError("a")).not.toBeNull();
    expect(roleKeyError("Report")).not.toBeNull();
    expect(roleKeyError("1abc")).not.toBeNull();
    expect(roleKeyError("a".repeat(64))).not.toBeNull();
  });
});

describe("mappings", () => {
  it("describes each kind", () => {
    expect(mappingText(mapping({}))).toBe("Departamento Tecnología");
    expect(mappingText(mapping({ kind: "position_prefix", value: "GR -" }))).toBe("Puesto que empieza por «GR -»");
    expect(mappingText(mapping({ kind: "everyone", value: "" }))).toBe("Todos los usuarios");
  });

  it("summarises a role's mappings", () => {
    const all = [
      mapping({}),
      mapping({ id: "m2", kind: "everyone", value: "" }),
      mapping({ id: "m3", role_id: "r2" }),
    ];
    expect(roleMappingsSummary(all, "r1")).toBe("Departamento Tecnología · Todos los usuarios");
    expect(roleMappingsSummary(all, "r9")).toBe("—");
  });

  it("requires a value except for everyone", () => {
    expect(mappingValueError("everyone", "")).toBeNull();
    expect(mappingValueError("department", "  ")).not.toBeNull();
    expect(mappingValueError("position_prefix", "GR -")).toBeNull();
    expect(mappingValueError("department", "x".repeat(201))).not.toBeNull();
  });
});
