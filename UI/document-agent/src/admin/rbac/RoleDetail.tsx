import ArrowBack from "@mui/icons-material/ArrowBack";
import { Button, CircularProgress, Tab, Tabs } from "@mui/material";
import { Link, useParams, useSearchParams } from "react-router";
import { useAdmin } from "../adminContext";
import { PermissionMatrix } from "../components/permissionMatrix/PermissionMatrix";
import {
  isSuperAdmin,
  roleLock,
} from "../components/permissionMatrix/matrixModel";
import { MappingsTable } from "./MappingsTable";
import { RoleHeader } from "./RoleHeader";
import { useMappings, useRoles } from "./useRbacData";

type RoleTab = "permissions" | "mappings";

/** /admin/rbac/roles/:id (spec §4.2) */
export const RoleDetail = () => {
  const { id = "" } = useParams();
  const { permissions } = useAdmin();
  const superAdmin = isSuperAdmin(permissions.roles);
  const roles = useRoles();
  const mappings = useMappings();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: RoleTab =
    searchParams.get("tab") === "mappings" ? "mappings" : "permissions";
  const role = roles.data?.find((r) => r.id === id) ?? null;

  const back = (
    <Link to="/admin/rbac/roles" className="admin-back-link">
      <ArrowBack fontSize="small" /> Roles
    </Link>
  );

  if (!role) {
    return (
      <section className="admin-card">
        {back}
        {roles.error ? (
          <p className="admin-msg admin-msg--error" role="alert">
            {roles.error}{" "}
            <Button size="small" onClick={roles.reload}>
              Reintentar
            </Button>
          </p>
        ) : roles.data ? (
          <p className="admin-msg admin-msg--error">Rol no encontrado.</p>
        ) : (
          <div className="admin-center">
            <CircularProgress size={24} />
          </div>
        )}
      </section>
    );
  }

  const lock = roleLock(role, superAdmin);

  return (
    <>
      <section className="admin-card">
        {back}
        <RoleHeader
          key={role.id}
          role={role}
          lock={lock}
          onChanged={(next) =>
            roles.setData((list) =>
              list.map((r) => (r.id === next.id ? next : r)),
            )
          }
        />
        <Tabs
          value={tab}
          onChange={(_, value: RoleTab) =>
            setSearchParams(value === "permissions" ? {} : { tab: value }, {
              replace: true,
            })
          }
          aria-label="Secciones del rol"
          className="admin-tabs"
        >
          <Tab value="permissions" label="Permisos" />
          <Tab value="mappings" label="Asignación automática" />
        </Tabs>
      </section>
      {tab === "permissions" && (
        <section className="admin-card">
          <h2>Permisos</h2>
          <p className="admin-muted">
            Lo que este rol permite o deniega a quien lo tenga. «Denegar» gana a
            cualquier «Permitir» de otro rol; un permiso directo del usuario
            gana a los de sus roles.
          </p>
          <PermissionMatrix
            key={role.id}
            subject={{ type: "role", id: role.id }}
            readOnlyReason={lock}
          />
        </section>
      )}
      {tab === "mappings" && (
        <section className="admin-card">
          <h2>Asignación automática</h2>
          <MappingsTable
            mappings={mappings.data}
            roles={roles.data}
            error={mappings.error}
            onRetry={mappings.reload}
            role={role}
            lock={lock}
            onCreated={(mapping) =>
              mappings.setData((list) => [...list, mapping])
            }
            onDeleted={(mappingId) =>
              mappings.setData((list) => list.filter((m) => m.id !== mappingId))
            }
          />
        </section>
      )}
    </>
  );
};
