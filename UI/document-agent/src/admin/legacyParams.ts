import { useEffect } from "react";
import { useLocation, useSearchParams } from "react-router";

/** params without `names`, or null when none of them is there (nothing to clean). */
export const withoutParams = (params: URLSearchParams, names: readonly string[]): URLSearchParams | null => {
  if (!names.some((name) => params.has(name))) return null;
  const cleaned = new URLSearchParams(params);
  for (const name of names) cleaned.delete(name);
  return cleaned;
};

/**
 * Removes params that no longer belong in the URL (e.g. search text from an old bookmark, which pages now
 * keep in the history state) by replacing the current entry once. The values are ignored, not moved: the
 * other params and the current history state stay as they are.
 */
export const useDropUrlParams = (names: readonly string[]) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const { state } = useLocation();
  const cleaned = withoutParams(searchParams, names)?.toString() ?? null;

  useEffect(() => {
    // Navigation, not component state: replaces the history entry
    if (cleaned !== null) setSearchParams(new URLSearchParams(cleaned), { replace: true, state });
  }, [cleaned, setSearchParams, state]);
};
