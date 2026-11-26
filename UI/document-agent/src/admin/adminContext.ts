import { useOutletContext } from "react-router";
import type { MyPermissions } from "../utils/axios";
import type { UsersScope } from "../utils/api/users";

/** What AdminLayout hands to every area through <Outlet context>. */
export interface AdminContext {
  token: string;
  permissions: MyPermissions;
  /** Scope in the Usuarios area (null without that area, or until it loads) */
  usersScope: UsersScope | null;
  /** Reloads the pending-users badge and the scope (call after changing a user's status) */
  refreshSummary: () => void;
}

export const useAdmin = () => useOutletContext<AdminContext>();
