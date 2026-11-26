export const formatDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("es-DO", { dateStyle: "short", timeStyle: "short" }) : "—";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "hace 5 min", "hace 3 h", "ayer", "hace 3 días", or the date after a week. */
export const formatRelative = (iso: string | null | undefined, now: number = Date.now()): string => {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, now - then);
  if (diff < MINUTE) return "hace un momento";
  if (diff < HOUR) return `hace ${Math.floor(diff / MINUTE)} min`;
  if (diff < DAY) return `hace ${Math.floor(diff / HOUR)} h`;
  if (diff < 2 * DAY) return "ayer";
  if (diff < 7 * DAY) return `hace ${Math.floor(diff / DAY)} días`;
  return new Date(iso).toLocaleDateString("es-DO", { dateStyle: "short" });
};

/** A <input type="datetime-local"> value (the browser's local time) as an ISO instant with time zone,
 * which is what the API requires for expires_at. null when empty or invalid. */
export const localInputToIso = (value: string): string | null => {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
};

const pad = (n: number) => String(n).padStart(2, "0");

/** The inverse of localInputToIso: an ISO instant as a <input type="datetime-local"> value in the browser's
 * local time ("YYYY-MM-DDTHH:mm"). "" when null or invalid. */
export const isoToLocalInput = (iso: string | null): string => {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
};
