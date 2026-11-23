import { useEffect, useState } from "react";
import { getMyPermissions, type MyPermissions } from "./axios";

/** The signed-in user's permissions, fetched again whenever the token is renewed (so changes made by an
 * admin show up within the hour). null while loading or if the request failed: show nothing optional. */
export const useMyPermissions = (token: string) => {
  const [permissions, setPermissions] = useState<MyPermissions | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getMyPermissions(token)
      .then(({ data }) => {
        if (!cancelled) setPermissions(data);
      })
      .catch(() => {
        // Keep the last known permissions on a network error; the API checks every call anyway
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return permissions;
};

export const canAdmin = (permissions: MyPermissions | null, area: string) =>
  !!permissions?.admin_areas.includes(area);
