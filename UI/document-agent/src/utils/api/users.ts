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

export const listUsers = (token: string, query: UsersQuery = {}) =>
  http.get<UsersPage>("/admin/users", { ...bearer(token), params: query });
