import { bearer, http } from "../axios";

/** A row of audit_log (app/db/migrations/0005_rbac.sql). */
export interface AuditEntry {
  id: number;
  at: string;
  /** Who did it (null for the system) */
  user_id: string | null;
  /** Their email or name, kept even if the user is later removed */
  actor: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  /** "ok" | "denied" | "error" */
  outcome: string;
  details: Record<string, unknown>;
}

/** GET /admin/audit. Newest first, no total. action_prefix: comma-separated prefixes ("role.,mapping."). */
export interface AuditQuery {
  user_id?: string;
  action_prefix?: string;
  resource_type?: string;
  resource_id?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export const listAudit = (token: string, query: AuditQuery, signal?: AbortSignal) =>
  http.get<{ entries: AuditEntry[] }>("/admin/audit", { ...bearer(token), params: query, signal });
