import { Button, Dialog, DialogActions, DialogContent, DialogTitle } from "@mui/material";
import { useId, type ReactNode } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  /** Red confirm button for actions that delete or take access away */
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Rendered inside the admin page (disablePortal) so it inherits the theme's CSS variables. */
export const ConfirmDialog = ({
  open,
  title,
  message,
  confirmLabel = "Confirmar",
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) => {
  const titleId = useId();
  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : onCancel}
      disablePortal
      aria-labelledby={titleId}
      slotProps={{ paper: { className: "admin-dialog" } }}
    >
      <DialogTitle id={titleId}>{title}</DialogTitle>
      <DialogContent>
        <div className="admin-dialog-text">{message}</div>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
        <Button
          variant="contained"
          color={destructive ? "error" : "primary"}
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? "Enviando…" : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
