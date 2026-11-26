import { Button } from "@mui/material";
import { useId, useState } from "react";
import { useNavigate } from "react-router";
import { deleteRole, updateRole, type Role } from "../../utils/api/rbac";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";

interface RoleHeaderProps {
  role: Role;
  /** Why the admin can't change this role (super_admin for a non super admin), or null */
  lock: string | null;
  onChanged: (role: Role) => void;
}

/** Spec §4.2: name and description editable in place, "Asignable por gerentes", delete. */
export const RoleHeader = ({ role, lock, onChanged }: RoleHeaderProps) => {
  const { token } = useAdmin();
  const navigate = useNavigate();
  const nameId = useId();
  const descriptionId = useId();
  const managerId = useId();
  const managerErrorId = useId();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(role.name);
  const [description, setDescription] = useState(role.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [managerBusy, setManagerBusy] = useState(false);
  const [managerError, setManagerError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const startEditing = () => {
    setName(role.name);
    setDescription(role.description ?? "");
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    if (!name.trim()) {
      setError("El nombre no puede estar vacío.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { data } = await updateRole(token, role.id, {
        name: name.trim(),
        description: description.trim(),
      });
      onChanged(data);
      setEditing(false);
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const setManagerAssignable = async (value: boolean) => {
    setManagerBusy(true);
    setManagerError(null);
    try {
      const { data } = await updateRole(token, role.id, {
        manager_assignable: value,
      });
      onChanged(data);
    } catch (e) {
      setManagerError(apiErrorMessage(e));
    } finally {
      setManagerBusy(false);
    }
  };

  const remove = async () => {
    setDeleteBusy(true);
    try {
      await deleteRole(token, role.id);
      navigate("/admin/rbac/roles", { replace: true });
    } catch (e) {
      setError(apiErrorMessage(e));
      setDeleteBusy(false);
      setConfirmDelete(false);
    }
  };

  return (
    <>
      <div className="admin-detail-head">
        <div>
          <div className="admin-rbac-head-title">
            <h2>{role.name}</h2>
            <code>{role.key}</code>
            {role.is_system && (
              <StatusBadge tone="muted">🔒 Sistema</StatusBadge>
            )}
          </div>
          {!editing && (
            <p className="admin-muted">
              {role.description || "Sin descripción."}
            </p>
          )}
        </div>
        {!lock && !editing && (
          <div className="admin-row-actions">
            <Button size="small" onClick={startEditing}>
              Editar
            </Button>
            {!role.is_system && (
              <Button
                size="small"
                color="error"
                onClick={() => setConfirmDelete(true)}
              >
                Eliminar
              </Button>
            )}
          </div>
        )}
      </div>
      {editing && (
        <div className="admin-rbac-edit">
          <div className="admin-field">
            <label htmlFor={nameId}>Nombre</label>
            <input
              id={nameId}
              className="admin-input"
              value={name}
              maxLength={100}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="admin-field">
            <label htmlFor={descriptionId}>Descripción</label>
            <textarea
              id={descriptionId}
              className="admin-input"
              rows={2}
              value={description}
              maxLength={500}
              disabled={busy}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="admin-actions">
            <Button
              variant="contained"
              size="small"
              onClick={() => void save()}
              disabled={busy}
            >
              {busy ? "Guardando…" : "Guardar"}
            </Button>
            <Button
              size="small"
              onClick={() => setEditing(false)}
              disabled={busy}
            >
              Cancelar
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p className="admin-msg admin-msg--error" role="alert">
          {error}
        </p>
      )}
      <div className="admin-rbac-check">
        <input
          id={managerId}
          type="checkbox"
          checked={role.manager_assignable}
          disabled={!!lock || managerBusy}
          aria-describedby={managerError ? managerErrorId : undefined}
          onChange={(event) => void setManagerAssignable(event.target.checked)}
        />
        <label htmlFor={managerId}>Asignable por gerentes</label>
        {managerError && (
          <span
            id={managerErrorId}
            className="admin-msg admin-msg--error"
            role="alert"
          >
            {managerError}
          </span>
        )}
      </div>
      {lock && <p className="admin-msg admin-msg--warn">🔒 {lock}</p>}
      <ConfirmDialog
        open={confirmDelete}
        title="¿Eliminar rol?"
        message={`Se eliminará «${role.name}» junto con sus asignaciones automáticas, sus asignaciones a usuarios y sus permisos. Quien lo tenga perderá esos permisos en menos de 30 segundos.`}
        confirmLabel="Eliminar"
        destructive
        busy={deleteBusy}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => void remove()}
      />
    </>
  );
};
