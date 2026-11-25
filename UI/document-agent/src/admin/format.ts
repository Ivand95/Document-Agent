export const formatDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("es-DO", { dateStyle: "short", timeStyle: "short" }) : "—";
