import type { ReactNode } from "react";
import "./components.css";

export type BadgeTone = "ok" | "warn" | "error" | "muted";

export const StatusBadge = ({ tone, children }: { tone: BadgeTone; children: ReactNode }) => (
  <span className={`admin-status admin-status--${tone}`}>{children}</span>
);
