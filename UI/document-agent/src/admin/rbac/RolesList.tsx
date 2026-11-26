import { Button } from "@mui/material";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import type { Role } from "../../utils/api/rbac";
import { DataTable, type Column } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { NewRoleDialog } from "./NewRoleDialog";
import { roleMappingsSummary } from "./rbacLabels";
import { useMappings, useRoles } from "./useRbacData";

/** Spec §4.1 */
export const RolesList = () => {
  const roles = useRoles();
  const mappings = useMappings();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState({ open: false, key: 0 });

  const columns: Column<Role>[] = [
    {
      key: "name",
      header: "Nombre",
      render: (role) => (
        <div className="admin-rbac-name">
          <Link to={`/admin/rbac/roles/${role.id}`} className="admin-link">
            {role.name}
          </Link>
          {role.description && <small>{role.description}</small>}
        </div>
      ),
    },
    { key: "key", header: "Clave", render: (role) => <code>{role.key}</code> },
    {
      key: "manager",
      header: "Asignable por gerentes",
      render: (role) =>
        role.manager_assignable ? (
          <>
            <span aria-hidden="true">✅</span>
            <span className="admin-visually-hidden">Sí</span>
          </>
        ) : (
          <>
            <span aria-hidden="true">—</span>
            <span className="admin-visually-hidden">No</span>
          </>
        ),
    },
    {
      key: "auto",
      header: "Se asigna automáticamente a",
      render: (role) =>
        mappings.data ? roleMappingsSummary(mappings.data, role.id) : "…",
    },
    {
      key: "system",
      header: "",
      className: "admin-col-actions",
      render: (role) =>
        role.is_system ? (
          <StatusBadge tone="muted">🔒 Sistema</StatusBadge>
        ) : null,
    },
  ];

  return (
    <section className="admin-card">
      <div className="admin-card-head">
        <h2>Roles</h2>
        <Button
          variant="contained"
          size="small"
          onClick={() => setDialog((d) => ({ open: true, key: d.key + 1 }))}
        >
          + Nuevo rol
        </Button>
      </div>
      <p className="admin-muted">
        Un rol agrupa permisos. Se da a mano desde el detalle de un usuario, o
        automáticamente por departamento o puesto.
      </p>
      {mappings.error && (
        <p className="admin-msg admin-msg--warn">
          No se pudieron cargar las asignaciones automáticas: {mappings.error}
        </p>
      )}
      <DataTable
        caption="Roles"
        columns={columns}
        rows={roles.data}
        rowKey={(role) => role.id}
        error={roles.error}
        onRetry={roles.reload}
        emptyText="No hay roles."
      />
      <NewRoleDialog
        key={dialog.key}
        open={dialog.open}
        onCancel={() => setDialog((d) => ({ ...d, open: false }))}
        onCreated={(role) => navigate(`/admin/rbac/roles/${role.id}`)}
      />
    </section>
  );
};
