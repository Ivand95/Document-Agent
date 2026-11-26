import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { useId, useState } from "react";
import {
  createMapping,
  type MappingKind,
  type Role,
  type RoleMapping,
} from "../../utils/api/rbac";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { adminContainer } from "../components/adminContainer";
import { MAPPING_KIND_LABELS, mappingValueError } from "./rbacLabels";

interface NewMappingDialogProps {
  open: boolean;
  /** Roles to choose from (already without super_admin for non super admins) */
  roles: Role[];
  /** The role detail's tab: the role is fixed */
  fixedRole?: Role;
  onCancel: () => void;
  onCreated: (mapping: RoleMapping) => void;
}

const KINDS = Object.keys(MAPPING_KIND_LABELS) as MappingKind[];

/** The parent remounts it (key) each time it opens. */
export const NewMappingDialog = ({
  open,
  roles,
  fixedRole,
  onCancel,
  onCreated,
}: NewMappingDialogProps) => {
  const { token, usersScope } = useAdmin();
  const titleId = useId();
  const kindId = useId();
  const valueId = useId();
  const valueHintId = useId();
  const listId = useId();
  const roleId = useId();
  const roleErrorId = useId();
  const [kind, setKind] = useState<MappingKind>("department");
  const [value, setValue] = useState("");
  const [role, setRole] = useState(fixedRole?.id ?? "");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valueError = touched ? mappingValueError(kind, value) : null;
  const roleError = touched && !role ? "Elige un rol." : null;

  const submit = async () => {
    setTouched(true);
    if (mappingValueError(kind, value) || !role) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await createMapping(token, {
        kind,
        value: kind === "everyone" ? "" : value,
        role_id: role,
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
      <DialogTitle id={titleId}>Nueva asignación automática</DialogTitle>
      <DialogContent>
        <div className="admin-field">
          <label htmlFor={kindId}>Tipo</label>
          <select
            id={kindId}
            className="admin-input"
            value={kind}
            disabled={busy}
            onChange={(event) => setKind(event.target.value as MappingKind)}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {MAPPING_KIND_LABELS[k]}
              </option>
            ))}
          </select>
        </div>
        {kind !== "everyone" && (
          <div className="admin-field">
            <label htmlFor={valueId}>
              {kind === "department" ? "Departamento" : "Inicio del puesto"}
            </label>
            <input
              id={valueId}
              className="admin-input"
              value={value}
              maxLength={200}
              list={kind === "department" ? listId : undefined}
              disabled={busy}
              aria-invalid={!!valueError}
              aria-describedby={valueHintId}
              onChange={(event) => setValue(event.target.value)}
            />
            {kind === "department" && (
              <datalist id={listId}>
                {(usersScope?.departments ?? []).map((department) => (
                  <option key={department} value={department} />
                ))}
              </datalist>
            )}
            <span
              id={valueHintId}
              className={
                valueError ? "admin-msg admin-msg--error" : "admin-field-hint"
              }
            >
              {valueError ??
                (kind === "department"
                  ? "Sin distinguir mayúsculas ni acentos."
                  : "Exacto, con mayúsculas y espacios: «GR -» no incluye «gr -».")}
            </span>
          </div>
        )}
        {fixedRole ? (
          <div className="admin-field">
            <span>Rol</span>
            <strong>{fixedRole.name}</strong>
          </div>
        ) : (
          <div className="admin-field">
            <label htmlFor={roleId}>Rol</label>
            <select
              id={roleId}
              className="admin-input"
              value={role}
              disabled={busy}
              aria-invalid={!!roleError}
              aria-describedby={roleError ? roleErrorId : undefined}
              onChange={(event) => setRole(event.target.value)}
            >
              <option value="">Elige un rol…</option>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.key})
                </option>
              ))}
            </select>
            {roleError && (
              <span id={roleErrorId} className="admin-msg admin-msg--error">
                {roleError}
              </span>
            )}
          </div>
        )}
        <p className="admin-muted">
          Ejemplo: Puesto que empieza por «GR -» → Administrador de
          departamento.
        </p>
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
