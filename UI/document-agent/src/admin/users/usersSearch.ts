import type { UsersPage, UsersQuery, UsersScope, UserStatus } from "../../utils/api/users";

export type StatusTab = UserStatus | "all";

export const STATUS_TABS: { id: StatusTab; label: string }[] = [
  { id: "pending", label: "Pendientes" },
  { id: "active", label: "Activos" },
  { id: "disabled", label: "Deshabilitados" },
  { id: "all", label: "Todos" },
];

export const USERS_PAGE_SIZE = 50;

/** The list's filters. status, department and page are kept in the URL; q (names, emails) only in the
 * browser's history state, since URLs end up in access logs. status null: not chosen yet (the list picks a
 * default). */
export interface UsersSearch {
  status: StatusTab | null;
  q: string;
  department: string;
  page: number;
}

/** The list's history state (React Router location.state): survives reload and back/forward, never sent. */
export interface UsersListState {
  q?: string;
}

/** The search text kept in a history state; "" for none or anything unexpected. */
export const usersStateQ = (state: unknown): string => {
  const q = (state as UsersListState | null)?.q;
  return typeof q === "string" ? q : "";
};

/** params: the URL's (an old ?q= is ignored); state: location.state, where q lives. */
export const parseUsersSearch = (params: URLSearchParams, state?: unknown): UsersSearch => {
  const status = params.get("status");
  const page = Number.parseInt(params.get("page") ?? "", 10);
  return {
    status: STATUS_TABS.some((tab) => tab.id === status) ? (status as StatusTab) : null,
    q: usersStateQ(state),
    department: params.get("department") ?? "",
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
};

/** The URL part of the search: never q (see toUsersListState). */
export const toSearchParams = (search: UsersSearch): URLSearchParams => {
  const params = new URLSearchParams();
  if (search.status) params.set("status", search.status);
  if (search.department) params.set("department", search.department);
  if (search.page > 1) params.set("page", String(search.page));
  return params;
};

/** The history-state part of the search. */
export const toUsersListState = (search: UsersSearch): UsersListState | null => (search.q ? { q: search.q } : null);

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
