import type { ReactNode } from "react";
import { Link } from "react-router";
import { useAdmin } from "../adminContext";
import { firstAreaPath, visibleAreas, type AdminArea } from "../areas";

/** Shows the area only if the user has it; otherwise a "no permission" message with a way out. */
export const RequireArea = ({ area, children }: { area: AdminArea; children: ReactNode }) => {
  const { permissions } = useAdmin();
  if (visibleAreas(permissions.admin_areas).some((a) => a.id === area)) return <>{children}</>;
  const first = firstAreaPath(permissions.admin_areas);
  return (
    <section className="admin-card">
      <p className="admin-msg admin-msg--error">No tienes permiso para esta sección.</p>
      {first && (
        <p className="admin-muted">
          <Link to={first} className="admin-link">
            Ir a una sección permitida
          </Link>
        </p>
      )}
    </section>
  );
};
