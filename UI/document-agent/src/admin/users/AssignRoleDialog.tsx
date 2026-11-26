import { Button, Dialog, DialogActions, DialogContent, DialogTitle } from "@mui/material";
import { useId, useState } from "react";
import { assignUserRole, type AssignableRole, type UserRolesView } from "../../utils/api/users";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { adminContainer } from "../components/adminContainer";
import { isoToLocalInput, localInputToIso } from "../format";

interface AssignRoleDialogProps {
  open: boolean;
  userId: string;
  roles: AssignableRole[];
  /** Edit mode: changes the expiry of this already-assigned role (assigning again upserts) */
  fixedRole?: { id: string; name: string; key: string; expires_at: string | null };
  onCancel: () => void;
  onAssigned: (view: UserRolesView) => void;
}

/** The parent remounts it (key) each time it opens, so it always starts from the initial values. */
export const AssignRoleDialog = ({ open, userId, roles, fixedRole, onCancel, onAssigned }: AssignRoleDialogProps) => {
  const { token } = useAdmin();
  const titleId = useId();
  const roleFieldId = useId();
  const roleHintId = useId();
  const expiresFieldId = useId();
  const expiresHintId = useId();
  const [roleId, setRoleId] = useState(fixedRole?.id ?? "");
  const [expires, setExpires] = useState(() => isoToLocalInput(fixedRole?.expires_at ?? null));
  const [minExpires] = useState(() => isoToLocalInput(new Date().toISOString()));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = fixedRole !== undefined;
  const selected = roles.find((role) => role.id === roleId);

  const submit = async () => {
    if (!roleId) {
      setError("Elige un rol.");
      return;
    }
    const expiresAt = localInputToIso(expires);
    if (expires && !expiresAt) {
      setError("La fecha de caducidad no es válida.");
      return;
    }
    if (expiresAt && new Date(expiresAt).getTime() <= Date.now()) {
      setError("La caducidad debe ser una fecha futura.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { data } = await assignUserRole(token, userId, { role_id: roleId, expires_at: expiresAt });
      onAssigned(data);
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : onCancel}
      container={adminContainer}
      aria-labelledby={titleId}
      slotProps={{ paper: { className: "admin-dialog" } }}
      fullWidth
      maxWidth="xs"
    >
      <DialogTitle id={titleId}>{editing ? "Cambiar caducidad" : "Asignar rol"}</DialogTitle>
      <DialogContent>
        {fixedRole ? (
          <div className="admin-field">
            <span>Rol</span>
            <span>
              <strong>{fixedRole.name}</strong> ({fixedRole.key})
            </span>
          </div>
        ) : (
          <div className="admin-field">
            <label htmlFor={roleFieldId}>Rol</label>
            <select
              id={roleFieldId}
              className="admin-input"
              value={roleId}
              onChange={(event) => setRoleId(event.target.value)}
              disabled={busy}
              aria-describedby={selected?.description ? roleHintId : undefined}
            >
              <option value="">Elige un rol…</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name} ({role.key})
                </option>
              ))}
            </select>
            {selected?.description && (
              <span id={roleHintId} className="admin-field-hint">
                {selected.description}
              </span>
            )}
          </div>
        )}
        <div className="admin-field">
          <label htmlFor={expiresFieldId}>Caducidad (opcional)</label>
          <input
            id={expiresFieldId}
            type="datetime-local"
            className="admin-input"
            value={expires}
            min={minExpires}
            onChange={(event) => setExpires(event.target.value)}
            disabled={busy}
            aria-describedby={expiresHintId}
          />
          <span id={expiresHintId} className="admin-field-hint">
            Vacío: el rol no caduca.
          </span>
        </div>
        {error && (
          <p className="admin-msg admin-msg--error" role="alert">
            {error}
          </p>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
        <Button variant="contained" onClick={() => void submit()} disabled={busy}>
          {busy ? (editing ? "Guardando…" : "Asignando…") : editing ? "Guardar" : "Asignar"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
