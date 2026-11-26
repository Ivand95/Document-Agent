import type { ProtectionReason, UserAction, UserStatus } from "../../utils/api/users";
import type { BadgeTone } from "../components/StatusBadge";

export const STATUS_LABELS: Record<UserStatus, string> = {
  active: "Activo",
  pending: "Pendiente",
  disabled: "Deshabilitado",
};

export const STATUS_TONES: Record<UserStatus, BadgeTone> = {
  active: "ok",
  pending: "warn",
  disabled: "muted",
};

export const ACTION_LABELS: Record<UserAction, string> = {
  approve: "Aprobar",
  hold: "Poner en espera",
  disable: "Deshabilitar",
};

/** The status each row action sets (same mapping as app/users/admin_scope.py ACTIONS). */
export const ACTION_STATUS: Record<UserAction, UserStatus> = {
  approve: "active",
  hold: "pending",
  disable: "disabled",
};

export const PROTECTED_LABELS: Record<ProtectionReason, string> = {
  self: "Tu cuenta",
  super_admin: "Super admin",
  admin: "Administrador",
};

export const displayName = (user: { name: string | null; email: string | null }) =>
  user.name?.trim() || user.email || "(sin nombre)";

/** Where a role comes from, as the API names it in `sources`. */
export const sourceLabel = (source: string): string => {
  if (source === "assigned") return "Asignado a mano";
  if (source === "everyone") return "Todos los usuarios";
  const [kind, ...rest] = source.split(":");
  const value = rest.join(":");
  if (kind === "department") return `Por departamento ${value}`;
  if (kind === "position_prefix") return `Por puesto ${value}`;
  if (kind === "legacy") return `Configuración antigua (${value})`;
  return source;
};
