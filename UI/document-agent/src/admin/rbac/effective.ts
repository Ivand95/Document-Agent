import type { Catalog, DecisionReason, EffectivePermissions, Permission, ResourceType } from "../../utils/api/rbac";
import { MATRIX_GROUPS, WILDCARD, constraintBadges } from "../components/permissionMatrix/matrixModel";

export const REASON_LABELS: Record<DecisionReason, string> = {
  inactive: "Usuario no activo",
  user_allow: "Permitido directamente al usuario",
  user_deny: "Denegado directamente al usuario",
  role_allow: "Permitido por un rol",
  role_deny: "Denegado por un rol",
  no_rule: "Sin regla: denegado por defecto",
};

export interface EffectiveItem {
  id: string;
  label: string;
  allowed: boolean;
  reason: string;
  rules: string[];
  constraints: string[];
}

export interface EffectiveGroup {
  type: ResourceType;
  label: string;
  items: EffectiveItem[];
}

/** "Rol «Gerente»: Permitir" or "Permiso directo: Denegar (todos)". roleNames: role id → name. */
export const describeRule = (rule: Permission, roleNames: ReadonlyMap<string, string>): string => {
  const who = rule.subject_type === "user" ? "Permiso directo" : `Rol «${roleNames.get(rule.subject_id) ?? "?"}»`;
  const effect = rule.effect === "allow" ? "Permitir" : "Denegar";
  return `${who}: ${effect}${rule.resource_id === WILDCARD ? " (todos)" : ""}`;
};

/** /effective as groups of readable items, in the matrix's order. catalog null: ids as labels. */
export const effectiveGroups = (
  data: EffectivePermissions,
  catalog: Catalog | null,
  onlyAllowed: boolean,
): EffectiveGroup[] => {
  const rules = new Map([...data.user_rules, ...data.role_rules].map((rule) => [rule.id, rule]));
  const roleNames = new Map(data.roles.map((role) => [role.id, role.name]));
  return MATRIX_GROUPS.map((group) => {
    const labels = new Map((catalog?.resources[group.type] ?? []).map((r) => [r.id, r.label]));
    const items = data.decisions
      .filter((d) => d.resource_type === group.type && (!onlyAllowed || d.allowed))
      .map((d) => ({
        id: d.resource_id,
        label: labels.get(d.resource_id) ?? d.resource_id,
        allowed: d.allowed,
        reason: REASON_LABELS[d.reason] ?? d.reason,
        rules: d.rule_ids.flatMap((id) => {
          const rule = rules.get(id);
          return rule ? [describeRule(rule, roleNames)] : [];
        }),
        constraints: constraintBadges(d.constraints),
      }));
    return { type: group.type, label: group.label, items };
  }).filter((group) => group.items.length > 0);
};
