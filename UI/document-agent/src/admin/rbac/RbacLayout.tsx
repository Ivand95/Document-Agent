import { CircularProgress } from "@mui/material";
import { Suspense } from "react";
import { NavLink, Outlet } from "react-router";
import { useAdmin } from "../adminContext";
import "../components/components.css";
import "./rbac.css";

/** Roles y permisos: sub-navigation above the area's screens (spec §4). */
export const RbacLayout = () => {
  const context = useAdmin();
  return (
    <>
      <nav className="admin-subnav" aria-label="Roles y permisos">
        <NavLink to="/admin/rbac/roles">Roles</NavLink>
        <NavLink to="/admin/rbac/mappings">Asignación automática</NavLink>
      </nav>
      <Suspense
        fallback={
          <div className="admin-center">
            <CircularProgress size={24} />
          </div>
        }
      >
        <Outlet context={context} />
      </Suspense>
    </>
  );
};
