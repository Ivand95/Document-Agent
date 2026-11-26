import { Button } from "@mui/material";
import { useEffect, useState } from "react";
import { getUserRoles, removeUserRole, type UserRole, type UserRolesView } from "../../utils/api/users";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../format";
import { AssignRoleDialog } from "./AssignRoleDialog";
import { sourceLabel } from "./userLabels";

const removeMessage = (role: UserRole) =>
  role.automatic
    ? `Se quitará la asignación manual del rol «${role.name}». El usuario lo seguirá teniendo por: ${role.sources
        .filter((source) => source !== "assigned")
        .map(sourceLabel)
        .join(", ")}.`
    : `Se quitará el rol «${role.name}» a este usuario. Perderá sus permisos en menos de 30 segundos.`;

export const UserRolesTab = ({ userId }: { userId: string }) => {
  const { token } = useAdmin();
  const [view, setView] = useState<UserRolesView | null>(null);
  const [loadError, setLoadError] = useState<{ userId: string; text: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignKey, setAssignKey] = useState(0);
  /** The role whose expiry the dialog changes; null: the dialog assigns a new role */
  const [editing, setEditing] = useState<UserRole | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [removing, setRemoving] = useState<UserRole | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getUserRoles(token, userId, controller.signal)
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setView(data);
        setLoadError(null);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setLoadError({ userId, text: apiErrorMessage(e) });
      });
    return () => {
      controller.abort();
    };
  }, [token, userId, reloadKey]);

  // Ignore a previous user's data while the new one loads
  const current = view?.user_id === userId ? view : null;
  const error = loadError?.userId === userId ? loadError.text : null;

  const openDialog = (role: UserRole | null) => {
    setEditing(role);
    setAssignKey((key) => key + 1);
    setAssignOpen(true);
  };

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    setActionError(null);
    try {
      const { data } = await removeUserRole(token, userId, removing.id);
      setView(data);
    } catch (e) {
      setActionError(apiErrorMessage(e));
    } finally {
      setBusy(false);
      setRemoving(null);
    }
  };

  const columns: Column<UserRole>[] = [
    {
      key: "role",
      header: "Rol",
      render: (role) => (
        <div className="admin-user-cell">
          <span>
            {role.name}{" "}
            {role.expired && !role.automatic && <StatusBadge tone="muted">Asignación manual caducada</StatusBadge>}
          </span>
          <small>
            <code>{role.key}</code>
          </small>
        </div>
      ),
    },
    {
      key: "sources",
      header: "Origen",
      render: (role) => (role.sources.length ? role.sources.map(sourceLabel).join(", ") : "—"),
    },
    {
      key: "expires",
      header: "Caducidad",
      render: (role) => (role.assigned ? (role.expires_at ? formatDateTime(role.expires_at) : "Sin caducidad") : "—"),
    },
    { key: "granted", header: "Asignado por", render: (role) => role.granted_by ?? "—" },
    {
      key: "actions",
      header: "",
      className: "admin-col-actions",
      render: (role) =>
        role.removable ? (
          <div className="admin-row-actions">
            {role.assigned && (
              <Button size="small" onClick={() => openDialog(role)}>
                Cambiar caducidad
              </Button>
            )}
            <Button size="small" color="error" onClick={() => setRemoving(role)}>
              Quitar
            </Button>
          </div>
        ) : role.automatic && !role.assigned ? (
          <span className="admin-muted">Automático</span>
        ) : null,
    },
  ];

  return (
    <section className="admin-card">
      <div className="admin-card-head">
        <h2>Roles</h2>
        {current && current.assignable.length > 0 && (
          <Button
            variant="contained"
            size="small"
            onClick={() => openDialog(null)}
          >
            + Asignar rol
          </Button>
        )}
      </div>
      {current && !current.manage_all_roles && (
        <p className="admin-muted">Solo ves los roles que pueden asignar los gerentes.</p>
      )}
      {actionError && (
        <p className="admin-msg admin-msg--error" role="alert">
          {actionError}
        </p>
      )}
      <DataTable
        caption="Roles del usuario"
        columns={columns}
        rows={current?.roles ?? null}
        rowKey={(role) => role.id}
        error={error}
        onRetry={() => {
          setLoadError(null);
          setReloadKey((key) => key + 1);
        }}
        emptyText="Este usuario no tiene roles."
      />
      {current?.roles.some((role) => role.automatic) && (
        <p className="admin-muted">
          Los roles automáticos vienen de una asignación por departamento o puesto y se cambian en Roles y permisos.
        </p>
      )}
      {current && (
        <AssignRoleDialog
          key={assignKey}
          open={assignOpen}
          userId={userId}
          roles={current.assignable}
          fixedRole={editing ?? undefined}
          onCancel={() => setAssignOpen(false)}
          onAssigned={(next) => {
            setView(next);
            setAssignOpen(false);
          }}
        />
      )}
      <ConfirmDialog
        open={removing !== null}
        title="¿Quitar rol?"
        message={removing ? removeMessage(removing) : ""}
        confirmLabel="Quitar"
        destructive
        busy={busy}
        onCancel={() => setRemoving(null)}
        onConfirm={() => void remove()}
      />
    </section>
  );
};
