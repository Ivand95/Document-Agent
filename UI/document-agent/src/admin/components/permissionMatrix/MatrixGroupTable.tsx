import MoreHoriz from "@mui/icons-material/MoreHoriz";
import { useId } from "react";
import { IconButton, ToggleButton, ToggleButtonGroup } from "@mui/material";
import { StatusBadge } from "../StatusBadge";
import { BudgetField } from "./BudgetField";
import {
  budgetText,
  constraintBadges,
  ruleKey,
  type CellValue,
  type MatrixGroup,
  type MatrixRow,
} from "./matrixModel";

interface MatrixGroupTableProps {
  group: MatrixGroup;
  readOnly: boolean;
  superAdmin: boolean;
  /** ruleKey()s with a call in flight */
  pending: ReadonlySet<string>;
  onValue: (row: MatrixRow, value: CellValue) => void;
  onConstraints: (
    row: MatrixRow,
    constraints: Record<string, unknown>,
  ) => Promise<string | null>;
  onAdvanced: (row: MatrixRow) => void;
}

export const MatrixGroupTable = ({
  group,
  readOnly,
  superAdmin,
  pending,
  onValue,
  onConstraints,
  onAdvanced,
}: MatrixGroupTableProps) => {
  const denyHintId = useId();
  return (
    <>
      <span id={denyHintId} className="admin-visually-hidden">
        Anula cualquier permiso de otro rol
      </span>
      <table className="admin-table admin-matrix-table">
        <caption className="admin-visually-hidden">{group.label}</caption>
        <thead>
          <tr>
            <th scope="col">Recurso</th>
            <th scope="col">Permiso</th>
            <th scope="col" className="admin-col-actions">
              <span className="admin-visually-hidden">Más opciones</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {group.rows.map((row) => {
            const busy = pending.has(ruleKey(group.type, row.id, group.action));
            const locked = readOnly || (row.wildcard && !superAdmin);
            const disabledWhileBusy = locked || busy;
            return (
              <tr key={row.id}>
                <td>
                  <div className="admin-matrix-resource">
                    <span>{row.label}</span>
                    {!row.wildcard && row.label !== row.id && (
                      <code>{row.id}</code>
                    )}
                    {row.unknown && (
                      <StatusBadge tone="muted">Ya no existe</StatusBadge>
                    )}
                    {row.rule &&
                      constraintBadges(row.rule.constraints).map((badge) => (
                        <span key={badge} className="admin-matrix-badge">
                          {badge}
                        </span>
                      ))}
                  </div>
                </td>
                <td>
                  <div className="admin-matrix-cell">
                    <ToggleButtonGroup
                      exclusive
                      size="small"
                      value={row.value}
                      disabled={locked}
                      aria-disabled={busy || undefined}
                      aria-busy={busy || undefined}
                      aria-label={`Permiso sobre ${row.label}`}
                      className="admin-matrix-toggle"
                      onChange={(_, value: CellValue | null) => {
                        if (value && !busy) onValue(row, value);
                      }}
                    >
                      <ToggleButton value="none" aria-label="Sin regla">
                        —
                      </ToggleButton>
                      <ToggleButton
                        value="allow"
                        className="admin-matrix-allow"
                      >
                        Permitir
                      </ToggleButton>
                      <ToggleButton
                        value="deny"
                        className="admin-matrix-deny"
                        title="Anula cualquier permiso de otro rol"
                        aria-describedby={denyHintId}
                      >
                        Denegar
                      </ToggleButton>
                    </ToggleButtonGroup>
                    {group.type === "model" &&
                      row.value === "allow" &&
                      row.rule && (
                        <BudgetField
                          key={`${row.id}:${budgetText(row.rule.constraints)}`}
                          resourceLabel={row.label}
                          constraints={row.rule.constraints}
                          disabled={disabledWhileBusy}
                          onSave={(constraints) =>
                            onConstraints(row, constraints)
                          }
                        />
                      )}
                  </div>
                </td>
                <td className="admin-col-actions">
                  <IconButton
                    size="small"
                    title="Avanzado"
                    aria-label={`Restricciones de ${row.label}`}
                    disabled={!row.rule || busy}
                    onClick={() => onAdvanced(row)}
                  >
                    <MoreHoriz fontSize="small" />
                  </IconButton>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
};
