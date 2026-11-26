import { useCallback, useEffect, useState } from "react";
import { getMyPermissions, type MyPermissions } from "./axios";
import { onPermissionsStale } from "./permissionEvents";
import { createTrailingThrottle } from "./throttle";

/** At most one reload every 15 s from focus or 403s; a trigger inside the window is deferred to its end,
 * not dropped (also stops a 403 on /me/permissions from looping). */
export const PERMISSIONS_MIN_REFETCH_MS = 15_000;

/** The signed-in user's permissions. Reloaded when the token is renewed, when the window regains focus
 * and when any API call is refused (403), so admin changes show up within seconds.
 * `permissions` is null while loading or if the first request failed; `failed` tells the two apart.
 * Later failures keep the last known permissions: the API checks every call anyway. */
export const useMyPermissions = (token: string) => {
  const [permissions, setPermissions] = useState<MyPermissions | null>(null);
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const refetch = useCallback(() => {
    setFailed(false); // lets the UI show the spinner again while a retry is in flight
    setReloadKey((key) => key + 1);
  }, []);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    getMyPermissions(token, controller.signal)
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setPermissions(data);
        setFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => {
      controller.abort();
    };
  }, [token, reloadKey]);

  useEffect(() => {
    const throttle = createTrailingThrottle(PERMISSIONS_MIN_REFETCH_MS, refetch);
    throttle.markRan(); // the first load has just started
    const maybeRefetch = () => throttle.call();
    const onVisibility = () => {
      if (document.visibilityState === "visible") maybeRefetch();
    };
    document.addEventListener("visibilitychange", onVisibility);
    const stopListening = onPermissionsStale(maybeRefetch);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stopListening();
      throttle.cancel();
    };
  }, [refetch]);

  return { permissions, failed, refetch };
};
