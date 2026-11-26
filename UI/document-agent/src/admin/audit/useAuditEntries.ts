import { useEffect, useState } from "react";
import { listAudit, type AuditEntry } from "../../utils/api/audit";
import { apiErrorMessage } from "../../utils/apiError";
import { parseAuditSearch, toAuditQuery } from "./auditSearch";

/** Loads one page of the audit log.
 *
 * searchKey: the filters as URL params (auditSearch format). It is a string, so the effect re-runs only when
 * the filters change; relative ranges ("Hoy", "7 días") are resolved when each request starts. While a new
 * page loads, the previous entries stay (the table dims them). */
export const useAuditEntries = (token: string, searchKey: string) => {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  // The request each response belongs to: loading = no response yet for the current request
  const [settled, setSettled] = useState<{ key: string; error: string | null } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const requestKey = `${searchKey}#${reloadKey}`;

  useEffect(() => {
    const controller = new AbortController();
    const query = toAuditQuery(parseAuditSearch(new URLSearchParams(searchKey)));
    listAudit(token, query, controller.signal)
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setEntries(data.entries);
        setSettled({ key: requestKey, error: null });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, error: apiErrorMessage(e) });
      });
    return () => {
      controller.abort();
    };
  }, [token, searchKey, requestKey]);

  return {
    entries,
    loading: settled?.key !== requestKey,
    error: settled?.key === requestKey ? settled.error : null,
    reload: () => setReloadKey((key) => key + 1),
  };
};
