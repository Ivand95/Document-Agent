import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { useId, useState } from "react";
import { adminContainer } from "../adminContainer";
import { JsonField } from "../JsonField";
import { parseConstraints } from "./matrixModel";

interface ConstraintsDialogProps {
  title: string;
  constraints: Record<string, unknown>;
  readOnly: boolean;
  /** Saves; resolves to the error text (the dialog stays open), or null (the parent closes it) */
  onSave: (constraints: Record<string, unknown>) => Promise<string | null>;
  onClose: () => void;
}

/** "Avanzado": a rule's constraints as JSON. Mounted only while open, so it starts from the rule. */
export const ConstraintsDialog = ({
  title,
  constraints,
  readOnly,
  onSave,
  onClose,
}: ConstraintsDialogProps) => {
  const titleId = useId();
  const [text, setText] = useState(() =>
    Object.keys(constraints).length ? JSON.stringify(constraints, null, 2) : "",
  );
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const parsed = parseConstraints(text);

  const save = async () => {
    if (!parsed.ok) return;
    setBusy(true);
    setServerError(null);
    const failure = await onSave(parsed.value);
    setBusy(false);
    if (failure) setServerError(failure);
  };

  return (
    <Dialog
      open
      onClose={busy ? undefined : onClose}
      container={adminContainer}
      aria-labelledby={titleId}
      slotProps={{ paper: { className: "admin-dialog" } }}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle id={titleId}>Restricciones · {title}</DialogTitle>
      <DialogContent>
        <p className="admin-muted">
          Se guardan con la regla. Claves conocidas:{" "}
          <code>"scope": "departments"</code> (solo sus departamentos) y{" "}
          <code>"llm_budget_usd_month"</code> (presupuesto mensual en USD).
        </p>
        <JsonField
          label="Restricciones (JSON)"
          value={text}
          onChange={(value) => {
            setText(value);
            setServerError(null);
          }}
          error={parsed.ok ? serverError : parsed.error}
          hint="Vacío: sin restricciones."
          disabled={readOnly || busy}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {readOnly ? "Cerrar" : "Cancelar"}
        </Button>
        {!readOnly && (
          <Button
            variant="contained"
            onClick={() => void save()}
            disabled={busy || !parsed.ok}
          >
            {busy ? "Guardando…" : "Guardar"}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};
