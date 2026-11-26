import { useCallback } from "react";
import { getCatalog } from "../../../utils/api/rbac";
import { useAdmin } from "../../adminContext";
import { useApiResource } from "../../useApiResource";

/** GET /admin/rbac/catalog: the resources the matrix can name, with their labels. */
export const useCatalog = () => {
  const { token } = useAdmin();
  const load = useCallback((signal: AbortSignal) => getCatalog(token, signal).then(({ data }) => data), [token]);
  return useApiResource("catalog", load);
};
