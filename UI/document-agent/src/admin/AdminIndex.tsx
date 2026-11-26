import { Navigate } from "react-router";
import { useAdmin } from "./adminContext";
import { firstAreaPath } from "./areas";

/** /admin (and unknown /admin/* paths): go to the first area the user may open. AdminLayout only
 * renders its outlet when there is at least one, so the path always exists. */
export const AdminIndex = () => {
  const { permissions } = useAdmin();
  return <Navigate to={firstAreaPath(permissions.admin_areas) ?? "/chat"} replace />;
};
