import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { useId, useState } from "react";
import { createRole, type Role } from "../../utils/api/rbac";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { adminContainer } from "../components/adminContainer";
import { roleKeyError } from "./rbacLabels";

interface NewRoleDialogProps {
  open: boolean;
  onCancel: () => void;
  onCreated: (role: Role) => void;
}

/** The parent remounts it (key) each time it opens. */
export const NewRoleDialog = ({
  open,
  onCancel,
  onCreated,
}: NewRoleDialogProps) => {
  const { token } = useAdmin();
  const titleId = useId();
  const keyId = useId();
  const keyHintId = useId();
  const nameId = useId();
  const nameErrorId = useId();
  const descriptionId = useId();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyError = touched ? roleKeyError(key) : null;
  const nameError = touched && !name.trim() ? "Escribe un nombre." : null;

  const submit = async () => {
    setTouched(true);
    if (roleKeyError(key) || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await createRole(token, {
        key,
        name: name.trim(),
        description: description.trim() || null,
      });
      onCreated(data);
    } catch (e) {
      setError(apiErrorMessage(e));
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
      <DialogTitle id={titleId}>Nuevo rol</DialogTitle>
      <DialogContent>
        <div className="admin-field">
          <label htmlFor={keyId}>Clave</label>
          <input
            id={keyId}
            className="admin-input"
            value={key}
            maxLength={63}
            disabled={busy}
            aria-invalid={!!keyError}
            aria-describedby={keyHintId}
            onChange={(event) => setKey(event.target.value)}
          />
          <span
            id={keyHintId}
            className={
              keyError ? "admin-msg admin-msg--error" : "admin-field-hint"
            }
          >
            {keyError ?? "No se puede cambiar después. Ejemplo: report_user."}
          </span>
        </div>
        <div className="admin-field">
          <label htmlFor={nameId}>Nombre</label>
          <input
            id={nameId}
            className="admin-input"
            value={name}
            maxLength={100}
            disabled={busy}
            aria-invalid={!!nameError}
            aria-describedby={nameError ? nameErrorId : undefined}
            onChange={(event) => setName(event.target.value)}
          />
          {nameError && (
            <span id={nameErrorId} className="admin-msg admin-msg--error">
              {nameError}
            </span>
          )}
        </div>
        <div className="admin-field">
          <label htmlFor={descriptionId}>Descripción (opcional)</label>
          <textarea
            id={descriptionId}
            className="admin-input"
            rows={3}
            value={description}
            maxLength={500}
            disabled={busy}
            onChange={(event) => setDescription(event.target.value)}
          />
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
        <Button
          variant="contained"
          onClick={() => void submit()}
          disabled={busy}
        >
          {busy ? "Creando…" : "Crear"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
