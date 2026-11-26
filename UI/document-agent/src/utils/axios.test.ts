import { afterEach, describe, expect, it, vi } from "vitest";
import { audioAgentWebSocket, documentAgentWebSocket, wsAuthFrame } from "./axios";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("chat sockets", () => {
  it("connect without the token in the URL", () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "WebSocket",
      class {
        constructor(url: string) {
          urls.push(url);
        }
      },
    );
    documentAgentWebSocket();
    audioAgentWebSocket();
    expect(urls).toHaveLength(2);
    expect(urls[0]).toMatch(/\/ws\/chat$/);
    expect(urls[1]).toMatch(/\/ws\/chat\/audio$/);
  });

  it("authenticate with a first frame", () => {
    expect(JSON.parse(wsAuthFrame("jwt"))).toEqual({ type: "auth", token: "jwt" });
  });
});
