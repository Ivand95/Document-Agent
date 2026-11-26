import { useId, useState } from "react";
import { budgetText, withBudget } from "./matrixModel";

interface BudgetFieldProps {
  resourceLabel: string;
  constraints: Record<string, unknown>;
  disabled: boolean;
  /** Saves the new constraints; resolves to the error text, or null */
  onSave: (constraints: Record<string, unknown>) => Promise<string | null>;
}

/** "Presupuesto mensual (USD)" of an allowed model. Saves on blur or Enter; empty means no limit.
 * The parent remounts it (key) when the saved budget changes, so the draft starts from it. */
export const BudgetField = ({
  resourceLabel,
  constraints,
  disabled,
  onSave,
}: BudgetFieldProps) => {
  const id = useId();
  const errorId = useId();
  const saved = budgetText(constraints);
  const [draft, setDraft] = useState(saved);
  const [error, setError] = useState<string | null>(null);

  const commit = async () => {
    const next = withBudget(constraints, draft);
    if (!next.ok) {
      setError(next.error);
      return;
    }
    setError(null);
    if (budgetText(next.constraints) === saved) {
      setDraft(saved);
      return;
    }
    const failure = await onSave(next.constraints);
    if (failure) setError(failure);
  };

  return (
    <span className="admin-matrix-budget">
      <label htmlFor={id}>
        Presupuesto mensual (USD)
        <span className="admin-visually-hidden"> de {resourceLabel}</span>
      </label>
      <input
        id={id}
        className="admin-input"
        inputMode="decimal"
        placeholder="Sin límite"
        value={draft}
        disabled={disabled}
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        onChange={(event) => {
          setDraft(event.target.value);
          setError(null);
        }}
        onBlur={() => void commit()}
        onKeyDown={(event) => {
          // Blur saves: Enter only blurs, so the value isn't sent twice
          if (event.key === "Enter") event.currentTarget.blur();
        }}
      />
      {error && (
        <span id={errorId} className="admin-msg admin-msg--error">
          {error}
        </span>
      )}
    </span>
  );
};
