import { Tab, Tabs } from "@mui/material";
import { useState } from "react";
import { Link } from "react-router";
import type { UserDetail } from "../../utils/api/users";
import { useAdmin } from "../adminContext";
import { visibleAreas } from "../areas";
import { AuditTable } from "../audit/AuditTable";
import { EMPTY_AUDIT_SEARCH, toAuditSearchParams, type AuditSearch } from "../audit/auditSearch";
import { useAuditEntries } from "../audit/useAuditEntries";
import { displayName } from "./userLabels";

type ActivityView = "about" | "by";

/** Spec §5.4. Render it with key={user.id}, so the view and page start over for each user. */
export const UserActivityTab = ({ user }: { user: UserDetail }) => {
  const { token, permissions } = useAdmin();
  const [view, setView] = useState<ActivityView>("about");
  const [page, setPage] = useState(1);

  const search: AuditSearch =
    view === "about"
      ? { ...EMPTY_AUDIT_SEARCH, resourceType: "user", resourceId: user.id, page }
      : { ...EMPTY_AUDIT_SEARCH, userId: user.id, userLabel: displayName(user), page };
  const { entries, loading, error, reload } = useAuditEntries(token, toAuditSearchParams(search).toString());
  const openAreas = visibleAreas(permissions.admin_areas).map((area) => area.id);
  const auditLink = `/admin/audit?${toAuditSearchParams({ ...search, page: 1 }).toString()}`;

  return (
    <section className="admin-card">
      <div className="admin-activity-head">
        <Tabs
          value={view}
          onChange={(_, value: ActivityView) => {
            setView(value);
            setPage(1);
          }}
          aria-label="Actividad del usuario"
          className="admin-tabs"
        >
          <Tab value="about" label="Cambios sobre este usuario" />
          <Tab value="by" label="Acciones de este usuario" />
        </Tabs>
        <Link to={auditLink} className="admin-link">
          Abrir en Auditoría
        </Link>
      </div>
      <AuditTable
        entries={entries}
        loading={loading}
        error={error}
        onRetry={reload}
        page={page}
        onPageChange={setPage}
        openAreas={openAreas}
        emptyText={
          view === "about" ? "Nadie ha hecho cambios sobre este usuario." : "Este usuario no tiene acciones registradas."
        }
      />
    </section>
  );
};
