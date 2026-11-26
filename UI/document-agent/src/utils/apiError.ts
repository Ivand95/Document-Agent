import { isAxiosError } from "axios";

export const GENERIC_ERROR = "Ha ocurrido un error inesperado. Inténtalo de nuevo.";
export const NETWORK_ERROR = "No se pudo conectar con el servidor.";

interface ValidationItem {
  loc?: (string | number)[];
  msg?: string;
}

/** The text to show for a failed API call: the backend's `detail` (already in Spanish), FastAPI's
 * validation errors joined with their field, or a generic message. */
export function apiErrorMessage(error: unknown): string {
  if (!isAxiosError(error)) return GENERIC_ERROR;
  if (!error.response) return NETWORK_ERROR;
  const detail = (error.response.data as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail.trim() ? detail : GENERIC_ERROR;
  if (Array.isArray(detail)) {
    const parts = (detail as ValidationItem[])
      .map((item) => {
        const field = (item.loc ?? []).filter((part) => part !== "body").join(".");
        const msg = item.msg ?? "";
        return field ? `${field}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return GENERIC_ERROR;
}
