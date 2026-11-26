import { useId } from "react";
import "./components.css";

interface JsonFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Validation error while typing, or the backend's error */
  error?: string | null;
  hint?: string;
  disabled?: boolean;
  rows?: number;
}

/** A monospace text box for JSON. The parent validates (it knows the expected shape) and passes `error`. */
export const JsonField = ({ label, value, onChange, error = null, hint, disabled = false, rows = 8 }: JsonFieldProps) => {
  const id = useId();
  const hintId = useId();
  const errorId = useId();
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="admin-field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        className="admin-input admin-json"
        value={value}
        rows={rows}
        spellCheck={false}
        disabled={disabled}
        aria-invalid={!!error}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && (
        <span id={hintId} className="admin-field-hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={errorId} className="admin-msg admin-msg--error">
          {error}
        </span>
      )}
    </div>
  );
};
