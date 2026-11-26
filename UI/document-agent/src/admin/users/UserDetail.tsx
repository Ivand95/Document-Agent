import ArrowBack from "@mui/icons-material/ArrowBack";
import { CircularProgress, Tab, Tabs } from "@mui/material";
import { isAxiosError } from "axios";
import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router";
import { getUser, type UserDetail as UserDetailData } from "../../utils/api/users";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { visibleAreas } from "../areas";
import { StatusBadge } from "../components/StatusBadge";
import { PROTECTED_LABELS, STATUS_LABELS, STATUS_TONES, displayName } from "./userLabels";
import { UserActivityTab } from "./UserActivityTab";
import { UserRolesTab } from "./UserRolesTab";
import { UserStatusTab } from "./UserStatusTab";
import "./users.css";

type DetailTab = "status" | "roles" | "activity";

export const UserDetail = () => {
  const { id = "" } = useParams();
  const { token, refreshSummary, permissions } = useAdmin();
  const canAudit = visibleAreas(permissions.admin_areas).some((area) => area.id === "audit");
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  /** The list's filters when coming from it (UsersList passes them as state) */
  const listSearch = (location.state as { from?: string } | null)?.from ?? "";
  const requestedTab = searchParams.get("tab");
  const tab: DetailTab =
    requestedTab === "roles" ? "roles" : requestedTab === "activity" && canAudit ? "activity" : "status";

  const [user, setUser] = useState<UserDetailData | null>(null);
  const [failure, setFailure] = useState<{ id: string; text: string; notFound: boolean } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getUser(token, id, controller.signal)
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setUser(data);
        setFailure(null);
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          // 422: the id isn't a UUID, so no such user either
          const status = isAxiosError(e) ? e.response?.status : undefined;
          setFailure({ id, text: apiErrorMessage(e), notFound: status === 404 || status === 422 });
        }
      });
    return () => {
      controller.abort();
    };
  }, [token, id, reloadKey]);

  const reload = useCallback(() => {
    setReloadKey((key) => key + 1);
    refreshSummary();
  }, [refreshSummary]);

  // Ignore data of a previous :id while the new one loads
  const current = user?.id === id ? user : null;
  const currentFailure = failure?.id === id ? failure : null;

  const back = (
    <Link to={`/admin/users${listSearch}`} className="admin-back-link">
      <ArrowBack fontSize="small" /> Usuarios
    </Link>
  );

  if (!current) {
    return (
      <section className="admin-card">
        {back}
        {currentFailure ? (
          <p className="admin-msg admin-msg--error">
            {currentFailure.notFound ? "Usuario no encontrado o fuera de tu alcance." : currentFailure.text}
          </p>
        ) : (
          <div className="admin-center">
            <CircularProgress size={24} />
          </div>
        )}
      </section>
    );
  }

  return (
    <>
      <section className="admin-card">
        {back}
        <div className="admin-detail-head">
          <div>
            <h2>{displayName(current)}</h2>
            <p className="admin-muted">
              {current.email ?? "—"} · {current.department ?? "Sin departamento"} · {current.position ?? "Sin puesto"}
            </p>
          </div>
          <div className="admin-detail-badges">
            <StatusBadge tone={STATUS_TONES[current.status]}>{STATUS_LABELS[current.status]}</StatusBadge>
            {current.protected && <StatusBadge tone="muted">🔒 {PROTECTED_LABELS[current.protected]}</StatusBadge>}
          </div>
        </div>
        {currentFailure && <p className="admin-msg admin-msg--error">{currentFailure.text}</p>}
        <Tabs
          value={tab}
          onChange={(_, value: DetailTab) => setSearchParams(value === "status" ? {} : { tab: value }, { replace: true, state: location.state })}
          aria-label="Secciones del usuario"
          className="admin-tabs"
        >
          <Tab value="status" label="Estado" />
          <Tab value="roles" label="Roles" />
          {canAudit && <Tab value="activity" label="Actividad" />}
        </Tabs>
      </section>
      {tab === "status" && <UserStatusTab user={current} onChanged={reload} />}
      {tab === "roles" && <UserRolesTab userId={current.id} />}
      {tab === "activity" && <UserActivityTab key={current.id} user={current} />}
    </>
  );
};
