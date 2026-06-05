import axios from "axios";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL as string;

const http = axios.create({
  baseURL: BACKEND_URL,
});

export const login = () => http.get("/login");

export const documentAgentWebSocket = (token: string) => {
  return new WebSocket(`${BACKEND_URL}/ws/chat?token=${token}`);
};

export const audioAgentWebSocket = (token: string) => {
  return new WebSocket(`${BACKEND_URL}/ws/chat/audio?token=${token}`);
};
