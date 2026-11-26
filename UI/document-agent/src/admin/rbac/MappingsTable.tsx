import { Button } from "@mui/material";
import { useState } from "react";
import { Link } from "react-router";
import {
  deleteMapping,
  type Role,
  type RoleMapping,
} from "../../utils/api/rbac";
import { apiErrorMessage } from "../../utils/apiError";
import { useAdmin } from "../adminContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { isSuperAdmin } from "../components/permissionMatrix/matrixModel";
import { NewMappingDialog } from "./NewMappingDialog";
import { MAPPING_KIND_LABELS, mappingText } from "./rbacLabels";

interface MappingsTableProps {
  mappings: RoleMapping[] | null;
  roles: Role[] | null;
  error: string | null;
  onRetry: () => void;
  /** Only this role's mappings, without the role column (role detail tab) */
  role?: Role;
  /** Why the role can't be changed (super_admin for a non super admin); hides add and remove */
  lock?: string | null;
  onCreated: (mapping: RoleMapping) => void;
  onDeleted: (mappingId: string) => void;
}

/** Spec §4.4: department / position prefix / everyone → role. */
export const MappingsTable = ({
  mappings,
  roles,
  error,
  onRetry,
  role,
  lock = null,
  onCreated,
  onDeleted,
}: MappingsTableProps) => {
  const { token, permissions } = useAdmin();
  const superAdmin = isSuperAdmin(permissions.roles);
  const [dialog, setDialog] = useState({ open: false, key: 0 });
  const [removing, setRemoving] = useState<RoleMapping | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const roleById = new Map((roles ?? []).map((r) => [r.id, r]));
  const lockedRole = (roleId: string) =>
    roleById.get(roleId)?.key === "super_admin" && !superAdmin;
  const rows =
    mappings && roles
      ? mappings.filter((m) => !role || m.role_id === role.id)
      : null;
  const choosable = (roles ?? []).filter(
    (r) => superAdmin || r.key !== "super_admin",
  );

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteMapping(token, removing.id);
      onDeleted(removing.id);
    } catch (e) {
      setActionError(apiErrorMessage(e));
    } finally {
      setBusy(false);
      setRemoving(null);
    }
  };

  const columns: Column<RoleMapping>[] = [
    { key: "kind", header: "Tipo", render: (m) => MAPPING_KIND_LABELS[m.kind] },
    {
      key: "value",
      header: "Valor",
      render: (m) => (m.kind === "everyone" ? "—" : m.value),
    },
    ...(role
      ? []
      : [
          {
            key: "role",
            header: "Rol",
            render: (m: RoleMapping) => {
              const target = roleById.get(m.role_id);
              return target ? (
                <Link
                  to={`/admin/rbac/roles/${target.id}`}
                  className="admin-link"
                >
                  {target.name}
                </Link>
              ) : (
                "—"
              );
            },
          },
        ]),
    {
      key: "actions",
      header: "",
      className: "admin-col-actions",
      render: (m) =>
        lock || lockedRole(m.role_id) ? null : (
          <Button size="small" color="error" onClick={() => setRemoving(m)}>
            Quitar
          </Button>
        ),
    },
  ];

  const removingRole = removing
    ? (roleById.get(removing.role_id)?.name ?? "?")
    : "";

  return (
    <>
      {!lock && roles && (
        <div className="admin-actions">
          <Button
            variant="contained"
            size="small"
            onClick={() => setDialog((d) => ({ open: true, key: d.key + 1 }))}
          >
            + Nueva asignación
          </Button>
        </div>
      )}
      {actionError && (
        <p className="admin-msg admin-msg--error" role="alert">
          {actionError}
        </p>
      )}
      <DataTable
        caption="Asignaciones automáticas"
        columns={columns}
        rows={rows}
        rowKey={(m) => m.id}
        error={error}
        onRetry={onRetry}
        emptyText={
          role
            ? "Este rol no se asigna automáticamente."
            : "No hay asignaciones automáticas."
        }
      />
      <p className="admin-muted admin-rbac-help">
        Se aplican según el departamento y el puesto del último inicio de sesión
        en Microsoft 365. Ejemplo: Puesto que empieza por «GR -» → Administrador
        de departamento.
      </p>
      {roles && (
        <NewMappingDialog
          key={dialog.key}
          open={dialog.open}
          roles={choosable}
          fixedRole={role}
          onCancel={() => setDialog((d) => ({ ...d, open: false }))}
          onCreated={(mapping) => {
            onCreated(mapping);
            setDialog((d) => ({ ...d, open: false }));
          }}
        />
      )}
      <ConfirmDialog
        open={removing !== null}
        title="¿Quitar asignación automática?"
        message={
          removing
            ? `«${mappingText(removing)}» → ${removingRole}. Quien tenga el rol solo por esta asignación lo perderá en menos de 30 segundos.`
            : ""
        }
        confirmLabel="Quitar"
        destructive
        busy={busy}
        onCancel={() => setRemoving(null)}
        onConfirm={() => void remove()}
      />
    </>
  );
};
