import type { UserDetail } from "../../utils/api/users";
import { PermissionMatrix } from "../components/permissionMatrix/PermissionMatrix";
import { userPermissionsLock } from "../components/permissionMatrix/matrixModel";

/** Direct permissions: exceptions for one user (spec §3.2). */
export const UserPermissionsTab = ({ user }: { user: UserDetail }) => (
  <section className="admin-card">
    <h2>Permisos directos</h2>
    <p className="admin-muted">
      Excepciones para este usuario. Un permiso directo gana a lo que digan sus
      roles sobre el mismo recurso: «Denegar» le quita el acceso aunque un rol
      se lo dé.
    </p>
    <PermissionMatrix
      key={user.id}
      subject={{ type: "user", id: user.id }}
      readOnlyReason={userPermissionsLock(user.protected)}
    />
  </section>
);
