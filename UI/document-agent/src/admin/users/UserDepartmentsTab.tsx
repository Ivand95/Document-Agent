import { Button } from "@mui/material";
import { useId, useState } from "react";
import {
  addAdminDepartment,
  removeAdminDepartment,
  type AdminDepartmentsView,
} from "../../utils/api/users";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../format";

const plain = (text: string) =>
  text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .trim();

interface UserDepartmentsTabProps {
  view: AdminDepartmentsView;
  onChange: (view: AdminDepartmentsView) => void;
  /** Why the admin can't change this user's departments; null: editable */
  lock: string | null;
}

/** Spec §3.5: the departments a department admin manages (their own + the extra ones). */
export const UserDepartmentsTab = ({
  view,
  onChange,
  lock,
}: UserDepartmentsTabProps) => {
  const { token, usersScope } = useAdmin();
  const inputId = useId();
  const listId = useId();
  const [draft, setDraft] = useState("");
  /** The department being removed, "add", or null */
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const taken = new Set(
    [view.department ?? "", ...view.extra.map((d) => d.department)]
      .filter(Boolean)
      .map(plain),
  );
  const options = (usersScope?.departments ?? []).filter(
    (department) => !taken.has(plain(department)),
  );

  const add = async () => {
    const department = draft.trim();
    if (!department) {
      setError("Escribe o elige un departamento.");
      return;
    }
    setBusy("add");
    setError(null);
    try {
      const { data } = await addAdminDepartment(
        token,
        view.user_id,
        department,
      );
      onChange(data);
      setDraft("");
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (department: string) => {
    setBusy(department);
    setError(null);
    try {
      const { data } = await removeAdminDepartment(
        token,
        view.user_id,
        department,
      );
      onChange(data);
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="admin-card">
      <h2>Departamentos administrados</h2>
      <p className="admin-muted">
        Este gerente gestiona los usuarios de estos departamentos.
      </p>
      {lock && <p className="admin-msg admin-msg--warn">🔒 {lock}</p>}
      <ul className="admin-departments">
        <li>
          <span>{view.department ?? "Sin departamento"}</span>
          <StatusBadge tone="muted">Por perfil de M365</StatusBadge>
        </li>
        {view.extra.map((d) => (
          <li key={d.department}>
            <span>{d.department}</span>
            <small className="admin-muted">
              Añadido por {d.created_by}, {formatDateTime(d.created_at)}
            </small>
            {!lock && (
              <Button
                size="small"
                color="error"
                disabled={busy !== null}
                onClick={() => void remove(d.department)}
              >
                {busy === d.department ? "Quitando…" : "Quitar"}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {!lock && (
        <div className="admin-field">
          <label htmlFor={inputId}>Añadir departamento</label>
          <div className="admin-inline">
            <input
              id={inputId}
              className="admin-input"
              list={listId}
              value={draft}
              placeholder="Nombre del departamento"
              disabled={busy !== null}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void add();
              }}
            />
            <datalist id={listId}>
              {options.map((department) => (
                <option key={department} value={department} />
              ))}
            </datalist>
            <Button
              variant="contained"
              size="small"
              disabled={busy !== null}
              onClick={() => void add()}
            >
              {busy === "add" ? "Añadiendo…" : "Añadir"}
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p className="admin-msg admin-msg--error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
};
