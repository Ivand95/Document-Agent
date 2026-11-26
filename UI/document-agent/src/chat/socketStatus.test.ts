import { describe, expect, it } from "vitest";
import { socketClosedNotice, WS_STATUS_LABELS } from "./socketStatus";

describe("socketClosedNotice", () => {
  it("adds nothing when the server already sent an error frame", () => {
    expect(socketClosedNotice({ ready: false, explained: true, code: 1008 })).toBeNull();
    expect(socketClosedNotice({ ready: false, explained: true, code: 1011 })).toBeNull();
  });

  it("adds nothing on a normal close", () => {
    expect(socketClosedNotice({ ready: true, explained: false, code: 1000 })).toBeNull();
  });

  it("explains a connection that never became ready", () => {
    expect(socketClosedNotice({ ready: false, explained: false, code: 1006 })).toMatch(/No se pudo conectar/);
  });

  it("explains a connection lost mid-conversation", () => {
    expect(socketClosedNotice({ ready: true, explained: false, code: 1006 })).toMatch(/Se perdió la conexión/);
  });
});

describe("WS_STATUS_LABELS", () => {
  it("never shows 'Conectando…' for a closed socket", () => {
    expect(WS_STATUS_LABELS.closed).toBe("Desconectado");
    expect(WS_STATUS_LABELS.connecting).toBe("Conectando…");
  });
});
