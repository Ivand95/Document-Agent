import Close from "@mui/icons-material/Close";
import { IconButton } from "@mui/material";
import { useState } from "react";
import { displayName } from "../users/userLabels";
import { CATEGORIES, resourceTypeLabel, type CategoryId } from "./auditLabels";
import { RANGES, customRangeInvalid, isValidDay, type AuditSearch, type RangeId } from "./auditSearch";
import { UserPicker } from "./UserPicker";

const FilterChip = ({ label, onRemove }: { label: string; onRemove: () => void }) => (
  <span className="admin-chip">
    {label}
    <IconButton
      size="small"
      aria-label={`Quitar filtro ${label}`}
      onClick={onRemove}
      sx={{ color: "var(--chat-text-muted)", p: "2px" }}
    >
      <Close fontSize="inherit" />
    </IconButton>
  </span>
);

/** A day field that only reports complete days: while a year is being typed the input says "0002-11-26",
 * which would otherwise reach the URL, be rejected there and clear the field mid-typing. */
const DayInput = ({
  label,
  value,
  min,
  max,
  onCommit,
}: {
  label: string;
  value: string;
  min?: string;
  max?: string;
  onCommit: (day: string) => void;
}) => {
  const [draft, setDraft] = useState(value);
  const [committed, setCommitted] = useState(value);
  // The URL changed from elsewhere (back/forward, another filter): show its value
  if (value !== committed) {
    setCommitted(value);
    setDraft(value);
  }
  return (
    <label className="admin-audit-date">
      {label}
      <input
        type="date"
        className="admin-input"
        value={draft}
        min={min || undefined}
        max={max || undefined}
        onChange={(event) => {
          const day = event.target.value;
          setDraft(day);
          if (day === "" || isValidDay(day)) {
            setCommitted(day);
            onCommit(day);
          }
        }}
      />
    </label>
  );
};

interface AuditFiltersProps {
  search: AuditSearch;
  /** Changes some filters (the page goes back to 1). replace: no new history entry */
  onChange: (patch: Partial<AuditSearch>, options?: { replace?: boolean }) => void;
  token: string;
  /** The user picker searches GET /admin/users: only with the Usuarios area */
  canPickUser: boolean;
}

/** Spec §5.2: period, action category and who acted; a specific resource only arrives from links (chip). */
export const AuditFilters = ({ search, onChange, token, canPickUser }: AuditFiltersProps) => {
  const hasResource = !!(search.resourceType || search.resourceId);
  return (
    <>
      <div className="admin-audit-filters">
        <select
          className="admin-input"
          aria-label="Periodo"
          value={search.range}
          onChange={(event) => onChange({ range: event.target.value as RangeId })}
        >
          {RANGES.map((range) => (
            <option key={range.id} value={range.id}>
              {range.label}
            </option>
          ))}
        </select>
        {search.range === "custom" && (
          <>
            <DayInput
              label="Desde"
              value={search.since}
              max={search.until}
              onCommit={(since) => onChange({ since }, { replace: true })}
            />
            <DayInput
              label="Hasta"
              value={search.until}
              min={search.since}
              onCommit={(until) => onChange({ until }, { replace: true })}
            />
          </>
        )}
        <select
          className="admin-input"
          aria-label="Categoría de acción"
          value={search.category}
          onChange={(event) => onChange({ category: event.target.value as CategoryId | "" })}
        >
          <option value="">Todas las acciones</option>
          {CATEGORIES.map((category) => (
            <option key={category.id} value={category.id}>
              {category.label}
            </option>
          ))}
        </select>
        {canPickUser && !search.userId && (
          <UserPicker token={token} onPick={(user) => onChange({ userId: user.id, userLabel: displayName(user) })} />
        )}
      </div>

      {(search.userId || hasResource) && (
        <div className="admin-audit-filters">
          {search.userId && (
            <FilterChip
              label={`Usuario: ${search.userLabel || search.userId}`}
              onRemove={() => onChange({ userId: "", userLabel: "" })}
            />
          )}
          {hasResource && (
            <FilterChip
              label={`Recurso: ${resourceTypeLabel(search.resourceType || null)}${search.resourceId ? ` · ${search.resourceId}` : ""}`}
              onRemove={() => onChange({ resourceType: "", resourceId: "" })}
            />
          )}
        </div>
      )}

      {customRangeInvalid(search) && (
        <p className="admin-msg admin-msg--warn">La fecha «Desde» es posterior a «Hasta»: no habrá resultados.</p>
      )}
    </>
  );
};
