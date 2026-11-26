import { bearer, http } from "../axios";

export type UserStatus = "active" | "pending" | "disabled";
export type UserAction = "approve" | "hold" | "disable";
export type ProtectionReason = "self" | "super_admin" | "admin";

/** The admin's reach in the Usuarios area. limited: only `departments` (a department admin). */
export interface UsersScope {
  limited: boolean;
  departments: string[];
}

export interface UserRow {
  id: string;
  email: string | null;
  name: string | null;
  department: string | null;
  position: string | null;
  status: UserStatus;
  first_login_at: string;
  last_login_at: string;
  updated_at: string | null;
  updated_by: string | null;
  actionable: boolean;
  actions: UserAction[];
  protected: ProtectionReason | null;
}

export interface UsersPage {
  users: UserRow[];
  total: number;
  counts: Record<UserStatus | "all", number>;
  scope: UsersScope;
  limit: number;
  offset: number;
}

export interface UsersQuery {
  status?: UserStatus;
  q?: string;
  department?: string;
  limit?: number;
  offset?: number;
}

/** POST /admin/users/search: the query (free text included) travels in the body, never in the URL. */
export const listUsers = (token: string, query: UsersQuery = {}, signal?: AbortSignal) =>
  http.post<UsersPage>("/admin/users/search", query, { ...bearer(token), signal });

/** GET /admin/users/{id}: the list row plus a few profile fields. */
export interface UserDetail extends UserRow {
  perm_version: number;
  created_by: string;
}

export const getUser = (token: string, id: string, signal?: AbortSignal) =>
  http.get<UserDetail>(`/admin/users/${id}`, { ...bearer(token), signal });

export const setUserStatus = (token: string, id: string, status: UserStatus) =>
  http.post<{ id: string; status: UserStatus }>(`/admin/users/${id}/status`, { status }, bearer(token));

/** One role the user holds (or held by hand and expired), from GET /admin/users/{id}/roles. */
export interface UserRole {
  id: string;
  key: string;
  name: string;
  description: string | null;
  manager_assignable: boolean;
  /** "assigned", "department:<x>", "position_prefix:<x>", "everyone", "legacy:<env var>" */
  sources: string[];
  /** Comes (also) from a mapping: can't be removed here */
  automatic: boolean;
  /** Given by hand (even if expired) */
  assigned: boolean;
  expired: boolean;
  granted_by: string | null;
  granted_at: string | null;
  expires_at: string | null;
  /** The actor may remove it */
  removable: boolean;
}

export interface AssignableRole {
  id: string;
  key: string;
  name: string;
  description: string | null;
}

export interface UserRolesView {
  user_id: string;
  /** true for admins with the rbac area; false: only manager-assignable roles are shown */
  manage_all_roles: boolean;
  roles: UserRole[];
  /** Roles the actor may give this user (empty when the user is protected) */
  assignable: AssignableRole[];
}

export const getUserRoles = (token: string, id: string, signal?: AbortSignal) =>
  http.get<UserRolesView>(`/admin/users/${id}/roles`, { ...bearer(token), signal });

export const assignUserRole = (token: string, id: string, body: { role_id: string; expires_at: string | null }) =>
  http.post<UserRolesView>(`/admin/users/${id}/roles`, body, bearer(token));

export const removeUserRole = (token: string, id: string, roleId: string) =>
  http.delete<UserRolesView>(`/admin/users/${id}/roles/${roleId}`, bearer(token));

export interface AdminDepartment {
  department: string;
  created_at: string;
  created_by: string;
}

/** GET /admin/users/{id}/admin-departments (rbac area): the departments a department admin manages. */
export interface AdminDepartmentsView {
  user_id: string;
  is_department_admin: boolean;
  /** From Microsoft 365: always managed, can't be removed */
  department: string | null;
  extra: AdminDepartment[];
}

export const getAdminDepartments = (token: string, id: string, signal?: AbortSignal) =>
  http.get<AdminDepartmentsView>(`/admin/users/${id}/admin-departments`, { ...bearer(token), signal });

export const addAdminDepartment = (token: string, id: string, department: string) =>
  http.post<AdminDepartmentsView>(`/admin/users/${id}/admin-departments`, { department }, bearer(token));

export const removeAdminDepartment = (token: string, id: string, department: string) =>
  http.delete<AdminDepartmentsView>(
    `/admin/users/${id}/admin-departments/${encodeURIComponent(department)}`,
    bearer(token),
  );
