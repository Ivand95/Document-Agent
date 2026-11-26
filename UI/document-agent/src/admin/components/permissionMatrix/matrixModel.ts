import type {
  Catalog,
  Effect,
  Permission,
  PermissionAction,
  PermissionSet,
  ResourceType,
  Subject,
} from "../../../utils/api/rbac";
import type { ProtectionReason } from "../../../utils/api/users";

export const WILDCARD = "*";
export const BUDGET_KEY = "llm_budget_usd_month";
const MAX_CONSTRAINTS_CHARS = 4000; // app/models/rbac.py MAX_CONSTRAINTS_CHARS
const COLLAPSE_OVER = 20;

export interface MatrixGroupDef {
  type: ResourceType;
  label: string;
  /** The one action the matrix manages for this type. `configure` isn't shown: no endpoint checks it. */
  action: PermissionAction;
}

/** Spec §4.3, in this order. */
export const MATRIX_GROUPS: MatrixGroupDef[] = [
  { type: "agent", label: "Agentes", action: "use" },
  { type: "admin_area", label: "Áreas de administración", action: "admin" },
  { type: "doc_category", label: "Categorías de documentos", action: "use" },
  { type: "capability", label: "Capacidades", action: "use" },
  { type: "model", label: "Modelos", action: "use" },
];

export type CellValue = "none" | Effect;

export interface MatrixRow {
  id: string;
  label: string;
  rule: Permission | null;
  value: CellValue;
  /** The "Todos (*)" row */
  wildcard: boolean;
  /** A rule on a resource the catalog no longer lists (e.g. a deleted model): it can still be cleared */
  unknown: boolean;
}

export interface MatrixGroup extends MatrixGroupDef {
  rows: MatrixRow[];
  /** Rows before the search filter */
  total: number;
}

export type MatrixChange =
  | { kind: "none" }
  | { kind: "delete"; rule: Permission }
  | { kind: "put"; body: PermissionSet };

const plain = (text: string) =>
  text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();

export const ruleKey = (type: string, id: string, action: string) => `${type}\u0000${id}\u0000${action}`;

const keyOf = (rule: { resource_type: string; resource_id: string; action: string }) =>
  ruleKey(rule.resource_type, rule.resource_id, rule.action);

const matches = (row: { id: string; label: string }, search: string) => {
  const q = plain(search.trim());
  return !q || plain(row.label).includes(q) || plain(row.id).includes(q);
};

/** The groups of the matrix: the wildcard row first (always for a super admin; for others only when a rule
 * exists, read-only, so they see why), then the catalog's resources, then rules on unknown ids. */
export const buildMatrix = (
  catalog: Catalog,
  rules: readonly Permission[],
  options: { superAdmin: boolean; search?: string },
): MatrixGroup[] => {
  const index = new Map(rules.map((rule) => [keyOf(rule), rule]));
  return MATRIX_GROUPS.map((def) => {
    const row = (id: string, label: string, unknown: boolean): MatrixRow => {
      const rule = index.get(ruleKey(def.type, id, def.action)) ?? null;
      return { id, label, rule, value: rule ? rule.effect : "none", wildcard: id === WILDCARD, unknown };
    };
    const known = catalog.resources[def.type] ?? [];
    const knownIds = new Set(known.map((resource) => resource.id));
    const hasWildcard = index.has(ruleKey(def.type, WILDCARD, def.action));
    const orphans = rules
      .filter(
        (r) =>
          r.resource_type === def.type && r.action === def.action && r.resource_id !== WILDCARD && !knownIds.has(r.resource_id),
      )
      .map((r) => r.resource_id)
      .sort();
    const rows = [
      ...(options.superAdmin || hasWildcard ? [row(WILDCARD, "Todos (*)", false)] : []),
      ...known.map((resource) => row(resource.id, resource.label, false)),
      ...orphans.map((id) => row(id, id, true)),
    ];
    return { ...def, total: rows.length, rows: rows.filter((r) => matches(r, options.search ?? "")) };
  });
};

/** Models and capabilities start folded when they are many. */
export const startsCollapsed = (group: { type: ResourceType; total: number }) =>
  (group.type === "model" || group.type === "capability") && group.total > COLLAPSE_OVER;

/** What setting a cell to `value` asks the API: — deletes the rule, allow/deny creates or replaces it
 * keeping its constraints. */
export const changeFor = (
  subject: Subject,
  group: MatrixGroupDef,
  row: { id: string; rule: Permission | null },
  value: CellValue,
): MatrixChange => {
  const current: CellValue = row.rule?.effect ?? "none";
  if (value === current) return { kind: "none" };
  if (value === "none") return row.rule ? { kind: "delete", rule: row.rule } : { kind: "none" };
  return {
    kind: "put",
    body: {
      subject_type: subject.type,
      subject_id: subject.id,
      resource_type: group.type,
      resource_id: row.id,
      action: group.action,
      effect: value,
      constraints: row.rule?.constraints ?? {},
    },
  };
};

/** New constraints for an existing rule (same effect). Without a rule there is nothing to constrain. */
export const constraintsChange = (
  subject: Subject,
  group: MatrixGroupDef,
  row: { id: string; rule: Permission | null },
  constraints: Record<string, unknown>,
): MatrixChange => {
  if (!row.rule) return { kind: "none" };
  const base = changeFor(subject, group, { id: row.id, rule: null }, row.rule.effect);
  return base.kind === "put" ? { kind: "put", body: { ...base.body, constraints } } : base;
};

/** The rules once the change is applied, before the API answers. A new rule gets a temporary id. */
export const applyChange = (rules: readonly Permission[], change: MatrixChange): Permission[] => {
  if (change.kind === "none") return [...rules];
  if (change.kind === "delete") return rules.filter((rule) => rule.id !== change.rule.id);
  const key = keyOf(change.body);
  const existing = rules.find((rule) => keyOf(rule) === key);
  if (existing) {
    return rules.map((rule) =>
      rule === existing ? { ...existing, effect: change.body.effect, constraints: change.body.constraints } : rule,
    );
  }
  return [...rules, { id: `pending:${key}`, ...change.body }];
};

/** Puts the row the API saved in place of the optimistic one. */
export const settleRule = (rules: readonly Permission[], saved: Permission): Permission[] => {
  const key = keyOf(saved);
  return rules.map((rule) => (keyOf(rule) === key ? saved : rule));
};

/** Undoes a failed change on one cell only (other cells may have changed meanwhile). */
export const restoreRule = (
  rules: readonly Permission[],
  type: ResourceType,
  id: string,
  action: PermissionAction,
  before: Permission | null,
): Permission[] => {
  const key = ruleKey(type, id, action);
  const rest = rules.filter((rule) => keyOf(rule) !== key);
  return before ? [...rest, before] : rest;
};

const amount = (n: number) => String(Math.round(n * 100) / 100);

/** Short labels for a rule's constraints (spec §4.3). */
export const constraintBadges = (constraints: Record<string, unknown> | null | undefined): string[] => {
  const badges: string[] = [];
  let other = false;
  for (const [key, value] of Object.entries(constraints ?? {})) {
    if (key === "scope" && value === "departments") badges.push("🏢 sus departamentos");
    else if (key === BUDGET_KEY && typeof value === "number") badges.push(`💲 ${amount(value)} USD/mes`);
    else other = true;
  }
  if (other) badges.push("⚙ restricciones");
  return badges;
};

export const budgetText = (constraints: Record<string, unknown>) => {
  const value = constraints[BUDGET_KEY];
  return typeof value === "number" ? amount(value) : "";
};

/** The constraints with the monthly budget the admin typed ("," or "."). Empty: no limit. */
export const withBudget = (
  constraints: Record<string, unknown>,
  text: string,
): { ok: true; constraints: Record<string, unknown> } | { ok: false; error: string } => {
  const rest = { ...constraints };
  delete rest[BUDGET_KEY];
  const trimmed = text.trim().replace(",", ".");
  if (!trimmed) return { ok: true, constraints: rest };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) {
    return { ok: false, error: "El presupuesto debe ser un número mayor o igual que 0." };
  }
  return { ok: true, constraints: { ...rest, [BUDGET_KEY]: Math.round(value * 100) / 100 } };
};

/** The advanced constraints box: a JSON object of at most 4000 characters. Empty means {}. */
export const parseConstraints = (
  text: string,
): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } => {
  if (!text.trim()) return { ok: true, value: {} };
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { ok: false, error: "No es un JSON válido." };
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, error: 'Debe ser un objeto JSON, por ejemplo {"scope": "departments"}.' };
  }
  if (JSON.stringify(value).length > MAX_CONSTRAINTS_CHARS) {
    return { ok: false, error: `Las restricciones no pueden superar ${MAX_CONSTRAINTS_CHARS} caracteres.` };
  }
  return { ok: true, value: value as Record<string, unknown> };
};

export const isSuperAdmin = (roles: readonly string[]) => roles.includes("super_admin");

/** Why a user's direct permissions are read-only for the admin, from the user's `protected`. */
export const userPermissionsLock = (protectedReason: ProtectionReason | null): string | null => {
  if (protectedReason === "self") return "Es tu cuenta: no puedes cambiar tus propios permisos.";
  if (protectedReason === "super_admin") {
    return "Es un super administrador: solo otro super administrador puede cambiar sus permisos.";
  }
  return null;
};

/** Why a role can't be edited by the admin. */
export const roleLock = (role: { key: string }, actorIsSuperAdmin: boolean): string | null =>
  role.key === "super_admin" && !actorIsSuperAdmin
    ? "Solo un super administrador puede cambiar el rol super_admin."
    : null;
