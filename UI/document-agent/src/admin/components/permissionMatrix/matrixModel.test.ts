import { describe, expect, it } from "vitest";
import type { Catalog, Permission } from "../../../utils/api/rbac";
import {
  MATRIX_GROUPS,
  applyChange,
  budgetText,
  buildMatrix,
  changeFor,
  constraintBadges,
  constraintsChange,
  parseConstraints,
  restoreRule,
  roleLock,
  settleRule,
  startsCollapsed,
  userPermissionsLock,
  withBudget,
} from "./matrixModel";

const catalog: Catalog = {
  resource_types: ["agent", "admin_area", "doc_category", "capability", "model"],
  actions: ["use", "configure", "admin"],
  mapping_kinds: ["department", "position_prefix", "everyone"],
  resources: {
    agent: [
      { id: "documents", label: "Documentos" },
      { id: "audio", label: "Llamadas" },
    ],
    admin_area: [{ id: "users", label: "Usuarios" }],
    doc_category: [{ id: "MKT", label: "Marketing" }],
    model: [{ id: "openai:gpt-x", label: "GPT X" }],
  },
};

const rule = (over: Partial<Permission>): Permission => ({
  id: "p1",
  subject_type: "role",
  subject_id: "r1",
  resource_type: "agent",
  resource_id: "audio",
  action: "use",
  effect: "allow",
  constraints: {},
  ...over,
});

const subject = { type: "role" as const, id: "r1" };
const agents = MATRIX_GROUPS[0];

describe("buildMatrix", () => {
  it("lists the groups in the spec order with the managed action", () => {
    expect(MATRIX_GROUPS.map((g) => [g.type, g.action])).toEqual([
      ["agent", "use"],
      ["admin_area", "admin"],
      ["doc_category", "use"],
      ["capability", "use"],
      ["model", "use"],
    ]);
  });

  it("gives each known resource the value of its rule, ignoring other actions", () => {
    const rules = [rule({}), rule({ id: "p2", resource_id: "documents", action: "configure" })];
    const groups = buildMatrix(catalog, rules, { superAdmin: false });
    expect(groups[0].rows.map((r) => [r.id, r.value])).toEqual([
      ["documents", "none"],
      ["audio", "allow"],
    ]);
  });

  it("shows the wildcard row to a super admin, and to others only when a rule exists", () => {
    expect(buildMatrix(catalog, [], { superAdmin: true })[0].rows[0]).toMatchObject({ id: "*", wildcard: true });
    expect(buildMatrix(catalog, [], { superAdmin: false })[0].rows.some((r) => r.wildcard)).toBe(false);
    const withRule = buildMatrix(catalog, [rule({ resource_id: "*", effect: "deny" })], { superAdmin: false });
    expect(withRule[0].rows[0]).toMatchObject({ id: "*", value: "deny" });
  });

  it("keeps rules on resources the catalog no longer lists, flagged as unknown", () => {
    const groups = buildMatrix(catalog, [rule({ resource_type: "model", resource_id: "old:gone" })], {
      superAdmin: false,
    });
    const models = groups.find((g) => g.type === "model")!;
    expect(models.rows.map((r) => [r.id, r.unknown])).toEqual([
      ["openai:gpt-x", false],
      ["old:gone", true],
    ]);
  });

  it("filters rows by label or id, without accents or case, and keeps the total", () => {
    const groups = buildMatrix(catalog, [], { superAdmin: false, search: "LLAMADAS" });
    expect(groups[0].rows.map((r) => r.id)).toEqual(["audio"]);
    expect(groups[0].total).toBe(2);
    expect(buildMatrix(catalog, [], { superAdmin: false, search: "usúarios" })[1].rows.map((r) => r.id)).toEqual([
      "users",
    ]);
  });
});

describe("startsCollapsed", () => {
  it("folds models and capabilities with more than 20 resources", () => {
    expect(startsCollapsed({ type: "model", total: 21 })).toBe(true);
    expect(startsCollapsed({ type: "model", total: 20 })).toBe(false);
    expect(startsCollapsed({ type: "agent", total: 50 })).toBe(false);
  });
});

describe("changeFor", () => {
  it("deletes the rule for — and does nothing when the value doesn't change", () => {
    const existing = rule({});
    expect(changeFor(subject, agents, { id: "audio", rule: existing }, "none")).toEqual({
      kind: "delete",
      rule: existing,
    });
    expect(changeFor(subject, agents, { id: "audio", rule: existing }, "allow")).toEqual({ kind: "none" });
    expect(changeFor(subject, agents, { id: "audio", rule: null }, "none")).toEqual({ kind: "none" });
  });

  it("PUTs allow or deny and keeps the rule's constraints", () => {
    const existing = rule({ constraints: { scope: "departments" } });
    expect(changeFor(subject, agents, { id: "audio", rule: existing }, "deny")).toEqual({
      kind: "put",
      body: {
        subject_type: "role",
        subject_id: "r1",
        resource_type: "agent",
        resource_id: "audio",
        action: "use",
        effect: "deny",
        constraints: { scope: "departments" },
      },
    });
  });

  it("changes only the constraints of an existing rule", () => {
    expect(constraintsChange(subject, agents, { id: "audio", rule: null }, { a: 1 })).toEqual({ kind: "none" });
    const change = constraintsChange(subject, agents, { id: "audio", rule: rule({ effect: "deny" }) }, { a: 1 });
    expect(change).toMatchObject({ kind: "put", body: { effect: "deny", constraints: { a: 1 } } });
  });
});

describe("optimistic updates", () => {
  it("adds a pending rule, replaces it with the saved one, and restores the previous rule", () => {
    const put = changeFor(subject, agents, { id: "documents", rule: null }, "allow");
    const optimistic = applyChange([rule({})], put);
    expect(optimistic).toHaveLength(2);
    expect(optimistic[1].id.startsWith("pending:")).toBe(true);

    const saved = rule({ id: "p9", resource_id: "documents" });
    expect(settleRule(optimistic, saved).map((r) => r.id)).toEqual(["p1", "p9"]);
    expect(restoreRule(optimistic, "agent", "documents", "use", null).map((r) => r.id)).toEqual(["p1"]);
  });

  it("updates an existing rule in place and removes a deleted one", () => {
    const existing = rule({});
    const updated = applyChange([existing], changeFor(subject, agents, { id: "audio", rule: existing }, "deny"));
    expect(updated).toEqual([{ ...existing, effect: "deny" }]);
    expect(applyChange([existing], { kind: "delete", rule: existing })).toEqual([]);
    expect(restoreRule([], "agent", "audio", "use", existing)).toEqual([existing]);
  });
});

describe("constraints", () => {
  it("describes known constraints and groups the rest", () => {
    expect(constraintBadges({ scope: "departments", llm_budget_usd_month: 12.5, x: 1, y: 2 })).toEqual([
      "🏢 sus departamentos",
      "💲 12.5 USD/mes",
      "⚙ restricciones",
    ]);
    expect(constraintBadges({})).toEqual([]);
    expect(constraintBadges(null)).toEqual([]);
  });

  it("reads and writes the monthly budget", () => {
    expect(budgetText({ llm_budget_usd_month: 30 })).toBe("30");
    expect(budgetText({})).toBe("");
    expect(withBudget({ a: 1 }, " 12,5 ")).toEqual({ ok: true, constraints: { a: 1, llm_budget_usd_month: 12.5 } });
    expect(withBudget({ a: 1, llm_budget_usd_month: 5 }, "")).toEqual({ ok: true, constraints: { a: 1 } });
    expect(withBudget({}, "-1")).toMatchObject({ ok: false });
    expect(withBudget({}, "abc")).toMatchObject({ ok: false });
  });

  it("parses the advanced JSON as an object", () => {
    expect(parseConstraints("")).toEqual({ ok: true, value: {} });
    expect(parseConstraints('{"scope": "departments"}')).toEqual({ ok: true, value: { scope: "departments" } });
    expect(parseConstraints("[1]")).toMatchObject({ ok: false });
    expect(parseConstraints("{nope")).toMatchObject({ ok: false });
    expect(parseConstraints(JSON.stringify({ a: "x".repeat(4000) }))).toMatchObject({ ok: false });
  });
});

describe("locks", () => {
  it("explains why a user's permissions are read-only", () => {
    expect(userPermissionsLock("self")).toMatch(/tus propios permisos/);
    expect(userPermissionsLock("super_admin")).toMatch(/super administrador/);
    expect(userPermissionsLock("admin")).toBeNull();
    expect(userPermissionsLock(null)).toBeNull();
  });

  it("locks the super_admin role for everyone but a super admin", () => {
    expect(roleLock({ key: "super_admin" }, false)).toMatch(/super administrador/);
    expect(roleLock({ key: "super_admin" }, true)).toBeNull();
    expect(roleLock({ key: "manager" }, false)).toBeNull();
  });
});
