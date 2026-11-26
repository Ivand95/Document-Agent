import { Button } from "@mui/material";
import type { UserDetail } from "../../utils/api/users";
import { useAdmin } from "../adminContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { RelativeTime } from "../components/RelativeTime";
import { formatDateTime } from "../format";
import { ACTION_LABELS, PROTECTED_LABELS } from "./userLabels";
import { useStatusChange } from "./useStatusChange";

export const UserStatusTab = ({ user, onChanged }: { user: UserDetail; onChanged: () => void }) => {
  const { token } = useAdmin();
  const statusChange = useStatusChange(token, onChanged);

  return (
    <section className="admin-card">
      <h2>Estado</h2>
      {user.actionable ? (
        <div className="admin-actions">
          {user.actions.map((action) => (
            <Button
              key={action}
              variant={action === "approve" ? "contained" : "outlined"}
              color={action === "disable" ? "error" : "primary"}
              disabled={statusChange.busyId !== null}
              onClick={() => statusChange.request(user, action)}
            >
              {ACTION_LABELS[action]}
            </Button>
          ))}
        </div>
      ) : (
        <p className="admin-muted">
          🔒 No puedes cambiar el estado de este usuario
          {user.protected ? ` (${PROTECTED_LABELS[user.protected]})` : ""}.
        </p>
      )}
      {statusChange.error && (
        <p className="admin-msg admin-msg--error" role="alert">
          {statusChange.error}
        </p>
      )}
      <dl className="admin-facts">
        <dt>Primer acceso</dt>
        <dd>{formatDateTime(user.first_login_at)}</dd>
        <dt>Último acceso</dt>
        <dd>
          <RelativeTime iso={user.last_login_at} />
        </dd>
        <dt>Último cambio</dt>
        <dd>{user.updated_at ? `${formatDateTime(user.updated_at)} · ${user.updated_by ?? "—"}` : "—"}</dd>
      </dl>
      <ConfirmDialog {...statusChange.confirmProps} />
    </section>
  );
};
