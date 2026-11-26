import type { AuditEntry } from "../../utils/api/audit";
import type { AdminArea } from "../areas";
import type { BadgeTone } from "../components/StatusBadge";

const labelOf = (labels: Record<string, string>, key: string) => (Object.hasOwn(labels, key) ? labels[key] : key);

/** Spec §5.3. Every action the backend records today (grep `audit.record(` in app/). */
export const ACTION_LABELS: Record<string, string> = {
  "user.status": "Cambio de estado de usuario",
  "user.admin_department_add": "Departamento administrado añadido",
  "user.admin_department_remove": "Departamento administrado quitado",
  "role.create": "Rol creado",
  "role.update": "Rol modificado",
  "role.delete": "Rol eliminado",
  "role.assign": "Rol asignado",
  "role.revoke": "Rol quitado",
  "permission.set": "Permiso establecido",
  "permission.delete": "Permiso eliminado",
  "mapping.create": "Asignación automática creada",
  "mapping.delete": "Asignación automática eliminada",
  "access.denied": "Acceso denegado",
  "ingestion.folders_saved": "Carpetas de ingesta guardadas",
  "ingestion.run_requested": "Ingesta lanzada",
  "capability.create": "Capacidad registrada",
  "capability.update": "Capacidad modificada",
  "capability.delete": "Capacidad eliminada",
  "capability.sync_native": "Capacidades nativas sincronizadas",
  "capability.call": "Llamada a capacidad",
  "operation.create": "Operación creada",
  "operation.update": "Operación modificada",
  "operation.delete": "Operación eliminada",
  "connection.create": "Conexión creada",
  "connection.update": "Conexión modificada",
  "connection.delete": "Conexión eliminada",
  "connection.secret_set": "Secreto de conexión guardado",
  "connection.secret_clear": "Secreto de conexión borrado",
  "connection.check": "Conexión comprobada",
  "model.create": "Modelo añadido",
  "model.update": "Modelo modificado",
  "model.delete": "Modelo eliminado",
  "provider.update": "Proveedor modificado",
  "provider.key_set": "Clave de proveedor guardada",
  "provider.key_clear": "Clave de proveedor borrada",
  "provider.check": "Proveedor comprobado",
  "provider.discover": "Modelos del proveedor descubiertos",
  "prompt_tool.create": "Herramienta de prompt creada",
  "prompt_tool.update": "Herramienta de prompt modificada",
  "prompt_tool.test": "Herramienta de prompt probada",
  "prompt_tool.delete": "Herramienta de prompt eliminada",
};

/** Unknown actions show their key as is. */
export const actionLabel = (action: string) => labelOf(ACTION_LABELS, action);

export type CategoryId = "users" | "rbac" | "access" | "ingestion" | "capabilities" | "models" | "prompt_tools";

/** Spec §5.2: the action filter, sent as action_prefix. */
export const CATEGORIES: { id: CategoryId; label: string; prefixes: string[] }[] = [
  { id: "users", label: "Usuarios", prefixes: ["user."] },
  { id: "rbac", label: "Roles y permisos", prefixes: ["role.", "permission.", "mapping."] },
  { id: "access", label: "Accesos denegados", prefixes: ["access."] },
  { id: "ingestion", label: "Ingesta", prefixes: ["ingestion."] },
  { id: "capabilities", label: "Capacidades", prefixes: ["capability.", "operation.", "connection."] },
  { id: "models", label: "Modelos", prefixes: ["model.", "provider."] },
  { id: "prompt_tools", label: "Herramientas de prompt", prefixes: ["prompt_tool."] },
];

export const isCategoryId = (value: string | null): value is CategoryId =>
  CATEGORIES.some((category) => category.id === value);

export const categoryPrefix = (id: CategoryId) =>
  (CATEGORIES.find((category) => category.id === id)?.prefixes ?? []).join(",");

const OUTCOME_LABELS: Record<string, string> = { ok: "Correcto", denied: "Denegado", error: "Error" };

export const outcomeLabel = (outcome: string) => labelOf(OUTCOME_LABELS, outcome);

export const outcomeTone = (outcome: string): BadgeTone =>
  outcome === "ok" ? "ok" : outcome === "denied" || outcome === "error" ? "error" : "warn";

/** Spec §5.1: rows that went wrong, and denied accesses, stand out. */
export const isHighlighted = (entry: Pick<AuditEntry, "action" | "outcome">) =>
  entry.outcome !== "ok" || entry.action === "access.denied";

export const actorLabel = (entry: Pick<AuditEntry, "actor" | "user_id">) => entry.actor ?? entry.user_id ?? "Sistema";

const RESOURCE_TYPE_LABELS: Record<string, string> = {
  user: "Usuario",
  role: "Rol",
  capability: "Capacidad",
  connection: "Conexión",
  model: "Modelo",
  provider: "Proveedor",
  admin_area: "Área de administración",
};

export const resourceTypeLabel = (type: string | null) => (type ? labelOf(RESOURCE_TYPE_LABELS, type) : "—");

/** Screens a resource can be opened in. Capabilities and models add theirs in phases 4 and 5. */
const RESOURCE_ROUTES: Record<string, { area: AdminArea; path: (id: string) => string }> = {
  user: { area: "users", path: (id) => `/admin/users/${encodeURIComponent(id)}` },
  role: { area: "rbac", path: (id) => `/admin/rbac/roles/${encodeURIComponent(id)}` },
};

/** Link to the resource's screen, or null when there is none or the admin can't open it.
 * openAreas: the admin's visible areas (visibleAreas(), so only implemented ones). */
export const resourceLink = (
  type: string | null,
  id: string | null,
  openAreas: readonly AdminArea[],
): string | null => {
  if (!type || !id || !Object.hasOwn(RESOURCE_ROUTES, type)) return null;
  const route = RESOURCE_ROUTES[type];
  return openAreas.includes(route.area) ? route.path(id) : null;
};

export type DetailsView =
  | { kind: "empty" }
  | { kind: "pairs"; pairs: [string, string][] }
  | { kind: "json"; text: string };

const isScalar = (value: unknown) =>
  value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean";

const scalarText = (value: unknown) =>
  value === null ? "—" : value === true ? "sí" : value === false ? "no" : String(value);

/** Spec §5.1: flat details as a key-value list, nested ones as JSON. */
export const detailsView = (details: Record<string, unknown> | null | undefined): DetailsView => {
  const entries = Object.entries(details ?? {});
  if (entries.length === 0) return { kind: "empty" };
  if (!entries.every(([, value]) => isScalar(value))) {
    return { kind: "json", text: JSON.stringify(details, null, 2) };
  }
  return { kind: "pairs", pairs: entries.map(([key, value]) => [key, scalarText(value)]) };
};
