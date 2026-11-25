import axios, { isAxiosError } from "axios";
import { notifyPermissionsStale } from "./permissionEvents";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL as string;

if (!BACKEND_URL) {
  console.error(
    "VITE_BACKEND_URL is not defined. Set it as a build arg when building the frontend image."
  );
}

const WS_URL = (BACKEND_URL ?? "").replace(/^http/, "ws");

export const http = axios.create({
  baseURL: BACKEND_URL,
});

// A 403 means the user's permissions changed (or the UI showed something it shouldn't): reload them
http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (isAxiosError(error) && error.response?.status === 403) notifyPermissionsStale();
    return Promise.reject(error);
  },
);

export const bearer = (token: string) => ({ headers: { Authorization: `Bearer ${token}` } });

export const login = () => http.get("/login");

/** Exchanges a still-valid token for a new one (same session, never past its absolute limit). */
export const refreshSession = (token: string) =>
  http.post<{ access_token: string }>("/auth/refresh", null, bearer(token));

/** Revokes the token on the server. */
export const logoutSession = (token: string) =>
  http.post("/auth/logout", null, bearer(token));

export const documentAgentWebSocket = (token: string) => {
  return new WebSocket(`${WS_URL}/ws/chat?token=${token}`);
};

export const audioAgentWebSocket = (token: string) => {
  return new WebSocket(`${WS_URL}/ws/chat/audio?token=${token}`);
};

// ---- Permissions (RBAC). The UI only uses them to choose what to show; the API enforces them ----

export interface MyPermissions {
  user_id: string;
  perm_version: number;
  roles: string[];
  agents: string[];
  admin_areas: string[];
  doc_categories: string[];
  capabilities: string[];
}

export const getMyPermissions = (token: string) =>
  http.get<MyPermissions>("/me/permissions", bearer(token));
