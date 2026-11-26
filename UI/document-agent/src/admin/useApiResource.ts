import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage } from "../utils/apiError";

interface Loaded<T> {
  key: string;
  data: T | null;
  error: string | null;
}

/** Loads the data of `key` (e.g. the id on screen) and ignores answers that belong to an older key.
 * key null: nothing to load yet. `load` must change only when what it fetches changes (useCallback).
 * A failed reload keeps the previous data next to the error. `setData` replaces the loaded data
 * (after a change the API answered, or an optimistic update); it does nothing before the first load. */
export function useApiResource<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<Loaded<T> | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ key, data, error: null });
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setState((prev) => ({ key, data: prev?.key === key ? prev.data : null, error: apiErrorMessage(e) }));
      });
    return () => {
      controller.abort();
    };
  }, [key, load, reloadKey]);

  const reload = useCallback(() => setReloadKey((n) => n + 1), []);

  const setData = useCallback(
    (next: T | ((prev: T) => T)) =>
      setState((prev) =>
        prev && prev.key === key && prev.data !== null
          ? { ...prev, data: typeof next === "function" ? (next as (p: T) => T)(prev.data) : next }
          : prev,
      ),
    [key],
  );

  const current = key !== null && state?.key === key ? state : null;
  return { data: current?.data ?? null, error: current?.error ?? null, reload, setData };
}
