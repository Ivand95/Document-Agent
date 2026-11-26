import {
  Button,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import { useCallback, useState } from "react";
import { getEffective } from "../../utils/api/rbac";
import { useAdmin } from "../adminContext";
import { useCatalog } from "../components/permissionMatrix/useCatalog";
import { StatusBadge } from "../components/StatusBadge";
import { effectiveGroups } from "../rbac/effective";
import { useApiResource } from "../useApiResource";
import { sourceLabel } from "./userLabels";

/** Spec §3.4: what the user can use right now and why. Read-only. */
export const UserEffectiveTab = ({ userId }: { userId: string }) => {
  const { token } = useAdmin();
  const catalog = useCatalog();
  const load = useCallback(
    (signal: AbortSignal) =>
      getEffective(token, userId, signal).then(({ data }) => data),
    [token, userId],
  );
  const effective = useApiResource(userId, load);
  const [onlyAllowed, setOnlyAllowed] = useState(true);
  const data = effective.data;
  const groups = data ? effectiveGroups(data, catalog.data, onlyAllowed) : [];

  return (
    <section className="admin-card">
      <div className="admin-card-head">
        <h2>Permisos efectivos</h2>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={onlyAllowed ? "allowed" : "all"}
          onChange={(_, value: string | null) => {
            if (value) setOnlyAllowed(value === "allowed");
          }}
          aria-label="Qué recursos mostrar"
          className="admin-matrix-toggle"
        >
          <ToggleButton value="allowed">Solo permitidos</ToggleButton>
          <ToggleButton value="all">Todos</ToggleButton>
        </ToggleButtonGroup>
      </div>
      <p className="admin-muted">
        Lo que el usuario puede usar ahora mismo y por qué. Solo lectura.
      </p>
      {effective.error && (
        <p className="admin-msg admin-msg--error" role="alert">
          {effective.error}{" "}
          <Button size="small" onClick={effective.reload}>
            Reintentar
          </Button>
        </p>
      )}
      {!data ? (
        !effective.error && (
          <div className="admin-center">
            <CircularProgress size={24} />
          </div>
        )
      ) : (
        <>
          {data.status !== "active" && (
            <p className="admin-msg admin-msg--warn">
              El usuario no está activo: no tiene ningún permiso.
            </p>
          )}
          <h3>Roles</h3>
          {data.roles.length ? (
            <ul className="admin-effective-list">
              {data.roles.map((role) => (
                <li key={role.id}>
                  <strong>{role.name}</strong> <code>{role.key}</code> ·{" "}
                  {role.sources.map(sourceLabel).join(", ")}
                </li>
              ))}
            </ul>
          ) : (
            <p className="admin-muted">Sin roles.</p>
          )}
          {groups.length === 0 && (
            <p className="admin-muted">
              {onlyAllowed ? "No tiene ningún permiso." : "No hay recursos."}
            </p>
          )}
          {groups.map((group) => (
            <div key={group.type}>
              <h3>{group.label}</h3>
              <div className="admin-table-wrap admin-table-wrap--full">
                <table className="admin-table">
                  <caption className="admin-visually-hidden">
                    {group.label}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Recurso</th>
                      <th scope="col">Acceso</th>
                      <th scope="col">Motivo</th>
                      <th scope="col">Reglas</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.items.map((item) => (
                      <tr key={item.id}>
                        <td>{item.label}</td>
                        <td>
                          {item.allowed ? (
                            <StatusBadge tone="ok">✅ Sí</StatusBadge>
                          ) : (
                            <StatusBadge tone="error">❌ No</StatusBadge>
                          )}
                        </td>
                        <td>{item.reason}</td>
                        <td>
                          {item.rules.length + item.constraints.length > 0 ? (
                            <ul className="admin-effective-list">
                              {[...item.rules, ...item.constraints].map(
                                (line, index) => (
                                  <li key={index}>{line}</li>
                                ),
                              )}
                            </ul>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  );
};
