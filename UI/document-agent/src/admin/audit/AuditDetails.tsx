import { detailsView } from "./auditLabels";

/** An entry's details: a key-value list when flat, JSON when nested. */
export const AuditDetails = ({ details }: { details: Record<string, unknown> }) => {
  const view = detailsView(details);
  if (view.kind === "empty") return <p className="admin-muted">Sin detalles.</p>;
  if (view.kind === "json") return <pre className="admin-audit-json">{view.text}</pre>;
  return (
    <dl className="admin-audit-details">
      {view.pairs.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
};
