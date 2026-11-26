import { useCallback } from "react";
import { listMappings, listRoles } from "../../utils/api/rbac";
import { useAdmin } from "../adminContext";
import { useApiResource } from "../useApiResource";

export const useRoles = () => {
  const { token } = useAdmin();
  const load = useCallback((signal: AbortSignal) => listRoles(token, signal).then(({ data }) => data.roles), [token]);
  return useApiResource("roles", load);
};

export const useMappings = () => {
  const { token } = useAdmin();
  const load = useCallback(
    (signal: AbortSignal) => listMappings(token, signal).then(({ data }) => data.mappings),
    [token],
  );
  return useApiResource("mappings", load);
};
