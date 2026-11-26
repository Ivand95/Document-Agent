import type { UsersPage, UsersQuery, UsersScope, UserStatus } from "../../utils/api/users";

export type StatusTab = UserStatus | "all";

export const STATUS_TABS: { id: StatusTab; label: string }[] = [
  { id: "pending", label: "Pendientes" },
  { id: "active", label: "Activos" },
  { id: "disabled", label: "Deshabilitados" },
  { id: "all", label: "Todos" },
];

export const USERS_PAGE_SIZE = 50;

/** The list's filters, kept in the URL. status null: not chosen yet (the list picks a default). */
export interface UsersSearch {
  status: StatusTab | null;
  q: string;
  department: string;
  page: number;
}

export const parseUsersSearch = (params: URLSearchParams): UsersSearch => {
  const status = params.get("status");
  const page = Number.parseInt(params.get("page") ?? "", 10);
  return {
    status: STATUS_TABS.some((tab) => tab.id === status) ? (status as StatusTab) : null,
    q: params.get("q") ?? "",
    department: params.get("department") ?? "",
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
};

export const toSearchParams = (search: UsersSearch): URLSearchParams => {
  const params = new URLSearchParams();
  if (search.status) params.set("status", search.status);
  if (search.q) params.set("q", search.q);
  if (search.department) params.set("department", search.department);
  if (search.page > 1) params.set("page", String(search.page));
  return params;
};

export const toUsersQuery = (search: UsersSearch): UsersQuery => {
  const query: UsersQuery = { limit: USERS_PAGE_SIZE, offset: (search.page - 1) * USERS_PAGE_SIZE };
  if (search.status && search.status !== "all") query.status = search.status;
  if (search.q.trim()) query.q = search.q.trim();
  if (search.department) query.department = search.department;
  return query;
};

/** Spec §3.1: open on Pendientes when there are any, otherwise on Todos. */
export const defaultStatusTab = (counts: UsersPage["counts"]): StatusTab => (counts.pending > 0 ? "pending" : "all");

/** A department admin with a single department can't filter: the department is fixed. */
export const fixedDepartment = (scope: UsersScope): string | null =>
  scope.limited && scope.departments.length === 1 ? scope.departments[0] : null;
