import ArrowBack from "@mui/icons-material/ArrowBack";
import { CircularProgress, Tab, Tabs } from "@mui/material";
import { isAxiosError } from "axios";
import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router";
import {
  getAdminDepartments,
  getUser,
  type UserDetail as UserDetailData,
} from "../../utils/api/users";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { visibleAreas } from "../areas";
import { StatusBadge } from "../components/StatusBadge";
import { useApiResource } from "../useApiResource";
import {
  PROTECTED_LABELS,
  STATUS_LABELS,
  STATUS_TONES,
  displayName,
} from "./userLabels";
import { UserActivityTab } from "./UserActivityTab";
import { UserDepartmentsTab } from "./UserDepartmentsTab";
import { UserEffectiveTab } from "./UserEffectiveTab";
import { UserPermissionsTab } from "./UserPermissionsTab";
import { UserRolesTab } from "./UserRolesTab";
import { UserStatusTab } from "./UserStatusTab";
import { usersStateQ } from "./usersSearch";
import "./users.css";

type DetailTab =
  "status" | "roles" | "permissions" | "effective" | "departments" | "activity";

export const UserDetail = () => {
  const { id = "" } = useParams();
  const { token, refreshSummary, permissions } = useAdmin();
  const open = visibleAreas(permissions.admin_areas).map((area) => area.id);
  const canAudit = open.includes("audit");
  const canRbac = open.includes("rbac");
  const loadDepartments = useCallback(
    (signal: AbortSignal) =>
      getAdminDepartments(token, id, signal).then(({ data }) => data),
    [token, id],
  );
  // Only to know whether the user is a department admin (the tab) and what they manage; errors just hide the tab
  const departments = useApiResource(canRbac ? id : null, loadDepartments);
  const isDepartmentAdmin = departments.data?.is_department_admin === true;
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  /** The list's URL filters and search text when coming from it (UsersList passes them as state) */
  const listSearch = (location.state as { from?: string } | null)?.from ?? "";
  const listQ = usersStateQ(location.state);
  const available: DetailTab[] = [
    "status",
    "roles",
    ...(canRbac ? (["permissions", "effective"] as const) : []),
    ...(isDepartmentAdmin ? (["departments"] as const) : []),
    ...(canAudit ? (["activity"] as const) : []),
  ];
  const requestedTab = searchParams.get("tab") as DetailTab | null;
  const tab: DetailTab =
    requestedTab && available.includes(requestedTab) ? requestedTab : "status";

  const [user, setUser] = useState<UserDetailData | null>(null);
  const [failure, setFailure] = useState<{
    id: string;
    text: string;
    notFound: boolean;
  } | null>(null);
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
          setFailure({
            id,
            text: apiErrorMessage(e),
            notFound: status === 404 || status === 422,
          });
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
    <Link
      to={`/admin/users${listSearch}`}
      state={listQ ? { q: listQ } : null}
      className="admin-back-link"
    >
      <ArrowBack fontSize="small" /> Usuarios
    </Link>
  );

  if (!current) {
    return (
      <section className="admin-card">
        {back}
        {currentFailure ? (
          <p className="admin-msg admin-msg--error">
            {currentFailure.notFound
              ? "Usuario no encontrado o fuera de tu alcance."
              : currentFailure.text}
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
              {current.email ?? "—"} ·{" "}
              {current.department ?? "Sin departamento"} ·{" "}
              {current.position ?? "Sin puesto"}
            </p>
          </div>
          <div className="admin-detail-badges">
            <StatusBadge tone={STATUS_TONES[current.status]}>
              {STATUS_LABELS[current.status]}
            </StatusBadge>
            {current.protected && (
              <StatusBadge tone="muted">
                🔒 {PROTECTED_LABELS[current.protected]}
              </StatusBadge>
            )}
          </div>
        </div>
        {currentFailure && (
          <p className="admin-msg admin-msg--error">{currentFailure.text}</p>
        )}
        <Tabs
          value={tab}
          onChange={(_, value: DetailTab) =>
            setSearchParams(value === "status" ? {} : { tab: value }, {
              replace: true,
              state: location.state,
            })
          }
          aria-label="Secciones del usuario"
          variant="scrollable"
          allowScrollButtonsMobile
          className="admin-tabs"
        >
          <Tab value="status" label="Estado" />
          <Tab value="roles" label="Roles" />
          {canRbac && <Tab value="permissions" label="Permisos directos" />}
          {canRbac && <Tab value="effective" label="Permisos efectivos" />}
          {isDepartmentAdmin && (
            <Tab value="departments" label="Departamentos administrados" />
          )}
          {canAudit && <Tab value="activity" label="Actividad" />}
        </Tabs>
      </section>
      {tab === "status" && (
        <UserStatusTab
          user={current}
          onChanged={() => {
            reload();
            departments.reload();
          }}
        />
      )}
      {tab === "roles" && (
        <UserRolesTab userId={current.id} onChanged={departments.reload} />
      )}
      {tab === "permissions" && <UserPermissionsTab user={current} />}
      {tab === "effective" && <UserEffectiveTab userId={current.id} />}
      {tab === "departments" && departments.data && (
        <UserDepartmentsTab
          view={departments.data}
          onChange={departments.setData}
          lock={
            current.protected === "self"
              ? "Es tu cuenta: no puedes cambiar tus propios departamentos."
              : current.protected === "super_admin"
                ? "Es un super administrador: solo otro super administrador puede cambiarlo."
                : null
          }
        />
      )}
      {tab === "activity" && (
        <UserActivityTab key={current.id} user={current} />
      )}
    </>
  );
};
