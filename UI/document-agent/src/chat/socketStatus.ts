// Connection state of the chat WebSocket: usable only once the server answers {"type": "ready"}
export type WsStatus = "connecting" | "online" | "closed";

export const WS_STATUS_LABELS: Record<WsStatus, string> = {
  connecting: "Conectando…",
  online: "En línea",
  closed: "Desconectado",
};

const NORMAL_CLOSE = 1000;

/**
 * What to tell the user when the socket closes, or null when there is nothing to add: a normal close,
 * or the server already explained it with an error frame right before closing.
 */
export const socketClosedNotice = ({
  ready,
  explained,
  code,
}: {
  ready: boolean;
  explained: boolean;
  code: number;
}): string | null => {
  if (explained || code === NORMAL_CLOSE) return null;
  return ready
    ? "Se perdió la conexión con el asistente. Recarga la página para continuar."
    : "No se pudo conectar con el asistente. Recarga la página para intentarlo de nuevo.";
};
