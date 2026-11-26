import { MappingsTable } from "./MappingsTable";
import { useMappings, useRoles } from "./useRbacData";

/** /admin/rbac/mappings */
export const MappingsPage = () => {
  const roles = useRoles();
  const mappings = useMappings();
  return (
    <section className="admin-card">
      <h2>Asignación automática</h2>
      <p className="admin-muted">
        Da un rol a todos los usuarios de un departamento, a los que tienen un
        puesto que empieza por un texto, o a todos.
      </p>
      <MappingsTable
        mappings={mappings.data}
        roles={roles.data}
        error={mappings.error ?? roles.error}
        onRetry={() => {
          mappings.reload();
          roles.reload();
        }}
        onCreated={(mapping) => mappings.setData((list) => [...list, mapping])}
        onDeleted={(mappingId) =>
          mappings.setData((list) => list.filter((m) => m.id !== mappingId))
        }
      />
    </section>
  );
};
