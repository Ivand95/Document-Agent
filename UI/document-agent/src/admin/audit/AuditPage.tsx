import { useSearchParams } from "react-router";
import { useAdmin } from "../adminContext";
import { visibleAreas } from "../areas";
import { AuditFilters } from "./AuditFilters";
import { parseAuditSearch, toAuditSearchParams, type AuditSearch } from "./auditSearch";
import { AuditTable } from "./AuditTable";
import { useAuditEntries } from "./useAuditEntries";

/** /admin/audit (spec §5). Filters and page live in the URL. */
export const AuditPage = () => {
  const { token, permissions } = useAdmin();
  const [searchParams, setSearchParams] = useSearchParams();
  const searchKey = searchParams.toString();
  const search = parseAuditSearch(searchParams);
  const { entries, loading, error, reload } = useAuditEntries(token, searchKey);
  const openAreas = visibleAreas(permissions.admin_areas).map((area) => area.id);

  /** Changes some filters and goes back to page 1, unless `page` is in the patch. */
  const update = (patch: Partial<AuditSearch>, { replace = false }: { replace?: boolean } = {}) =>
    setSearchParams(toAuditSearchParams({ ...search, page: 1, ...patch }), { replace });

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
