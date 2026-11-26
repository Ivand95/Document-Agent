import type { AuditQuery } from "../../utils/api/audit";
import { categoryPrefix, isCategoryId, type CategoryId } from "./auditLabels";

export const AUDIT_PAGE_SIZE = 100;

export type RangeId = "all" | "today" | "7d" | "30d" | "custom";

export const RANGES: { id: RangeId; label: string }[] = [
  { id: "all", label: "Todas las fechas" },
  { id: "today", label: "Hoy" },
  { id: "7d", label: "Últimos 7 días" },
  { id: "30d", label: "Últimos 30 días" },
  { id: "custom", label: "Personalizado" },
];

/** The audit filters, kept in the URL (spec §5.2). */
export interface AuditSearch {
  range: RangeId;
  /** Custom range: local days "YYYY-MM-DD", both inclusive; "" leaves that end open */
  since: string;
  until: string;
  category: CategoryId | "";
  /** Who acted */
  userId: string;
  /** userId's name, carried in the URL only to show it in the chip */
  userLabel: string;
  resourceType: string;
  resourceId: string;
  page: number;
}

export const EMPTY_AUDIT_SEARCH: AuditSearch = {
  range: "all",
  since: "",
  until: "",
  category: "",
  userId: "",
  userLabel: "",
  resourceType: "",
  resourceId: "",
  page: 1,
};

const DAY_MS = 24 * 60 * 60 * 1000;
const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

const MIN_YEAR = 1970;

/** A real "YYYY-MM-DD" day from 1970 on. Date inputs report half-typed years ("0002-11-26") while typing. */
export const isValidDay = (value: string | null): value is string => {
  const match = value ? DAY_PATTERN.exec(value) : null;
  if (!match) return false;
  const [, y, m, d] = match.map(Number);
  if (y < MIN_YEAR) return false;
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d;
};

const validDay = (value: string | null): string => (isValidDay(value) ? value : "");

/** Local midnight of a "YYYY-MM-DD" day, plus addDays, as an ISO instant. */
export const localDayStartIso = (day: string, addDays = 0): string => {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d + addDays).toISOString();
};

export const parseAuditSearch = (params: URLSearchParams): AuditSearch => {
  const range = params.get("range");
  const category = params.get("category");
  const page = Number.parseInt(params.get("page") ?? "", 10);
  const userId = params.get("user_id") ?? "";
  return {
    range: RANGES.some((option) => option.id === range) ? (range as RangeId) : "all",
    since: validDay(params.get("since")),
    until: validDay(params.get("until")),
    category: isCategoryId(category) ? category : "",
    userId,
    userLabel: userId ? (params.get("user_label") ?? "") : "",
    resourceType: params.get("resource_type") ?? "",
    resourceId: params.get("resource_id") ?? "",
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
};

export const toAuditSearchParams = (search: AuditSearch): URLSearchParams => {
  const params = new URLSearchParams();
  if (search.range !== "all") params.set("range", search.range);
  if (search.range === "custom") {
    if (search.since) params.set("since", search.since);
    if (search.until) params.set("until", search.until);
  }
  if (search.category) params.set("category", search.category);
  if (search.userId) {
    params.set("user_id", search.userId);
    if (search.userLabel) params.set("user_label", search.userLabel);
  }
  if (search.resourceType) params.set("resource_type", search.resourceType);
  if (search.resourceId) params.set("resource_id", search.resourceId);
  if (search.page > 1) params.set("page", String(search.page));
  return params;
};

/** The API query. Relative ranges are resolved against `now`, so call this when the request starts. */
export const toAuditQuery = (search: AuditSearch, now: number = Date.now()): AuditQuery => {
  const query: AuditQuery = { limit: AUDIT_PAGE_SIZE, offset: (search.page - 1) * AUDIT_PAGE_SIZE };
  if (search.range === "today") {
    const today = new Date(now);
    query.since = new Date(today.getFullYear(), today.getMonth(), today.getDate()).toISOString();
  } else if (search.range === "7d") {
    query.since = new Date(now - 7 * DAY_MS).toISOString();
  } else if (search.range === "30d") {
    query.since = new Date(now - 30 * DAY_MS).toISOString();
  } else if (search.range === "custom") {
    if (search.since) query.since = localDayStartIso(search.since);
    if (search.until) query.until = localDayStartIso(search.until, 1);
  }
  if (search.category) query.action_prefix = categoryPrefix(search.category);
  if (search.userId) query.user_id = search.userId;
  if (search.resourceType) query.resource_type = search.resourceType;
  if (search.resourceId) query.resource_id = search.resourceId;
  return query;
};

/** A custom range whose "from" day is after its "to" day (it would find nothing). */
export const customRangeInvalid = (search: AuditSearch) =>
  search.range === "custom" && !!search.since && !!search.until && search.since > search.until;
