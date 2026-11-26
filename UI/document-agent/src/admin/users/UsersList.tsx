import MoreVert from "@mui/icons-material/MoreVert";
import { Button, IconButton, Menu, MenuItem, Tab, Tabs } from "@mui/material";
import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";
import { listUsers, type UserAction, type UserRow, type UsersPage } from "../../utils/api/users";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { adminContainer } from "../components/adminContainer";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { RelativeTime } from "../components/RelativeTime";
import { StatusBadge } from "../components/StatusBadge";
import { useDropUrlParams } from "../legacyParams";
import { ACTION_LABELS, PROTECTED_LABELS, STATUS_LABELS, STATUS_TONES, displayName } from "./userLabels";
import {
  STATUS_TABS,
  USERS_PAGE_SIZE,
  defaultStatusTab,
  fixedDepartment,
  parseUsersSearch,
  toSearchParams,
  toUsersListState,
  toUsersQuery,
  usersStateQ,
  type StatusTab,
  type UsersSearch,
} from "./usersSearch";
import { useStatusChange } from "./useStatusChange";
import "./users.css";

const SEARCH_DELAY_MS = 300;
/** Params older versions put in the URL; the search text now lives in the history state. */
const LEGACY_URL_PARAMS = ["q"] as const;

/** State for a user's detail: the list's URL filters and its search text, so "← Usuarios" restores both. */
const detailLinkState = (location: { search: string; state: unknown }) => ({
  from: location.search,
  q: usersStateQ(location.state),
});

const RowActions = ({
  user,
  busy,
  onAction,
}: {
  user: UserRow;
  busy: boolean;
  onAction: (action: UserAction) => void;
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const menuId = useId();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = () => setAnchor(null);

  if (!user.actionable) {
    return <span className="admin-muted">🔒 {user.protected ? PROTECTED_LABELS[user.protected] : "Protegido"}</span>;
  }

  return (
    <div className="admin-row-actions">
      {user.actions.includes("approve") && (
        <Button
          size="small"
          variant="contained"
          disabled={busy}
          aria-label={`Aprobar a ${displayName(user)}`}
          onClick={() => onAction("approve")}
        >
          {ACTION_LABELS.approve}
        </Button>
      )}
      <IconButton
        size="small"
        aria-label={`Más acciones para ${displayName(user)}`}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        aria-controls={menuId}
        onClick={(event) => setAnchor(event.currentTarget)}
        sx={{ color: "var(--chat-text)" }}
      >
        <MoreVert fontSize="small" />
      </IconButton>
      <Menu
        id={menuId}
        anchorEl={anchor}
        open={anchor !== null}
        onClose={close}
        container={adminContainer}
        slotProps={{ paper: { className: "admin-menu" } }}
      >
        {user.actions
          .filter((action) => action !== "approve")
          .map((action) => (
            <MenuItem
              key={action}
              disabled={busy}
              onClick={() => {
                close();
                onAction(action);
              }}
            >
              {ACTION_LABELS[action]}
            </MenuItem>
          ))}
        <MenuItem
          onClick={() => {
            close();
            navigate(`/admin/users/${user.id}`, { state: detailLinkState(location) });
          }}
        >
          Ver detalle
        </MenuItem>
      </Menu>
    </div>
  );
};

export const UsersList = () => {
  const { token, refreshSummary } = useAdmin();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  // status/department/page come from the URL, q from the history state (it must never reach a URL);
  // an old bookmark's ?q= is ignored and removed from the address bar
  useDropUrlParams(LEGACY_URL_PARAMS);
  const search = parseUsersSearch(searchParams, location.state);
  const queryKey = JSON.stringify(search);

  /** What the user is typing, until it's committed to the history state; null: the input shows its q */
  const [draft, setDraft] = useState<string | null>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [data, setData] = useState<UsersPage | null>(null);
  // The request each response belongs to: loading = no response yet for the current request
  const [settled, setSettled] = useState<{ key: string; error: string | null } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const requestKey = `${queryKey}#${reloadKey}`;

  /** Changes some filters (plus any search still being typed) and goes back to page 1, unless `page` is in
   * the patch. Built from this render's params: RR's functional setSearchParams would get the params of the
   * render that created the callback, not the latest ones. */
  const update = (patch: Partial<UsersSearch>, { replace = false }: { replace?: boolean } = {}) => {
    clearTimeout(searchTimer.current);
    const pendingQ = draft !== null ? { q: draft } : {};
    if (draft !== null) setDraft(null);
    const next: UsersSearch = { ...search, page: 1, ...pendingQ, ...patch };
    setSearchParams(toSearchParams(next), { replace, state: toUsersListState(next) });
  };

  // Timers and responses call the latest render's update (its search and draft are current)
  const latestUpdate = useRef(update);
  useEffect(() => {
    latestUpdate.current = update;
  });

  useEffect(() => {
    const controller = new AbortController();
    const current = JSON.parse(queryKey) as UsersSearch;
    listUsers(token, toUsersQuery(current), controller.signal)
      .then(({ data: page }) => {
        if (controller.signal.aborted) return;
        setData(page);
        setSettled({ key: requestKey, error: null });
        const patch: Partial<UsersSearch> = {};
        // Spec §3.1: write the default tab once, so later reloads never move the user to another tab
        if (current.status === null) {
          patch.status = defaultStatusTab(page.counts);
          patch.page = current.page;
        }
        // Past the last page (e.g. users left this filter): go to the last one
        if (page.users.length === 0 && page.offset > 0 && page.total > 0) {
          patch.page = Math.ceil(page.total / USERS_PAGE_SIZE);
        }
        if (Object.keys(patch).length > 0) latestUpdate.current(patch, { replace: true });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, error: apiErrorMessage(e) });
      });
    return () => {
      controller.abort();
    };
  }, [token, queryKey, requestKey]);

  useEffect(() => () => clearTimeout(searchTimer.current), []);

  const onSearchChange = (value: string) => {
    setDraft(value);
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => latestUpdate.current({}, { replace: true }), SEARCH_DELAY_MS);
  };

  const statusChange = useStatusChange(token, () => {
    setReloadKey((key) => key + 1);
    refreshSummary();
  });

  const loading = settled?.key !== requestKey;
  const error = settled?.key === requestKey ? settled.error : null;
  const activeTab: StatusTab = search.status ?? "all";
  const fixed = data ? fixedDepartment(data.scope) : null;

  const columns: Column<UserRow>[] = [
    {
      key: "name",
      header: "Nombre",
      render: (user) => (
        <div className="admin-user-cell">
          <Link to={`/admin/users/${user.id}`} state={detailLinkState(location)} className="admin-link">
            {displayName(user)}
          </Link>
          <small>{user.email ?? "—"}</small>
        </div>
      ),
    },
    {
      key: "department",
      header: "Departamento",
      render: (user) => (
        <div className="admin-user-cell">
          <span>{user.department ?? "—"}</span>
          <small>{user.position ?? "—"}</small>
        </div>
      ),
    },
    {
      key: "status",
      header: "Estado",
      render: (user) => <StatusBadge tone={STATUS_TONES[user.status]}>{STATUS_LABELS[user.status]}</StatusBadge>,
    },
    { key: "last", header: "Último acceso", render: (user) => <RelativeTime iso={user.last_login_at} /> },
    {
      key: "actions",
      header: "Acciones",
      className: "admin-col-actions",
      render: (user) => (
        <RowActions
          user={user}
          busy={statusChange.busyId !== null}
          onAction={(action) => statusChange.request(user, action)}
        />
      ),
    },
  ];

  return (
    <section className="admin-card">
      <h2>Usuarios</h2>
      <Tabs
        value={activeTab}
        onChange={(_, value: StatusTab) => update({ status: value })}
        aria-label="Filtrar por estado"
        variant="scrollable"
        allowScrollButtonsMobile
        className="admin-tabs"
      >
        {STATUS_TABS.map((tab) => (
          <Tab key={tab.id} value={tab.id} label={data ? `${tab.label} (${data.counts[tab.id]})` : tab.label} />
        ))}
      </Tabs>

      <div className="admin-filters">
        <input
          type="search"
          className="admin-input"
          placeholder="Buscar por nombre o email"
          aria-label="Buscar usuarios"
          value={draft ?? search.q}
          onChange={(event) => onSearchChange(event.target.value)}
        />
        {data &&
          (fixed ? (
            <span className="admin-muted">
              Departamento: <strong>{fixed}</strong>
            </span>
          ) : (
            <select
              className="admin-input"
              aria-label="Filtrar por departamento"
              value={search.department}
              onChange={(event) => update({ department: event.target.value })}
            >
              <option value="">{data.scope.limited ? "Todos mis departamentos" : "Todos los departamentos"}</option>
              {data.scope.departments.map((department) => (
                <option key={department} value={department}>
                  {department}
                </option>
              ))}
            </select>
          ))}
      </div>

      {statusChange.error && (
        <p className="admin-msg admin-msg--error" role="alert">
          {statusChange.error}
        </p>
      )}

      <DataTable
        caption="Usuarios"
        columns={columns}
        rows={data?.users ?? null}
        rowKey={(user) => user.id}
        loading={loading && data !== null}
        error={error}
        onRetry={() => setReloadKey((key) => key + 1)}
        emptyText="No hay usuarios con estos filtros."
        rowClassName={(user) => (user.actionable ? undefined : "admin-row--muted")}
        pagination={
          data
            ? {
                offset: data.offset,
                limit: USERS_PAGE_SIZE,
                total: data.total,
                onChange: (offset) => update({ page: offset / USERS_PAGE_SIZE + 1 }),
              }
            : undefined
        }
      />
      <ConfirmDialog {...statusChange.confirmProps} />
    </section>
  );
};
