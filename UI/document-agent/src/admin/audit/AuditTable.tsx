import ChevronRight from "@mui/icons-material/ChevronRight";
import ExpandMore from "@mui/icons-material/ExpandMore";
import { IconButton } from "@mui/material";
import { useId, useState } from "react";
import { Link } from "react-router";
import type { AuditEntry } from "../../utils/api/audit";
import type { AdminArea } from "../areas";
import { DataTable, type Column } from "../components/DataTable";
import { RelativeTime } from "../components/RelativeTime";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../format";
import { AuditDetails } from "./AuditDetails";
import {
  actionLabel,
  actorLabel,
  isHighlighted,
  outcomeLabel,
  outcomeTone,
  resourceLink,
  resourceTypeLabel,
} from "./auditLabels";
import { AUDIT_PAGE_SIZE } from "./auditSearch";
import "./audit.css";

interface AuditTableProps {
  /** null: first load in progress (or failed) */
  entries: AuditEntry[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  page: number;
  onPageChange: (page: number) => void;
  /** The admin's visible areas: links to users (and later roles…) only where they can open them */
  openAreas: readonly AdminArea[];
  emptyText: string;
}

const ResourceCell = ({ entry, openAreas }: { entry: AuditEntry; openAreas: readonly AdminArea[] }) => {
  if (!entry.resource_type && !entry.resource_id) return <span>—</span>;
  const link = resourceLink(entry.resource_type, entry.resource_id, openAreas);
  return (
    <div className="admin-audit-cell">
      <span>{resourceTypeLabel(entry.resource_type)}</span>
      {entry.resource_id &&
        (link ? (
          <Link to={link} className="admin-link">
            <small>{entry.resource_id}</small>
          </Link>
        ) : (
          <small>{entry.resource_id}</small>
        ))}
    </div>
  );
};

/** Spec §5.1: the audit log, newest first, 100 per page, with the details unfolding under each row. */
export const AuditTable = ({
  entries,
  loading,
  error,
  onRetry,
  page,
  onPageChange,
  openAreas,
  emptyText,
}: AuditTableProps) => {
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());
  const idPrefix = useId();
  const detailsId = (entry: AuditEntry) => `${idPrefix}-details-${entry.id}`;
  const toggle = (id: number) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const columns: Column<AuditEntry>[] = [
    {
      key: "toggle",
      header: "",
      className: "admin-col-toggle",
      render: (entry) => {
        const open = expanded.has(entry.id);
        return (
          <IconButton
            size="small"
            aria-expanded={open}
            aria-controls={open ? detailsId(entry) : undefined}
            aria-label={`${open ? "Ocultar" : "Ver"} detalles: ${actionLabel(entry.action)}, ${formatDateTime(entry.at)}`}
            onClick={() => toggle(entry.id)}
            sx={{ color: "var(--chat-text)" }}
          >
            {open ? <ExpandMore fontSize="small" /> : <ChevronRight fontSize="small" />}
          </IconButton>
        );
      },
    },
    { key: "at", header: "Fecha", render: (entry) => <RelativeTime iso={entry.at} /> },
    {
      key: "actor",
      header: "Quién",
      render: (entry) => {
        const link = resourceLink("user", entry.user_id, openAreas);
        const label = actorLabel(entry);
        return link ? (
          <Link to={link} className="admin-link">
            {label}
          </Link>
        ) : (
          <span>{label}</span>
        );
      },
    },
    { key: "action", header: "Acción", render: (entry) => <span title={entry.action}>{actionLabel(entry.action)}</span> },
    { key: "resource", header: "Recurso", render: (entry) => <ResourceCell entry={entry} openAreas={openAreas} /> },
    {
      key: "outcome",
      header: "Resultado",
      render: (entry) => <StatusBadge tone={outcomeTone(entry.outcome)}>{outcomeLabel(entry.outcome)}</StatusBadge>,
    },
  ];

  return (
    <DataTable
      caption="Registro de auditoría"
      columns={columns}
      rows={entries}
      rowKey={(entry) => String(entry.id)}
      loading={loading && entries !== null}
      error={error}
      onRetry={onRetry}
      emptyText={emptyText}
      rowClassName={(entry) => (isHighlighted(entry) ? "admin-row--alert" : undefined)}
      expandedRow={(entry) =>
        expanded.has(entry.id) ? (
          <div id={detailsId(entry)}>
            <AuditDetails details={entry.details} />
          </div>
        ) : null
      }
      pagination={
        entries
          ? {
              offset: (page - 1) * AUDIT_PAGE_SIZE,
              limit: AUDIT_PAGE_SIZE,
              // No total: a full page means there may be more (spec §5.2)
              hasMore: entries.length === AUDIT_PAGE_SIZE,
              onChange: (offset) => onPageChange(offset / AUDIT_PAGE_SIZE + 1),
            }
          : undefined
      }
    />
  );
};
