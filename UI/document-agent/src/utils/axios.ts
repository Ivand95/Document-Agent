import axios from "axios";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL as string;

if (!BACKEND_URL) {
  console.error(
    "VITE_BACKEND_URL is not defined. Set it as a build arg when building the frontend image."
  );
}

const WS_URL = (BACKEND_URL ?? "").replace(/^http/, "ws");

const http = axios.create({
  baseURL: BACKEND_URL,
});

export const login = () => http.get("/login");

const bearer = (token: string) => ({ headers: { Authorization: `Bearer ${token}` } });

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
