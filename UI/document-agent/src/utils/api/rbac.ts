import { bearer, http } from "../axios";

export type ResourceType = "agent" | "admin_area" | "doc_category" | "capability" | "model";
export type PermissionAction = "use" | "configure" | "admin";
export type Effect = "allow" | "deny";
export type SubjectType = "role" | "user";
export type MappingKind = "department" | "position_prefix" | "everyone";

export interface Role {
  id: string;
  key: string;
  name: string;
  description: string | null;
  /** Seeded roles: can't be deleted */
  is_system: boolean;
  /** Department admins may give it to the users of their departments */
  manager_assignable: boolean;
  created_at: string;
  created_by: string;
  updated_at: string | null;
  updated_by: string | null;
}

export interface RoleMapping {
  id: string;
  kind: MappingKind;
  /** "" when kind is everyone */
  value: string;
  role_id: string;
  created_at: string;
  created_by: string;
}

/** A permission cell (rule) of a role or a user. resource_id "*" covers every resource of the type. */
export interface Permission {
  id: string;
  subject_type: SubjectType;
  subject_id: string;
  resource_type: ResourceType;
  resource_id: string;
  action: PermissionAction;
  effect: Effect;
  constraints: Record<string, unknown>;
}

export type PermissionSet = Omit<Permission, "id">;

export interface Subject {
  type: SubjectType;
  id: string;
}

export interface CatalogResource {
  id: string;
  label: string;
}

export interface Catalog {
  resource_types: ResourceType[];
  actions: PermissionAction[];
  mapping_kinds: MappingKind[];
  /** Types without a source of ids are missing */
  resources: Partial<Record<ResourceType, CatalogResource[]>>;
}

export type DecisionReason = "inactive" | "user_deny" | "user_allow" | "role_deny" | "role_allow" | "no_rule";

export interface EffectiveDecision {
  resource_type: ResourceType;
  resource_id: string;
  action: PermissionAction;
  allowed: boolean;
  reason: DecisionReason;
  rule_ids: string[];
  /** Merged constraints of the allows that apply */
  constraints: Record<string, unknown>;
}

export interface EffectiveRole {
  id: string;
  key: string;
  name: string;
  /** Same values as UserRole.sources */
  sources: string[];
}

export interface EffectivePermissions {
  user_id: string;
  status: string;
  perm_version: number;
  department: string | null;
  position: string | null;
  roles: EffectiveRole[];
  user_rules: Permission[];
  role_rules: Permission[];
  decisions: EffectiveDecision[];
}

export const getCatalog = (token: string, signal?: AbortSignal) =>
  http.get<Catalog>("/admin/rbac/catalog", { ...bearer(token), signal });

export const listRoles = (token: string, signal?: AbortSignal) =>
  http.get<{ roles: Role[] }>("/admin/rbac/roles", { ...bearer(token), signal });

export const createRole = (token: string, body: { key: string; name: string; description: string | null }) =>
  http.post<Role>("/admin/rbac/roles", body, bearer(token));

export const updateRole = (
  token: string,
  id: string,
  body: { name?: string; description?: string | null; manager_assignable?: boolean },
) => http.patch<Role>(`/admin/rbac/roles/${id}`, body, bearer(token));

export const deleteRole = (token: string, id: string) => http.delete(`/admin/rbac/roles/${id}`, bearer(token));

export const listMappings = (token: string, signal?: AbortSignal) =>
  http.get<{ mappings: RoleMapping[] }>("/admin/rbac/mappings", { ...bearer(token), signal });

export const createMapping = (token: string, body: { kind: MappingKind; value: string; role_id: string }) =>
  http.post<RoleMapping>("/admin/rbac/mappings", body, bearer(token));

export const deleteMapping = (token: string, id: string) => http.delete(`/admin/rbac/mappings/${id}`, bearer(token));

export const listPermissions = (token: string, subject: Subject, signal?: AbortSignal) =>
  http.get<{ permissions: Permission[] }>("/admin/rbac/permissions", {
    ...bearer(token),
    params: { subject_type: subject.type, subject_id: subject.id },
    signal,
  });

export const setPermission = (token: string, body: PermissionSet) =>
  http.put<Permission>("/admin/rbac/permissions", body, bearer(token));

export const deletePermission = (token: string, id: string) =>
  http.delete(`/admin/rbac/permissions/${id}`, bearer(token));

export const getEffective = (token: string, userId: string, signal?: AbortSignal) =>
  http.get<EffectivePermissions>(`/admin/rbac/users/${userId}/effective`, { ...bearer(token), signal });
