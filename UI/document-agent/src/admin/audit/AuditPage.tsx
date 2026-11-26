import { useLocation, useSearchParams } from "react-router";
import { useAdmin } from "../adminContext";
import { visibleAreas } from "../areas";
import { useDropUrlParams } from "../legacyParams";
import { AuditFilters } from "./AuditFilters";
import { parseAuditSearch, toAuditHistoryState, toAuditSearchParams, type AuditSearch } from "./auditSearch";
import { AuditTable } from "./AuditTable";
import { useAuditEntries } from "./useAuditEntries";

/** Params older versions put in the URL; the user's name now lives in the history state. */
const LEGACY_URL_PARAMS = ["user_label"] as const;

/** /admin/audit (spec §5). Filters and page live in the URL; the filtered user's name in the history state. */
export const AuditPage = () => {
  const { token, permissions } = useAdmin();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  // An old bookmark's ?user_label= is ignored and removed from the address bar
  useDropUrlParams(LEGACY_URL_PARAMS);
  const searchKey = searchParams.toString();
  const parsed = parseAuditSearch(searchParams, location.state);
  const { entries, loading, error, reload } = useAuditEntries(token, searchKey);
  const openAreas = visibleAreas(permissions.admin_areas).map((area) => area.id);
  // No name in the state (e.g. a pasted link): the actor of a loaded entry by that user, if any
  const search: AuditSearch =
    parsed.userId && !parsed.userLabel
      ? { ...parsed, userLabel: entries?.find((entry) => entry.user_id === parsed.userId)?.actor ?? "" }
      : parsed;

  /** Changes some filters and goes back to page 1, unless `page` is in the patch. The user's name goes to the
   * history state, never to the URL. */
  const update = (patch: Partial<AuditSearch>, { replace = false }: { replace?: boolean } = {}) => {
    const next: AuditSearch = { ...search, page: 1, ...patch };
    setSearchParams(toAuditSearchParams(next), { replace, state: toAuditHistoryState(next) });
  };

  return (
    <section className="admin-card">
      <h2>Auditoría</h2>
      <AuditFilters search={search} onChange={update} token={token} canPickUser={openAreas.includes("users")} />
      <AuditTable
        entries={entries}
        loading={loading}
        error={error}
        onRetry={reload}
        page={search.page}
        onPageChange={(page) => update({ page })}
        openAreas={openAreas}
        emptyText="No hay entradas con estos filtros."
      />
    </section>
  );
};
