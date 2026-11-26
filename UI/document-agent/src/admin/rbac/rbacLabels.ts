import type { MappingKind, RoleMapping } from "../../utils/api/rbac";

/** app/models/rbac.py ROLE_KEY */
const ROLE_KEY = /^[a-z][a-z0-9_]{1,62}$/;

export const roleKeyError = (key: string): string | null =>
  ROLE_KEY.test(key)
    ? null
    : "La clave empieza por una letra minúscula y tiene de 2 a 63 caracteres: minúsculas, números o _.";

export const MAPPING_KIND_LABELS: Record<MappingKind, string> = {
  department: "Departamento",
  position_prefix: "Puesto que empieza por",
  everyone: "Todos",
};

export const mappingText = (mapping: Pick<RoleMapping, "kind" | "value">): string => {
  if (mapping.kind === "everyone") return "Todos los usuarios";
  if (mapping.kind === "position_prefix") return `${MAPPING_KIND_LABELS.position_prefix} «${mapping.value}»`;
  return `${MAPPING_KIND_LABELS.department} ${mapping.value}`;
};

/** Roles list column "Se asigna automáticamente a". */
export const roleMappingsSummary = (mappings: readonly RoleMapping[], roleId: string): string =>
  mappings
    .filter((mapping) => mapping.role_id === roleId)
    .map(mappingText)
    .join(" · ") || "—";

export const mappingValueError = (kind: MappingKind, value: string): string | null => {
  if (kind === "everyone") return null;
  if (!value.trim()) return "Escribe un valor.";
  if (value.length > 200) return "Máximo 200 caracteres.";
  return null;
};
