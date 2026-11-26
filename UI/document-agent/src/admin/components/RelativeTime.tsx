import { formatDateTime, formatRelative } from "../format";

export const RelativeTime = ({ iso }: { iso: string | null | undefined }) =>
  iso ? (
    <time dateTime={iso} title={formatDateTime(iso)}>
      {formatRelative(iso)}
    </time>
  ) : (
    <span>—</span>
  );
