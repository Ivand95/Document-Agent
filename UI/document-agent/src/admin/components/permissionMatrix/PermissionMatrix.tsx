import { Button, CircularProgress } from "@mui/material";
import { useCallback, useId, useState } from "react";
import {
  deletePermission,
  listPermissions,
  setPermission,
  type ResourceType,
  type Subject,
} from "../../../utils/api/rbac";
import { apiErrorMessage } from "../../../utils/apiError";
import { useAdmin } from "../../adminContext";
import { useApiResource } from "../../useApiResource";
import "../components.css";
import { ConstraintsDialog } from "./ConstraintsDialog";
import { MatrixGroupTable } from "./MatrixGroupTable";
import {
  applyChange,
  buildMatrix,
  changeFor,
  constraintsChange,
  isSuperAdmin,
  restoreRule,
  ruleKey,
  settleRule,
  startsCollapsed,
  type CellValue,
  type MatrixChange,
  type MatrixGroupDef,
  type MatrixRow,
} from "./matrixModel";
import { useCatalog } from "./useCatalog";

interface PermissionMatrixProps {
  subject: Subject;
  /** Why the admin can't edit this subject (own user, super admin…); null: editable */
  readOnlyReason: string | null;
}

/** Spec §4.3, subject mode: one row per resource, a — / Permitir / Denegar control per row. Changes show at
 * once and are undone if the API refuses them. */
export const PermissionMatrix = ({
  subject,
  readOnlyReason,
}: PermissionMatrixProps) => {
  const { type, id } = subject;
  const { token, permissions } = useAdmin();
  const superAdmin = isSuperAdmin(permissions.roles);
  const baseId = useId();
  const catalog = useCatalog();
  const loadRules = useCallback(
    (signal: AbortSignal) =>
      listPermissions(token, { type, id }, signal).then(
        ({ data }) => data.permissions,
      ),
    [token, type, id],
  );
  const rules = useApiResource(`${type}:${id}`, loadRules);
  const [search, setSearch] = useState("");
  const [folded, setFolded] = useState<Partial<Record<ResourceType, boolean>>>(
    {},
  );
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState<{
    group: MatrixGroupDef;
    row: MatrixRow;
  } | null>(null);
  const readOnly = readOnlyReason !== null;

  /** Applies the change at once, calls the API, and undoes this cell if it fails. Resolves to the error. */
  const run = async (
    group: MatrixGroupDef,
    row: MatrixRow,
    change: MatrixChange,
  ): Promise<string | null> => {
    if (change.kind === "none") return null;
    const key = ruleKey(group.type, row.id, group.action);
    const before = row.rule;
    setPending((current) => new Set(current).add(key));
    rules.setData((current) => applyChange(current, change));
    try {
      if (change.kind === "delete") {
        await deletePermission(token, change.rule.id);
      } else {
        const { data } = await setPermission(token, change.body);
        rules.setData((current) => settleRule(current, data));
      }
      return null;
    } catch (e) {
      rules.setData((current) =>
        restoreRule(current, group.type, row.id, group.action, before),
      );
      return apiErrorMessage(e);
    } finally {
      setPending((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
    }
  };

  const setValue = (
    group: MatrixGroupDef,
    row: MatrixRow,
    value: CellValue,
  ) => {
    setError(null);
    void run(group, row, changeFor({ type, id }, group, row, value)).then(
      (failure) => {
        if (failure) setError(`${row.label}: ${failure}`);
      },
    );
  };

  const loadError = catalog.error ?? rules.error;
  if (!catalog.data || !rules.data) {
    return loadError ? (
      <p className="admin-msg admin-msg--error" role="alert">
        {loadError}{" "}
        <Button
          size="small"
          onClick={() => {
            catalog.reload();
            rules.reload();
          }}
        >
          Reintentar
        </Button>
      </p>
    ) : (
      <div className="admin-center">
        <CircularProgress size={24} />
      </div>
    );
  }

  const searching = search.trim() !== "";
  const groups = buildMatrix(catalog.data, rules.data, {
    superAdmin,
    search,
  }).filter((group) => (searching ? group.rows.length > 0 : group.total > 0));

  return (
    <div className="admin-matrix">
      {readOnlyReason && (
        <p className="admin-msg admin-msg--warn">🔒 {readOnlyReason}</p>
      )}
      <input
        type="search"
        className="admin-input admin-matrix-search"
        placeholder="Buscar recurso…"
        aria-label="Buscar recurso"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {error && (
        <p className="admin-msg admin-msg--error" role="alert">
          {error}
        </p>
      )}
      {groups.length === 0 && (
        <p className="admin-muted">Ningún recurso coincide con la búsqueda.</p>
      )}
      {groups.map((group) => {
        // A search opens every group that has matches
        const closed =
          !searching && (folded[group.type] ?? startsCollapsed(group));
        const panelId = `${baseId}-${group.type}`;
        return (
          <section key={group.type} className="admin-matrix-group">
            <h3 className="admin-matrix-group-title">
              <button
                type="button"
                className="admin-matrix-group-head"
                aria-expanded={!closed}
                aria-controls={closed ? undefined : panelId}
                onClick={() => {
                  if (!searching)
                    setFolded((current) => ({
                      ...current,
                      [group.type]: !closed,
                    }));
                }}
              >
                <span aria-hidden="true">{closed ? "▸" : "▾"}</span>
                {group.label}
                <span className="admin-muted">({group.total})</span>
              </button>
            </h3>
            {!closed && (
              <div id={panelId} className="admin-matrix-body">
                <MatrixGroupTable
                  group={group}
                  readOnly={readOnly}
                  superAdmin={superAdmin}
                  pending={pending}
                  onValue={(row, value) => setValue(group, row, value)}
                  onConstraints={(row, constraints) => {
                    setError(null);
                    return run(
                      group,
                      row,
                      constraintsChange({ type, id }, group, row, constraints),
                    ).then((failure) => {
                      if (failure) setError(`${row.label}: ${failure}`);
                      return failure;
                    });
                  }}
                  onAdvanced={(row) => setAdvanced({ group, row })}
                />
              </div>
            )}
          </section>
        );
      })}
      {advanced && (
        <ConstraintsDialog
          key={`${advanced.group.type}:${advanced.row.id}`}
          title={advanced.row.label}
          constraints={advanced.row.rule?.constraints ?? {}}
          readOnly={readOnly || (advanced.row.wildcard && !superAdmin)}
          onClose={() => setAdvanced(null)}
          onSave={async (constraints) => {
            const failure = await run(
              advanced.group,
              advanced.row,
              constraintsChange(
                { type, id },
                advanced.group,
                advanced.row,
                constraints,
              ),
            );
            if (!failure) setAdvanced(null);
            return failure;
          }}
        />
      )}
    </div>
  );
};
