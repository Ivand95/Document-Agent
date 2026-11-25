import { useCallback, useEffect, useState } from "react";
import { getMyPermissions, type MyPermissions } from "./axios";
import { onPermissionsStale } from "./permissionEvents";
import { createThrottle } from "./throttle";

/** At most one reload every 15 s from focus or 403s (also stops a 403 on /me/permissions from looping). */
export const PERMISSIONS_MIN_REFETCH_MS = 15_000;

/** The signed-in user's permissions. Reloaded when the token is renewed, when the window regains focus
 * and when any API call is refused (403), so admin changes show up within seconds.
 * `permissions` is null while loading or if the first request failed; `failed` tells the two apart.
 * Later failures keep the last known permissions: the API checks every call anyway. */
export const useMyPermissions = (token: string) => {
  const [permissions, setPermissions] = useState<MyPermissions | null>(null);
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const refetch = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getMyPermissions(token)
      .then(({ data }) => {
        if (cancelled) return;
        setPermissions(data);
        setFailed(false);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token, reloadKey]);

  useEffect(() => {
    const allow = createThrottle(PERMISSIONS_MIN_REFETCH_MS);
    allow(Date.now()); // the first load has just started
    const maybeRefetch = () => {
      if (allow(Date.now())) refetch();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") maybeRefetch();
    };
    document.addEventListener("visibilitychange", onVisibility);
    const stopListening = onPermissionsStale(maybeRefetch);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stopListening();
    };
  }, [refetch]);

  return { permissions, failed, refetch };
};

export const canAdmin = (permissions: MyPermissions | null, area: string) =>
  !!permissions?.admin_areas.includes(area);
