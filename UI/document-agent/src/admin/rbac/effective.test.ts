import { describe, expect, it } from "vitest";
import type { Catalog, EffectivePermissions, Permission } from "../../utils/api/rbac";
import { REASON_LABELS, describeRule, effectiveGroups } from "./effective";

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

const catalog: Catalog = {
  resource_types: ["agent", "admin_area", "doc_category", "capability", "model"],
  actions: ["use", "configure", "admin"],
  mapping_kinds: ["department", "position_prefix", "everyone"],
  resources: { agent: [{ id: "audio", label: "Llamadas" }, { id: "documents", label: "Documentos" }] },
};

const data: EffectivePermissions = {
  user_id: "u1",
  status: "active",
  perm_version: 3,
  department: "Marketing",
  position: "Analista",
  roles: [{ id: "r1", key: "audio_user", name: "Usuario de llamadas", sources: ["assigned"] }],
  user_rules: [rule({ id: "p2", subject_type: "user", subject_id: "u1", resource_id: "documents", effect: "deny" })],
  role_rules: [rule({ constraints: { llm_budget_usd_month: 5 } })],
  decisions: [
    { resource_type: "agent", resource_id: "audio", action: "use", allowed: true, reason: "role_allow", rule_ids: ["p1"], constraints: { llm_budget_usd_month: 5 } },
    { resource_type: "agent", resource_id: "documents", action: "use", allowed: false, reason: "user_deny", rule_ids: ["p2"], constraints: {} },
    { resource_type: "doc_category", resource_id: "FI", action: "use", allowed: false, reason: "no_rule", rule_ids: [], constraints: {} },
  ],
};

describe("describeRule", () => {
  it("names where the rule comes from and its effect", () => {
    const names = new Map([["r1", "Usuario de llamadas"]]);
    expect(describeRule(rule({}), names)).toBe("Rol «Usuario de llamadas»: Permitir");
    expect(describeRule(rule({ subject_type: "user", effect: "deny", resource_id: "*" }), names)).toBe(
      "Permiso directo: Denegar (todos)",
    );
  });
});

describe("effectiveGroups", () => {
  it("groups decisions in matrix order with labels, reasons, rules and constraints", () => {
    const groups = effectiveGroups(data, catalog, false);
    expect(groups.map((g) => g.label)).toEqual(["Agentes", "Categorías de documentos"]);
    expect(groups[0].items[0]).toEqual({
      id: "audio",
      label: "Llamadas",
      allowed: true,
      reason: REASON_LABELS.role_allow,
      rules: ["Rol «Usuario de llamadas»: Permitir"],
      constraints: ["💲 5 USD/mes"],
    });
    expect(groups[1].items[0]).toMatchObject({ label: "FI", reason: REASON_LABELS.no_rule, rules: [] });
  });

  it("keeps only allowed resources when asked, dropping empty groups", () => {
    const groups = effectiveGroups(data, catalog, true);
    expect(groups.map((g) => [g.label, g.items.map((i) => i.id)])).toEqual([["Agentes", ["audio"]]]);
  });
});
