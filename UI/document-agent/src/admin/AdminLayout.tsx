import ArrowBack from "@mui/icons-material/ArrowBack";
import Close from "@mui/icons-material/Close";
import Menu from "@mui/icons-material/Menu";
import { Button, CircularProgress, IconButton } from "@mui/material";
import { Suspense, useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { ThemeToggle } from "../components/ThemeToggle";
import { listUsers, type UsersScope } from "../utils/api/users";
import { useMyPermissions } from "../utils/permissions";
import type { AdminContext } from "./adminContext";
import { scopeLabel, visibleGroups } from "./areas";
import { ScopeChip } from "./components/ScopeChip";
import "./style.css";

interface UsersSummary {
  pending: number;
  scope: UsersScope;
}

export const AdminLayout = ({ userInfo }: { userInfo: UserInfo }) => {
  const navigate = useNavigate();
  const token = userInfo.access_token;
  const { permissions, failed, refetch } = useMyPermissions(token);
  const [summary, setSummary] = useState<UsersSummary | null>(null);
  const [summaryKey, setSummaryKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const refreshSummary = useCallback(() => setSummaryKey((key) => key + 1), []);

  const canUsers = !!permissions?.admin_areas.includes("users");

  // Pending-users badge and the admin's scope (a department admin's departments) in one small request
  useEffect(() => {
    if (!canUsers) return;
    let cancelled = false;
    listUsers(token, { status: "pending", limit: 1 })
      .then(({ data }) => {
        if (!cancelled) setSummary({ pending: data.counts.pending, scope: data.scope });
      })
      .catch(() => {
        // Optional: without it there is no badge and the chip says "Global"; the API still enforces the scope
      });
    return () => {
      cancelled = true;
    };
  }, [token, canUsers, summaryKey]);

  const usersSummary = canUsers ? summary : null;
  const groups = permissions ? visibleGroups(permissions.admin_areas) : [];

  let content;
  if (permissions === null) {
    content = failed ? (
      <section className="admin-card">
        <p className="admin-msg admin-msg--error">No se pudieron cargar tus permisos.</p>
        <div className="admin-actions">
          <Button variant="contained" onClick={refetch}>
            Reintentar
          </Button>
        </div>
      </section>
    ) : (
      <div className="admin-center">
        <CircularProgress size={28} />
      </div>
    );
  } else if (groups.length === 0) {
    content = (
      <section className="admin-card">
        <p className="admin-msg admin-msg--error">No tienes acceso a la administración.</p>
        <div className="admin-actions">
          <Button onClick={() => navigate("/chat")}>Volver al chat</Button>
        </div>
      </section>
    );
  } else {
    const context: AdminContext = {
      token,
      permissions,
      usersScope: usersSummary?.scope ?? null,
      refreshSummary,
    };
    content = (
      <Suspense
        fallback={
          <div className="admin-center">
            <CircularProgress size={28} />
          </div>
        }
      >
        <Outlet context={context} />
      </Suspense>
    );
  }

  const showNav = groups.length > 0;

  return (
    <div className="admin-page">
      <header className="admin-header">
        <div className="admin-header-start">
          {showNav && (
            <IconButton
              className="admin-menu-button"
              aria-label={menuOpen ? "Cerrar menú" : "Abrir menú"}
              aria-expanded={menuOpen}
              aria-controls="admin-sidebar"
              onClick={() => setMenuOpen((open) => !open)}
              sx={{ color: "var(--chat-text)" }}
            >
              {menuOpen ? <Close /> : <Menu />}
            </IconButton>
          )}
          <Button startIcon={<ArrowBack />} onClick={() => navigate("/chat")}>
            Volver al chat
          </Button>
        </div>
        <h1>Administración</h1>
        <div className="admin-header-end">
          {permissions && showNav && <ScopeChip label={scopeLabel(permissions.roles, usersSummary?.scope ?? null)} />}
          <ThemeToggle />
        </div>
      </header>

      <div className="admin-shell">
        {showNav && (
          <>
            <nav
              id="admin-sidebar"
              className={`admin-sidebar${menuOpen ? " admin-sidebar--open" : ""}`}
              aria-label="Áreas de administración"
            >
              {groups.map((group) => (
                <div key={group.id} className="admin-nav-group">
                  <p className="admin-nav-group-label">{group.label}</p>
                  {group.areas.map((area) => (
                    <NavLink
                      key={area.id}
                      to={`/admin/${area.path}`}
                      className={({ isActive }) => `admin-nav-link${isActive ? " admin-nav-link--active" : ""}`}
                      onClick={() => setMenuOpen(false)}
                    >
                      <span>{area.label}</span>
                      {area.id === "users" && !!usersSummary?.pending && (
                        <span className="admin-nav-badge" aria-label={`${usersSummary.pending} pendientes`}>
                          {usersSummary.pending}
                        </span>
                      )}
                    </NavLink>
                  ))}
                </div>
              ))}
            </nav>
            {menuOpen && <div className="admin-backdrop" onClick={() => setMenuOpen(false)} aria-hidden />}
          </>
        )}
        <main className="admin-content">{content}</main>
      </div>
    </div>
  );
};
