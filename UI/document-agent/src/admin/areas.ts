import type { UsersScope } from "../utils/api/users";

export type AdminArea = "users" | "rbac" | "audit" | "capabilities" | "prompt_tools" | "models" | "ingestion";
type GroupId = "people" | "tools" | "data";

export interface AreaDef {
  id: AdminArea;
  label: string;
  /** Route segment under /admin */
  path: string;
  group: GroupId;
}

export interface AreaGroup {
  id: GroupId;
  label: string;
  areas: AreaDef[];
}

const GROUPS: { id: GroupId; label: string }[] = [
  { id: "people", label: "Personas" },
  { id: "tools", label: "Herramientas e IA" },
  { id: "data", label: "Datos" },
];

/** Sidebar order. Ids are the backend's admin areas (app/rbac/catalog.py ADMIN_AREAS). */
export const AREAS: AreaDef[] = [
  { id: "users", label: "Usuarios", path: "users", group: "people" },
  { id: "rbac", label: "Roles y permisos", path: "rbac", group: "people" },
  { id: "audit", label: "Auditoría", path: "audit", group: "people" },
  { id: "capabilities", label: "Capacidades", path: "capabilities", group: "tools" },
  { id: "prompt_tools", label: "Herramientas de prompt", path: "prompt-tools", group: "tools" },
  { id: "models", label: "Modelos", path: "models", group: "tools" },
  { id: "ingestion", label: "Ingesta", path: "ingestion", group: "data" },
];

/** Areas whose screens exist in this build. Each implementation phase adds its own. */
export const IMPLEMENTED: ReadonlySet<AdminArea> = new Set<AdminArea>(["ingestion", "users", "audit"]);

export const visibleAreas = (
  adminAreas: readonly string[],
  implemented: ReadonlySet<AdminArea> = IMPLEMENTED,
): AreaDef[] => AREAS.filter((area) => implemented.has(area.id) && adminAreas.includes(area.id));

export const visibleGroups = (
  adminAreas: readonly string[],
  implemented: ReadonlySet<AdminArea> = IMPLEMENTED,
): AreaGroup[] => {
  const areas = visibleAreas(adminAreas, implemented);
  return GROUPS.map((group) => ({ ...group, areas: areas.filter((area) => area.group === group.id) })).filter(
    (group) => group.areas.length > 0,
  );
};

export const firstAreaPath = (
  adminAreas: readonly string[],
  implemented: ReadonlySet<AdminArea> = IMPLEMENTED,
): string | null => {
  const first = visibleAreas(adminAreas, implemented)[0];
  return first ? `/admin/${first.path}` : null;
};

/** Header chip: the admin's level and reach. usersScope comes from GET /admin/users (null without that area). */
export const scopeLabel = (roles: readonly string[], usersScope: UsersScope | null): string => {
  if (roles.includes("super_admin")) return "Super admin · Global";
  if (usersScope?.limited) {
    return `Gerente · ${usersScope.departments.length ? usersScope.departments.join(", ") : "sin departamento"}`;
  }
  return "Admin · Global";
};
